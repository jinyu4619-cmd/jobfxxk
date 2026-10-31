#!/usr/bin/env node
/**
 * fetch_jd.cjs —— 后台读 JD，不在用户屏幕上开页面
 *
 * 用法: node scripts/fetch_jd.cjs <岗位链接>
 *
 * 做法：
 *   1. 无头浏览器打开链接
 *   2. 北森系（*.zhiye.com）优先走它自己的岗位列表接口，能拿到结构化的职责/要求
 *   3. 其他系统退回读页面正文，用「岗位职责/任职要求」这类小标题切段
 *   4. 输出 JSON：{ url, system, title, duty, require, ok, note }
 *
 * 读不到就明说读不到，不凭岗位名猜职责。
 */
const { chromium } = require('playwright');

const URL_ARG = process.argv[2];
if (!URL_ARG) {
  console.error('用法: node scripts/fetch_jd.cjs <岗位链接>');
  process.exit(1);
}

const SECTIONS = {
  duty: ['岗位职责', '工作职责', '职位描述', '工作内容', '岗位内容', '你将负责', '我们需要你'],
  require: ['任职要求', '岗位要求', '职位要求', '任职资格', '我们希望你', '任职条件'],
};
const STOP = ['公司介绍', '关于我们', '福利待遇', '投递方式', '联系方式', '校招流程', '招聘流程'];

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

(async () => {
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await (await b.newContext()).newPage();

  // 顺手记录页面上出现过的 JSON 接口，方便判断该用哪个
  const apis = new Set();
  page.on('response', (res) => {
    const u = res.url();
    if (/\/api\//.test(u) && !/\.(js|css|png|jpg)/.test(u)) apis.add(u.split('?')[0]);
  });

  let text = '';
  try {
    await page.goto(URL_ARG, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(4500);
    text = await page.evaluate(() => document.body.innerText);
  } catch (e) {
    console.log(JSON.stringify({ ok: false, note: '页面打不开：' + String(e).slice(0, 80) }, null, 1));
    await b.close();
    process.exit(2);
  }

  const title = await page.title();
  const host = new URL(URL_ARG).hostname;
  let system = 'unknown';
  if (/zhiye\.com/.test(host)) system = '北森';
  else if (/feishu\.cn|jobs\.feishu/.test(host)) system = '飞书招聘';
  else if (/mokahr\.com/.test(host)) system = 'Moka';
  else if (/workday/.test(host)) system = 'Workday';

  const flat = text.replace(/\n{2,}/g, '\n');
  let duty = cut(flat, SECTIONS.duty, STOP);
  let require = cut(flat, SECTIONS.require, STOP);
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
      }, URL_ARG);

      // 优先按 URL 里的 jobAdId 精确匹配，否则按页面标题里的岗位名匹配
      const idm = URL_ARG.match(/jobAdId=([0-9a-f-]{20,})/i);
      let hit = null;
      if (idm) hit = items.find(x => String(x.JobAdId || '').toLowerCase() === idm[1].toLowerCase());
      if (!hit) {
        hit = items.find(x => title.includes((x.JobAdName || '').slice(0, 8)));
      }
      if (!hit && items.length === 1) hit = items[0];

      if (hit) {
        const strip = s => (s || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
        duty = strip(hit.Duty).slice(0, 900);
        require = strip(hit.Require).slice(0, 900);
        note = '来自北森接口，岗位：' + (hit.JobAdName || '');
      } else {
        note = `北森接口返回 ${items.length} 个岗位，但没匹配到这一个，请直接把岗位详情页链接给我`;
      }
    } catch (e) {
      note = '北森接口调用失败：' + String(e).slice(0, 60);
    }
  }

  const out = {
    ok: !!(duty || require),
    url: URL_ARG,
    system,
    title: title.slice(0, 60),
    duty,
    require,
    note: duty || require ? note : (note || '没找到职责/要求段落，需要岗位详情页链接或登录'),
    apis: [...apis].slice(0, 8),
  };

  console.log(JSON.stringify(out, null, 1));
  await b.close();
  process.exit(out.ok ? 0 : 3);
})();
