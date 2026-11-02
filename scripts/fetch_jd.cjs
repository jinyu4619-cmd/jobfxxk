#!/usr/bin/env node
/**
 * fetch_jd.cjs —— 后台读 JD，不在用户屏幕上开页面
 *
 * 用法:
 *   node scripts/fetch_jd.cjs <岗位链接>                        # 读一条
 *   node scripts/fetch_jd.cjs <链接1> <链接2> <链接3>           # 读多条（并发）
 *   node scripts/fetch_jd.cjs --file=jobs.json                  # 从文件里读一批
 *   node scripts/fetch_jd.cjs <链接> --refresh                  # 忽略缓存重读
 *   node scripts/fetch_jd.cjs <链接> --max-age=72               # 缓存多少小时内算新鲜（默认 168）
 *
 * 为什么加缓存：一条 JD 读完就丢，下次同一个岗位还要再开一次浏览器。
 * 实测单条 6–10 秒，其中两三秒纯粹是浏览器启动。缓存之后第二轮几乎零成本，
 * 攒下来的 JD 还能反哺筛选——岗位库那张表没有「专业要求」这一列，
 * 而这恰恰是判断该不该投的关键字段。
 *
 * 为什么加并发：三条 JD 串行要 20–30 秒，一个浏览器里并发读三条只要十几秒。
 *
 * 做法：
 *   1. 无头浏览器打开链接
 *   2. 北森系（*.zhiye.com）优先走它自己的岗位列表接口，能拿到结构化的职责/要求
 *   3. 其他系统退回读页面正文，用「岗位职责/任职要求」这类小标题切段
 *   4. 输出 JSON：{ url, system, title, duty, require, ok, note, cached }
 *
 * 读不到就明说读不到，不凭岗位名猜职责。
 */
require('./ensure_deps.cjs');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { launch } = require('./browser_config.cjs');
const { dataDir } = require('./config.cjs');

const SECTIONS = {
  duty: ['岗位职责', '工作职责', '职位描述', '工作内容', '岗位内容', '你将负责', '我们需要你'],
  require: ['任职要求', '岗位要求', '职位要求', '任职资格', '我们希望你', '任职条件'],
};
const STOP = ['公司介绍', '关于我们', '福利待遇', '投递方式', '联系方式', '校招流程', '招聘流程'];

// ---------- 参数 ----------
const argv = process.argv.slice(2);
const flag = (name, def) => {
  const hit = argv.find(a => a.indexOf('--' + name + '=') === 0);
  return hit ? hit.slice(name.length + 3) : def;
};
const REFRESH = argv.includes('--refresh');
const MAX_AGE_H = parseFloat(flag('max-age', '168'));
const CONCURRENCY = Math.max(1, Math.min(6, parseInt(flag('concurrency', '3'), 10) || 3));

let urls = argv.filter(a => a && a.indexOf('--') !== 0);
const fileArg = flag('file', '');
if (fileArg) {
  try {
    const list = JSON.parse(fs.readFileSync(fileArg, 'utf8'));
    (Array.isArray(list) ? list : [list]).forEach(x => {
      const u = typeof x === 'string' ? x : (x && (x.url || x.link));
      if (u) urls.push(u);
    });
  } catch (e) {
    console.error('读不了 --file 指向的文件：' + e.message);
    process.exit(1);
  }
}
urls = [...new Set(urls.filter(Boolean))];
if (!urls.length) {
  console.error('用法: node scripts/fetch_jd.cjs <岗位链接> [<链接2> ...] [--file=jobs.json] [--refresh]');
  process.exit(1);
}

// ---------- 缓存 ----------
const CACHE = path.join(dataDir(), 'data', 'jd_cache.json');

function loadCache() {
  try { return JSON.parse(fs.readFileSync(CACHE, 'utf8').replace(/^﻿/, '')); }
  catch (e) { return {}; }
}

function saveCache(cache) {
  try {
    fs.mkdirSync(path.dirname(CACHE), { recursive: true });
    fs.writeFileSync(CACHE, JSON.stringify(cache, null, 1), 'utf8');
  } catch (e) { /* 缓存写不进去不影响主流程 */ }
}

function fresh(hit) {
  if (!hit || !hit.fetched_at) return false;
  if (!hit.ok) return false;                       // 上次没读到，下次重试
  const ageH = (Date.now() - Date.parse(hit.fetched_at)) / 3600000;
  return ageH <= MAX_AGE_H;
}

// ---------- 单条解析 ----------
function cut(text, keys, stops) {
  for (const k of keys) {
    const i = text.indexOf(k);
    if (i < 0) continue;
    let seg = text.slice(i + k.length, i + k.length + 900);
    for (const s of stops) {
      const j = seg.indexOf(s);
      if (j > 20) seg = seg.slice(0, j);
    }
    seg = seg.replace(/\s+/g, ' ').trim();
    if (seg.length > 20) return seg;
  }
  return '';
}

async function fetchOne(browser, url) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const apis = new Set();
  page.on('response', (res) => {
    const u = res.url();
    if (/\/api\//.test(u) && !/\.(js|css|png|jpg)/.test(u)) apis.add(u.split('?')[0]);
  });

  let text = '';
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    await page.waitForFunction(
      () => /岗位职责|工作职责|职位描述|任职要求|任职资格|岗位要求/.test(document.body.innerText),
      { timeout: 4000 }
    ).catch(() => {});
    text = await page.evaluate(() => document.body.innerText);
  } catch (e) {
    await ctx.close().catch(() => {});
    return { ok: false, url, note: '页面打不开：' + String(e).slice(0, 80) };
  }

  const title = await page.title();
  let host = '';
  try { host = new URL(url).hostname; } catch (e) { host = ''; }
  let system = 'unknown';
  if (/zhiye\.com/.test(host)) system = '北森';
  else if (/feishu\.cn|jobs\.feishu/.test(host)) system = '飞书招聘';
  else if (/mokahr\.com/.test(host)) system = 'Moka';
  else if (/workday/.test(host)) system = 'Workday';

  const flat = text.replace(/\n{2,}/g, '\n');
  let duty = cut(flat, SECTIONS.duty, STOP);
  let require_ = cut(flat, SECTIONS.require, STOP);
  let note = '';

  // 北森系：直接打它自己的岗位列表接口，能拿到结构化的 Duty / Require
  if (system === '北森' && !duty) {
    try {
      const items = await page.evaluate(async (u) => {
        const origin = new URL(u).origin;
        const r = await fetch(origin + '/api/Jobad/GetJobAdPageList', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pageIndex: 1, pageSize: 60, categoryId: 2, langType: 'zh_CN', keyword: '' }),
        });
        const j = await r.json();
        const find = (o) => {
          if (Array.isArray(o) && o.length && typeof o[0] === 'object' && o[0].JobAdName) return o;
          if (o && typeof o === 'object') {
            for (const k of Object.keys(o)) { const f = find(o[k]); if (f) return f; }
          }
          return null;
        };
        return find(j) || [];
      }, url);

      const idm = url.match(/jobAdId=([0-9a-f-]{20,})/i);
      let hit = null;
      if (idm) hit = items.find(x => String(x.JobAdId || '').toLowerCase() === idm[1].toLowerCase());
      if (!hit) hit = items.find(x => title.includes((x.JobAdName || '').slice(0, 8)));
      if (!hit && items.length === 1) hit = items[0];

      if (hit) {
        const strip = s => (s || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
        duty = strip(hit.Duty).slice(0, 900);
        require_ = strip(hit.Require).slice(0, 900);
        note = '来自北森接口，岗位：' + (hit.JobAdName || '');
      } else {
        note = `北森接口返回 ${items.length} 个岗位，但没匹配到这一个，请直接把岗位详情页链接给我`;
      }
    } catch (e) {
      note = '北森接口调用失败：' + String(e).slice(0, 60);
    }
  }

  await ctx.close().catch(() => {});
  return {
    ok: !!(duty || require_),
    url,
    system,
    title: title.slice(0, 60),
    duty,
    require: require_,
    note: duty || require_ ? note : (note || '没找到职责/要求段落，需要岗位详情页链接或登录'),
    apis: [...apis].slice(0, 8),
  };
}

// ---------- 主流程 ----------
(async () => {
  const t0 = Date.now();
  const cache = loadCache();
  const todo = [];
  const results = {};

  for (const u of urls) {
    const hit = cache[u];
    if (!REFRESH && fresh(hit)) {
      results[u] = Object.assign({}, hit, { cached: true });
    } else {
      todo.push(u);
    }
  }
  const cachedCount = urls.length - todo.length;

  let browser = null;
  if (todo.length) {
    try {
      browser = await launch(chromium, { headless: true });
    } catch (e) {
      console.log(JSON.stringify({ ok: false, note: String(e.message || e) }, null, 1));
      process.exit(2);
    }

    // 简单的并发池：一个浏览器，多个上下文，最多 CONCURRENCY 个同时在跑
    let cursor = 0;
    const workers = Array.from({ length: Math.min(CONCURRENCY, todo.length) }, async () => {
      while (cursor < todo.length) {
        const u = todo[cursor++];
        const r = await fetchOne(browser, u);
        r.cached = false;
        r.fetched_at = new Date().toISOString();
        results[u] = r;
        cache[u] = r;
        saveCache(cache);           // 每读一条就落盘，中途中断也不白跑
      }
    });
    await Promise.all(workers);
    await browser.close().catch(() => {});
  }

  const list = urls.map(u => results[u] || { ok: false, url: u, note: '没读到' });
  const elapsed = ((Date.now() - t0) / 1000).toFixed(1);

  if (urls.length === 1) {
    console.log(JSON.stringify(list[0], null, 1));
  } else {
    console.log(JSON.stringify({
      ok: list.every(x => x.ok),
      count: list.length,
      cached: cachedCount,
      fetched: todo.length,
      elapsed_s: parseFloat(elapsed),
      items: list,
    }, null, 1));
  }
  process.exit(list.every(x => x.ok) ? 0 : 3);
})();
