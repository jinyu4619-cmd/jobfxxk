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
 *   node scripts/apply.cjs <链接> --resume "xx.pdf" --fill fill-plan.json
 *   node scripts/apply.cjs <链接> --page=campus/detail --login-timeout=180
 *   node scripts/apply.cjs <链接> --json          # 只要机器可读结果
 *   node scripts/apply.cjs <链接> --no-state      # 试跑用：不写 state.json
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
const FILL_PATH = arg('fill', '');
const EXPECT = arg('expect', '');
const COMPANY = arg('company', '');
const LOGIN_TIMEOUT = parseInt(arg('login-timeout', '180'), 10) * 1000;
const AS_JSON = process.argv.includes('--json');
const ALLOW_MASTER = process.argv.includes('--allow-master');
// 试跑时用：不要把测试数据写进真实的 state.json（踩过一次，把用户进度覆盖了）
const NO_STATE = process.argv.includes('--no-state');

function say(...a) { if (!AS_JSON) console.log(...a); }
function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  })]).finally(() => clearTimeout(timer));
}

function readJsonFile(file) {
  const bytes = fs.readFileSync(file);
  let text;
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    text = new TextDecoder('utf-16le').decode(bytes.subarray(2));
  } else if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    text = new TextDecoder('utf-16be').decode(bytes.subarray(2));
  } else {
    text = bytes.toString('utf8').replace(/^\uFEFF/, '');
  }
  try { return JSON.parse(text); }
  catch (utf8Error) {
    // PowerShell may save redirected output as UTF-16LE without a byte-order mark.
    if (!(bytes.length >= 2 && bytes[0] === 0x7b && bytes[1] === 0x00)) throw utf8Error;
    return JSON.parse(new TextDecoder('utf-16le').decode(bytes));
  }
}

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

/** 提前退出也要留痕。
 *  以前只有「顺利走到表单」才写 state，遇到要登录、找不到投递入口、岗位对不上就
 *  什么都不写——下一个接手的人（或者换一个模型）完全不知道这次试过、卡在哪儿。
 *  2026-11-03 上午五家公司一条都没进 state，就是这么来的。 */
function bail(code, phase) {
  if (!NO_STATE) saveState({ company: COMPANY, position: EXPECT, phase: phase });
  process.exit(code);
}

/** 落一次进度，断了能接上 */
function saveState(info) {
  try {
    const dataDir = require(path.join(REPO, 'scripts', 'config.cjs')).dataDir();
    const p = path.join(dataDir, 'user', 'state.json');
    let d = {};
    try { d = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) {}
    d.phase = info.phase || '阶段 8 · 表单已就绪，待填/待核';
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

/** 等页面「安静」：只观察短期变化，快速完成时不再固定空等。 */
async function settled(page, maxMs) {
  const t0 = Date.now();
  let last = '', same = 0;
  while (Date.now() - t0 < maxMs) {
    const sig = await withTimeout(page.evaluate(() =>
      document.querySelectorAll('input,textarea,select').length + ':' +
      (document.body.innerText || '').length).catch(() => ''), 2500, '页面读取超时').catch(() => '');
    if (sig && sig === last) {
      if (++same >= 2) return true;
    } else { same = 0; last = sig; }
    await page.waitForTimeout(180);
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
      const beforeUrl = page.url();
      await loc.nth(i).click({ timeout: 5000 }).catch(() => {});
      const deadline = Date.now() + 2500;
      while (Date.now() < deadline) {
        const pages = page.context().pages();
        if (pages.length > before) return pages[pages.length - 1];
        if (page.url() !== beforeUrl) return page;
        await page.waitForTimeout(120);
      }
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
  let inputHasFile = false;
  for (let i = 0; i < 32; i++) {
    await page.waitForTimeout(250);
    const state = await page.evaluate(x => ({
      shown: (document.body.innerText || '').indexOf(x) >= 0,
      inputHasFile: [...document.querySelectorAll('input[type=file]')].some(el =>
        el.files && [...el.files].some(f => f.name === x)),
    }), base).catch(() => ({ shown: false, inputHasFile: false }));
    shown = state.shown;
    inputHasFile = state.inputHasFile;
    if (shown || inputHasFile) break;
  }
  return { ok: shown || inputHasFile, shown, inputHasFile,
    why: shown || inputHasFile ? '' : '8 秒内没有确认文件已挂到页面' };
}

(async () => {
  if (!TARGET) { console.error('用法: node scripts/apply.cjs <岗位详情链接> [--resume 路径]'); process.exit(3); }

  let fillItems = null;
  if (FILL_PATH) {
    let plan;
    try { plan = readJsonFile(FILL_PATH); }
    catch (e) { throw new Error('--fill 文件读取失败：' + e.message); }
    fillItems = Array.isArray(plan) ? plan : (plan && plan.auto);
    if (!Array.isArray(fillItems) || fillItems.some(x => !x || typeof x.label !== 'string' ||
        typeof x.value !== 'string' || !x.label.trim())) {
      throw new Error('--fill 必须是自动填写数组，或包含有效 auto 数组的 JSON 文件');
    }
  }

  const t0 = Date.now();
  const timings = {};
  let mark = t0;
  let browser;
  try {
    browser = await withTimeout(chromium.connectOverCDP('http://127.0.0.1:9222'),
      6000, 'CDP 在 6 秒内没有响应');
  } catch (e) {
    console.error('自动化浏览器连接失败：' + e.message + '。停止本次操作，没有更换或接管其他浏览器。');
    process.exit(3);
  }
  timings.cdp_connect_ms = Date.now() - mark;
  mark = Date.now();
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();

  // 只复用用户明确指定的页面，或与目标链接完全对应的标签。
  // 不从所有打开的申请页里猜「填得最多」的一页，避免串到另一家公司。
  let page = PAGE_HINT ? pages.find(p => p.url().indexOf(PAGE_HINT) >= 0) : null;
  if (PAGE_HINT && !page) {
    console.error('没有标签页匹配 --page=' + PAGE_HINT + '；未操作其他申请页。');
    process.exit(2);
  }
  if (!page) page = pages.find(p => p.url() === TARGET || p.url().split('#')[0] === TARGET.split('#')[0]);
  if (!page) page = await ctx.newPage();
  page.setDefaultTimeout(5000);
  page.setDefaultNavigationTimeout(20000);
  await page.bringToFront();

  const alreadyOnForm = /\/apply/.test(page.url()) || /\/resume\/\d+/.test(page.url());
  if (!alreadyOnForm && page.url().indexOf(TARGET.split('?')[0]) < 0) {
    await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
  }
  await settled(page, 8000);
  timings.open_and_settle_ms = Date.now() - mark;
  let positionObserved = !EXPECT;
  if (EXPECT) {
    const initialText = await page.evaluate(() => document.body.innerText).catch(() => '');
    positionObserved = initialText.indexOf(EXPECT) >= 0;
  }
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
      await settled(page, 8000);
      continue;
    }

    const next = await clickText(page, APPLY_TEXTS);
    if (!next) { stage = 'no_apply_button'; break; }
    page = next;
    await settled(page, 8000);
  }

  if (stage !== 'form_ready') {
    const out = { stage, url: page.url(), elapsed_ms: Date.now() - t0 };
    console.log(AS_JSON ? JSON.stringify(out) : '没走到表单：' + JSON.stringify(out));
    bail(stage === 'need_login' ? 1 : 2,
         stage === 'need_login' ? '阶段 7 · 等用户登录' : '阶段 7 · 找不到投递入口');
  }

  const formText = await page.evaluate(() => document.body.innerText).catch(() => '');
  positionObserved = positionObserved || formText.indexOf(EXPECT) >= 0;
  if (EXPECT && !positionObserved) {
    const out = { stage: 'wrong_position', url: page.url(), expected: EXPECT,
      elapsed_ms: Date.now() - t0 };
    console.log(AS_JSON ? JSON.stringify(out) : '岗位核对不匹配，未上传简历或填写：' + JSON.stringify(out));
    bail(2, '阶段 8 · 岗位核对不匹配');
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
    mark = Date.now();
    up = await upload(page, RESUME);
    say('上传：' + (up.ok ? '✓ 页面已确认文件' : '✗ ' + up.why));
    await settled(page, 8000);           // 只等字段实际变化并短暂稳定
    timings.upload_and_parse_ms = Date.now() - mark;
  }

  // Optional one-call batch fill. The file is the reviewed `fill_plan.py` auto array;
  // it may be the array itself or the complete plan object. Manual controls stay untouched.
  let fillResults = [];
  if (FILL_PATH) {
    if (fillItems.length) fillResults = await page.evaluate(ops.fillExpression(fillItems));
  }

  mark = Date.now();
  const info = await page.evaluate(ops.dumpExpression());
  timings.audit_ms = Date.now() - mark;

  // 岗位对不上就是投错岗。申请页的 document.title 往往只是「投递简历」，
  // 所以用正文里出没出现目标岗位名来判断，比标题可靠。
  const title = await page.evaluate(() => {
    const h = [...document.querySelectorAll('h1,h2,h3')]
      .map(el => (el.innerText || '').replace(/\s+/g, ' ').trim())
      .filter(t => t && t.length < 30);
    return h.length ? h[0] : (document.title || '').trim();
  }).catch(() => '');
  const expectOk = positionObserved; // 在岗位详情页或最终表单页核对，并在上传前阻断。

  // 只认本次上传已观测到的文件证据，不用页面里任意一个文件名猜。
  const resumePresent = up.ok;

  const audit = info.audit || {};
  const fillOk = fillResults.every(x => x.ok);
  const ready = audit.required_empty_count === 0 && resumePresent && expectOk && fillOk;

  const saved = NO_STATE ? '' : saveState({
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
    fill_results: fillResults,
    fill_failed: fillResults.filter(x => !x.ok).map(x => x.label),
    resume_present: resumePresent,
    fields_total: info.rows.length,
    elapsed_ms: Date.now() - t0,
    timings_ms: timings,
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
    if (timings.open_and_settle_ms != null) {
      console.log('  耗时：打开/渲染 ' + (timings.open_and_settle_ms / 1000).toFixed(1) + 's' +
        (timings.upload_and_parse_ms != null ? '，上传/解析 ' +
          (timings.upload_and_parse_ms / 1000).toFixed(1) + 's' : '') +
        (timings.audit_ms != null ? '，体检 ' + (timings.audit_ms / 1000).toFixed(1) + 's' : ''));
    }
    console.log('  岗位：' + title + (EXPECT ? (expectOk ? '  ✓ 与目标一致' : '  ✗ 和目标「' + EXPECT + '」对不上') : ''));
    const a = audit;
    if (a.required_empty_count) {
      console.log('  必填还空着 ' + a.required_empty_count + ' 个：' + a.required_empty.join('、'));
    } else {
      console.log('  必填项都满了');
    }
    if (fillResults.some(x => !x.ok)) {
      console.log('  自动填写未确认：' + fillResults.filter(x => !x.ok)
        .map(x => x.label + '（' + x.why + '）').join('；'));
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
