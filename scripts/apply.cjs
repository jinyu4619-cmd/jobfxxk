#!/usr/bin/env node
/**
 * apply.cjs —— 一条命令从岗位链接走到「表单就绪」
 *
 * 为什么要有它：真实投递时，「打开详情 → 点投递 → 等登录 → 等表单渲染 →
 * 传简历 → 等解析 → 看空字段」原本是七八条命令、七八次进程启动和 CDP 连接，
 * 每次还要人来回判断。合并成一条之后，固定开销只付一次，中间的等待全改成
 * 条件轮询（等字段数稳定、等文件名出现），不再干等固定秒数。
 *
 * 用法：
 *   node scripts/apply.cjs <岗位详情链接>
 *   node scripts/apply.cjs <链接> --resume "D:/简历库/pdf/xx.pdf" --company "某公司" --expect "用户运营"
 *   node scripts/apply.cjs <链接> --page=campus/detail --login-timeout=180
 *   node scripts/apply.cjs <链接> --json          # 只要机器可读结果
 *
 * 它会拦三件事：
 *   · 传的是母版（没跑过微调的那一份）——除非加 --allow-master
 *   · 页面上的岗位跟 --expect 对不上（投错岗）
 *   · 必填还空着 / 简历没挂上 → ready_to_submit=false
 *
 * 它不做的事：不点提交、不点保存、不碰任何自定义下拉。到头来输出一张空字段
 * 体检表——必填空了几个、非必填空了几个，填不填由用户决定。
 *
 * 退出码：0 表单就绪且可提交 / 1 需要用户登录 / 2 找不到投递入口 /
 *         3 出错 / 4 传的是母版 / 5 表单就绪但还不能提交
 */
const path = require('path');
const fs = require('fs');
// 用脚本自身位置推导，别写死绝对路径——写死的话换台电脑就 require 不到了
const REPO = path.resolve(__dirname, '..');
require(path.join(REPO, 'scripts/ensure_deps.cjs'));
const { chromium } = require('playwright');
const ops = require(path.join(REPO, 'scripts/page_ops.cjs'));

const APPLY_TEXTS = ['立即投递', '投递', '立即申请', '申请职位', '我要投递'];
const arg = (name, def) => {
  // 两种写法都要认：--name=value 和 --name value。
  // 之前只认等号形式，`--resume "路径"` 会被静默忽略——母版拦截因此形同虚设。
  const i = process.argv.indexOf('--' + name);
  if (i >= 0 && i + 1 < process.argv.length && process.argv[i + 1].indexOf('--') !== 0) {
    return process.argv[i + 1];
  }
  const hit = process.argv.find(a => a.indexOf('--' + name + '=') === 0);
  return hit ? hit.slice(name.length + 3) : def;
};

const TARGET = process.argv[2];
const PAGE_HINT = arg('page', '');
const RESUME = arg('resume', '');
const EXPECT = arg('expect', '');
const COMPANY = arg('company', '');
const LOGIN_TIMEOUT = parseInt(arg('login-timeout', '180'), 10) * 1000;
const AS_JSON = process.argv.includes('--json');
const ALLOW_MASTER = process.argv.includes('--allow-master');

function say(...a) { if (!AS_JSON) console.log(...a); }

/** 配置：用来判断传的是不是母版。查找规则与 paths.py / config.cjs 完全一致 */
function cfg() {
  try { return require(path.join(REPO, 'scripts', 'config.cjs')).config(); } catch (e) { return {}; }
}

/** 传的简历是不是「母版」——没跑过微调的那一份

 * 为什么拦：真实事故是「没跑微调就直接传了母版」，简历和岗位对不上还投出去了。
 * 文件层面的闸门（gate 8）只看这一版存在，看不出你传的是哪一份。
 */
function isMaster(file) {
  const c = cfg();
  const norm = p => (p || '').replace(/\\/g, '/').toLowerCase();
  const f = norm(file);
  return [c.master_docx, c.master_pdf].filter(Boolean).some(m => norm(m) === f);
}

/** 落一次进度，断了能接上 */
function saveState(info) {
  try {
    const dataDir = require(path.join(REPO, 'scripts', 'config.cjs')).dataDir();
    const p = path.join(dataDir, 'user', 'state.json');
    let d = {};
    try { d = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) {}
    d.phase = '阶段 8 · 表单已就绪，待填/待核';
    if (info.company) d.company = info.company;
    d.position = info.position || d.position || '';
    // 这次没传简历就别把上次的版本名继续挂着，容易让人以为已经传过了
    d.resume_version = info.resume || '';
    d.todo = [].concat(
      (info.required_empty || []).map(x => '必填未填：' + x),
      (info.optional_empty || []).length ? ['非必填空 ' + info.optional_empty.length + ' 项，等用户定'] : [],
      ['填完主要区块让用户点「暂存」，否则换标签就没了']);
    // 用本地时间，和 state.py 写出来的格式保持一致
    const off = -new Date().getTimezoneOffset();
    const sign = off >= 0 ? '+' : '-';
    const pad = n => String(Math.floor(Math.abs(n))).padStart(2, '0');
    const t = new Date();
    d.updated_at = t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate()) +
      'T' + pad(t.getHours()) + ':' + pad(t.getMinutes()) + ':' + pad(t.getSeconds()) +
      sign + pad(off / 60) + ':' + pad(off % 60);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify(d, null, 1), 'utf8');
    return p;
  } catch (e) { return ''; }
}

/** 等页面「安静」：字段数和正文长度连续两次不变，最多等 maxMs */
async function settled(page, maxMs) {
  const t0 = Date.now();
  let last = '', same = 0;
  while (Date.now() - t0 < maxMs) {
    const sig = await page.evaluate(() =>
      document.querySelectorAll('input,textarea,select').length + ':' +
      (document.body.innerText || '').length).catch(() => '');
    if (sig && sig === last) {
      if (++same >= 2) return true;
    } else { same = 0; last = sig; }
    await page.waitForTimeout(400);
  }
  return false;
}

/** 点一个文字按钮；可能新开标签，返回操作后的页面 */
async function clickText(page, texts) {
  for (const t of texts) {
    const loc = page.getByText(t, { exact: true });
    const n = await loc.count().catch(() => 0);
    for (let i = 0; i < n; i++) {
      const box = await loc.nth(i).boundingBox().catch(() => null);
      if (!box || box.width < 2) continue;
      const before = page.context().pages().length;
      await loc.nth(i).click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(1200);
      const pages = page.context().pages();
      // 新开了标签就切过去
      if (pages.length > before) return pages[pages.length - 1];
      return page;
    }
  }
  return null;
}

async function upload(page, file) {
  const inputs = page.locator('input[type=file]');
  const n = await inputs.count();
  if (!n) return { ok: false, why: '这一页没有上传控件' };
  const isPhoto = /\.(jpe?g|png)$/i.test(file);
  const idx = isPhoto ? n - 1 : 0;
  await inputs.nth(idx).setInputFiles(file);
  const base = path.basename(file);
  let shown = false;
  for (let i = 0; i < 16; i++) {
    await page.waitForTimeout(500);
    shown = await page.evaluate(x => document.body.innerText.indexOf(x) >= 0, base).catch(() => false);
    if (shown && i >= 1) break;
  }
  return { ok: shown, why: shown ? '' : '等了 8 秒没在页面上看到文件名' };
}

(async () => {
  if (!TARGET) { console.error('用法: node scripts/apply.cjs <岗位详情链接> [--resume 路径]'); process.exit(3); }

  const t0 = Date.now();
  const browser = await chromium.connectOverCDP('http://127.0.0.1:9222');
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();

  // 选页面：优先复用已经打开的申请页，而且挑「内容最多的那一份」。
  // 为什么：表单内容在内存里，没点过「暂存」服务端草稿就是空的；同名标签可能
  // 并存一个空白的新草稿和一个填了一半的旧草稿，随便挑一个就可能挑到空的。
  const applyPages = pages.filter(p => /\/apply/.test(p.url()) || /\/resume\/\d+/.test(p.url()));
  let page = PAGE_HINT ? pages.find(p => p.url().indexOf(PAGE_HINT) >= 0) : null;
  if (!page && applyPages.length) {
    let best = null, bestScore = -1;
    for (const p of applyPages) {
      const score = await p.evaluate(() =>
        [...document.querySelectorAll('input,textarea')]
          .filter(el => el.value && el.value.trim()).length).catch(() => 0);
      if (score > bestScore) { bestScore = score; best = p; }
    }
    page = best;
    if (applyPages.length > 1) {
      say('发现 ' + applyPages.length + ' 个申请页，选了填得最多的那个（' + bestScore + ' 个有值字段）');
    } else {
      say('复用已打开的申请页（不重新点投递）');
    }
  }
  if (!page) page = pages.find(p => p.url().indexOf(TARGET.split('?')[0]) >= 0);
  if (!page) page = await ctx.newPage();
  await page.bringToFront();

  const alreadyOnForm = /\/apply/.test(page.url()) || /\/resume\/\d+/.test(page.url());
  if (!alreadyOnForm && page.url().indexOf(TARGET.split('?')[0]) < 0) {
    await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  }
  await settled(page, 12000);
  say('打开岗位页：' + (Date.now() - t0) + 'ms');

  // ---- 走到申请表单 ----
  let stage = 'unknown';
  for (let round = 0; round < 4; round++) {
    const url = page.url();
    if (url.indexOf('/apply') >= 0 || url.indexOf('/resume/') >= 0) { stage = 'form_ready'; break; }

    if (url.indexOf('/login') >= 0) {
      // 登录只能用户做，等到超时为止
      say('需要登录，等你操作（最多 ' + LOGIN_TIMEOUT / 1000 + ' 秒）…');
      const start = Date.now();
      while (Date.now() - start < LOGIN_TIMEOUT) {
        await page.waitForTimeout(2000);
        if (page.url().indexOf('/login') < 0) break;
      }
      if (page.url().indexOf('/login') >= 0) { stage = 'need_login'; break; }
      await settled(page, 12000);
      continue;
    }

    const next = await clickText(page, APPLY_TEXTS);
    if (!next) { stage = 'no_apply_button'; break; }
    page = next;
    await settled(page, 12000);
  }

  if (stage !== 'form_ready') {
    const out = { stage, url: page.url(), elapsed_ms: Date.now() - t0 };
    console.log(AS_JSON ? JSON.stringify(out) : '没走到表单：' + JSON.stringify(out));
    process.exit(stage === 'need_login' ? 1 : 2);
  }

  // ---- 上传简历（先传，解析会重置表单）----
  if (RESUME && isMaster(RESUME) && !ALLOW_MASTER) {
    console.error('✗ 你给的是母版（未经微调的那一份）：' + RESUME);
    console.error('  这一版没有针对本岗位改过，而版面/内容是按岗位派生的。');
    console.error('  先用 fill_engine 生成这一版，或者确认就要用母版再加 --allow-master。');
    process.exit(4);
  }

  let up = { ok: false, why: '没传' };
  if (RESUME) {
    up = await upload(page, RESUME);
    say('上传：' + (up.ok ? '✓ 页面上已出现文件名' : '✗ ' + up.why));
    await settled(page, 15000);          // 等解析把字段填完
  }

  const info = await page.evaluate(ops.dumpExpression());

  // 岗位对不上就是投错岗。申请页的 document.title 往往只是「投递简历」，
  // 所以用正文里出没出现目标岗位名来判断，比标题可靠。
  const pageText = await page.evaluate(() => document.body.innerText).catch(() => '');
  const title = await page.evaluate(() => {
    const h = [...document.querySelectorAll('h1,h2,h3')]
      .map(el => (el.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(t => t && t.length < 30);
    return h.length ? h[0] : (document.title || '').trim();
  }).catch(() => '');
  const expectOk = !EXPECT || pageText.indexOf(EXPECT) >= 0;

  // 简历挂没挂上：看页面上有没有文件名或「上次上传」这类痕迹
  const resumePresent = up.ok || await page.evaluate(() =>
    /\.(pdf|docx?|doc)\b/i.test(document.body.innerText) ||
    document.body.innerText.indexOf('上次上传') >= 0).catch(() => false);

  const audit = info.audit || {};
  const ready = audit.required_empty_count === 0 && resumePresent && expectOk;

  const saved = saveState({
    company: COMPANY, position: EXPECT || title,
    resume: RESUME ? path.basename(RESUME) : '',
    required_empty: audit.required_empty || [],
    optional_empty: audit.optional_empty || [],
  });

  const out = {
    stage: 'form_ready',
    url: info.url,
    position_title: title,
    expect_matched: expectOk,
    resume_uploaded: up.ok,
    resume_present: resumePresent,
    fields_total: info.rows.length,
    elapsed_ms: Date.now() - t0,
    ready_to_submit: ready,
    state_saved: saved,
    audit,
  };

  if (AS_JSON) {
    out.rows = info.rows;
    console.log(JSON.stringify(out, null, 1));
  } else {
    console.log('\n表单就绪：' + info.rows.length + ' 个字段，用时 ' +
                (out.elapsed_ms / 1000).toFixed(1) + 's');
    console.log('  岗位：' + title + (EXPECT ? (expectOk ? '  ✓ 与目标一致' : '  ✗ 和目标「' + EXPECT + '」对不上') : ''));
    const a = audit;
    if (a.required_empty_count) {
      console.log('  必填还空着 ' + a.required_empty_count + ' 个：' + a.required_empty.join('、'));
    } else {
      console.log('  必填项都满了');
    }
    if (a.optional_empty_count) {
      console.log('  非必填空着 ' + a.optional_empty_count + ' 个：' + a.optional_empty.join('、'));
      console.log('  （这些我没有代填——信息库里没有的就不编。填不填你定）');
    }
    if (!resumePresent) console.log('  ✗ 没检测到已上传的简历');
    console.log('  能否提交：' + (ready ? '✓ 三个条件都满足，等你按提交' : '✗ 还不满足，见上面'));
    if (saved) console.log('  进度已记录：' + saved);
  }
  process.exit(ready ? 0 : 5);
})().catch(e => { console.error('ERR', e.message); process.exit(3); });
