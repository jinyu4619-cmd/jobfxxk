#!/usr/bin/env node
/**
 * browser.cjs —— 网申表单的浏览器操作引擎
 *
 * 一条命令一个动作，模型按需调用，不用每次重写 Playwright 代码。
 *
 * 用法：
 *   node scripts/browser.cjs launch <url>      用 config 里选定的浏览器开窗口（带调试端口）
 *   node scripts/browser.cjs dump              导出当前页所有表单字段（字段名/类型/当前值/下拉选项）
 *   node scripts/browser.cjs fill <json>       填字：文字框直接填，原生下拉挑选项，都回读验证
 *   node scripts/browser.cjs pick <json>       自定义下拉：点开→挑选项→回读，歧义就报错不猜
 *   node scripts/browser.cjs shot <png>        截图，给用户看当前状态
 *   node scripts/browser.cjs pages             列出所有标签页
 *   node scripts/browser.cjs close             关掉自动化浏览器
 *
 * 所有命令都可以加 --page=<地址片段> 指定操作哪个标签页（默认最后一个）。
 * 表格页、详情页、申请表同时开着的时候必须加，否则容易操作错页：
 *   node scripts/browser.cjs dump --page=resume/7683
 *
 * fill 的 json 格式：
 *   [{"label":"姓名","value":"张三"},{"label":"学历","value":"硕士"}]
 *
 * 下拉的处理边界：
 *   原生 <select>      —— 唯一命中才选，歧义就报错不猜，选完回读确认
 *   自定义下拉（antd / element / 北森那类 div 浮层）—— 只识别、不代填，留用户点
 */
require('./ensure_deps.cjs');
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const ops = require('./page_ops.cjs');
const { pickOnPage } = require('./pick_logic.cjs');

const ROOT = path.resolve(__dirname, '..');
const { config: readCfg, dataDir } = require('./config.cjs');

// 个人数据放在仓库外面；装成技能时在用户主目录下（由 config.cjs 统一判断，
// 不能在这里写死「与仓库平级」——托管目录旁边没有那个文件夹）
const DATA = dataDir();
const CFG = readCfg();

const PORT = 9222;
const PROFILE = path.join(DATA, 'browser-profile');
const PIDFILE = path.join(PROFILE, '.pid');

const log = (...a) => console.log(...a);
const die = (m) => { console.error(m); process.exit(1); };

async function connect() {
  try {
    return await chromium.connectOverCDP(`http://127.0.0.1:${PORT}`);
  } catch (e) {
    die('没有可用的自动化浏览器。先跑：node scripts/browser.cjs launch <岗位地址>');
  }
}

async function currentPage(browser) {
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();
  // 一条真实表单可能开着好几个标签（列表页、详情页、申请表），默认只取最后一个
  // 很容易操作错页，所以支持 --page <地址片段> 指定
  const hint = (process.argv.find(a => a.indexOf('--page=') === 0) || '').slice(7);
  if (hint) {
    const hit = pages.find(p => p.url().indexOf(hint) >= 0);
    if (!hit) die('没有地址里含「' + hint + '」的标签页。现在开着：\n' + pages.map(p => '  ' + p.url()).join('\n'));
    return hit;
  }
  return pages[pages.length - 1];
}

/** 在**已有的同一个标签**里打开网址：已经有了就切过去，没有就复用当前标签导航。
 *  北森的职位卡片是 target=_blank，点一次会新开一个标签，所以脚本不要靠点点点，
 *  拿到详情页地址后一律用 open 复用标签，避免越点越多。 */
async function cmdOpen(url) {
  if (!url) die('用法: browser.cjs open <网址>');
  const browser = await connect();
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();

  const same = pages.find(p => p.url() === url);
  if (same) {
    await same.bringToFront();
    log('这个地址已经开着，切过去了：' + url);
    process.exit(0);
  }
  const blank = pages.find(p => p.url() === 'about:blank');
  const page = blank || pages[pages.length - 1];
  await page.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {});
  log('已打开：' + page.url() + `（当前共 ${ctx.pages().length} 个标签）`);
  process.exit(0);
}

/** 关掉多余标签，只留一个。参数是可选的「保留哪个」的地址片段 */
async function cmdCloseTabs(keep) {
  const browser = await connect();
  const ctx = browser.contexts()[0];
  const pages = ctx.pages();
  let keepPage = keep ? pages.find(p => p.url().includes(keep)) : pages[0];
  if (!keepPage) keepPage = pages[0];
  let closed = 0;
  for (const p of pages) {
    if (p === keepPage) continue;
    await p.close().catch(() => {});
    closed++;
  }
  log(`关掉 ${closed} 个标签，保留：` + keepPage.url());
  process.exit(0);
}

async function cdpAlive() {
  try {
    const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
    return r.ok;
  } catch (e) { return false; }
}

async function cmdLaunch(url) {
  if (!CFG.browser_path) die('config.json 里没有 browser_path，先跑 detect_browsers.py 让用户选浏览器');

  // 已经有一个自动化浏览器在跑：直接在里面打开新地址，不要重复启动
  if (await cdpAlive()) {
    const browser = await connect();
    const page = await currentPage(browser);
    if (url) { await page.goto(url, { waitUntil: 'domcontentloaded' }).catch(() => {}); }
    log('复用已在运行的自动化浏览器，当前地址：' + page.url());
    process.exit(0);
  }

  if (!url) die('用法: launch <url>');
  const args = [
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--start-maximized', '--no-first-run', '--no-default-browser-check',
    url,
  ];
  const child = spawn(CFG.browser_path, args, { detached: true, stdio: 'ignore' });
  child.unref();
  // 记下 PID：关的时候只杀这一个进程树，绝不碰用户日常浏览器
  try { fs.mkdirSync(PROFILE, { recursive: true }); fs.writeFileSync(PIDFILE, String(child.pid)); } catch (e) {}
  log(`已启动 ${CFG.browser_name || '浏览器'}，调试端口 ${PORT}（PID ${child.pid}）`);
  log('等用户扫码登录，登录后跑 dump 继续。');
  process.exit(0);
}

async function cmdDump() {
  const browser = await connect();
  const page = await currentPage(browser);
  // 页内逻辑在 page_ops.cjs，测试脚本用的是同一份
  const info = await page.evaluate(ops.dumpExpression());
  // --only-empty：只看空着的，省得每次读几十条已填好的字段
  if (process.argv.includes('--only-empty')) {
    info.rows = info.rows.filter(r => r.empty);
    delete info.audit;
  }
  log(JSON.stringify(info, null, 1));
  process.exit(0);
}

async function cmdFill(jsonPath) {
  if (!jsonPath) die('用法: fill <json文件路径>');
  const items = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const browser = await connect();
  const page = await currentPage(browser);

  // 原生 select 会挑选项并回读；自定义下拉只回「需要用户点」，不尝试
  const result = await page.evaluate(ops.fillExpression(items));

  result.forEach(r => {
    if (r.ok) { log('✓ ' + r.label + (r.kind === 'select' ? ' → ' + r.got : '')); return; }
    log('✗ ' + r.label + ' → ' + (r.why || r.got));
    if (r.candidates && r.candidates.length) {
      log('    可选：' + r.candidates.slice(0, 8).join(' / ') +
          (r.candidates.length > 8 ? ` …共 ${r.candidates.length} 项` : ''));
    }
  });
  const bad = result.filter(r => !r.ok).length;
  log(`\n填了 ${result.length - bad} 个，失败 ${bad} 个`);
  process.exit(bad ? 1 : 0);
}

/**
 * pick <json> —— 自定义下拉代填
 *
 * 这类控件是 div 触发器加一层浮层，不是原生 select，只能「点开 → 读选项 → 点中 → 回读」。
 * 四条硬规则，缺一条就停，绝不猜：
 *   1. 浮层必须只有一层可见，同时冒出两个说明还有别的下拉开着，按顺序点就会点错
 *   2. 选项里必须有唯一命中（先完全同名，再唯一包含），多个都像就报候选
 *   3. 点完立刻回读触发器上的显示文字，对不上算失败
 *   4. 不滚动找、不重试第二次——点错了比没填更糟
 */
async function cmdPick(jsonPath) {
  if (!jsonPath) die('用法: pick <json文件路径>');
  const items = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const browser = await connect();
  const page = await currentPage(browser);
  // 逻辑在 pick_logic.cjs，测试脚本用的是同一份
  result(await pickOnPage(page, ops, items));
}

function result(out) {
  out.forEach(r => {
    if (r.ok) { log('✓ ' + r.label + ' → ' + r.got); return; }
    log('✗ ' + r.label + ' → ' + (r.why || ('点完读回来是「' + r.got + '」，不对')));
    if (r.candidates && r.candidates.length) {
      log('    可选：' + r.candidates.join(' / '));
    }
  });
  const bad = out.filter(r => !r.ok).length;
  log(`\n成功 ${out.length - bad} 个，失败 ${bad} 个`);
  process.exit(bad ? 1 : 0);
}

async function cmdUpload(file) {
  if (!file) die('用法: upload <文件绝对路径>');
  if (!fs.existsSync(file)) die('文件不存在: ' + file);
  const browser = await connect();
  const page = await currentPage(browser);

  const inputs = page.locator('input[type=file]');
  let n = await inputs.count();
  if (!n) {
    // 上传控件经常藏在折叠区块里，页面上只露出一个「添加」图标（娃哈哈就是
    // <img alt="addIcon">）。先看有没有这类图标，点开一次再数；只试这一次，
    // 还找不到就把图标列出来告诉人该点哪儿，不说「这一页没有上传控件」这种废话。
    const icons = await page.evaluate(() => [...document.querySelectorAll('img[alt],button[aria-label]')]
      .filter(el => el.getBoundingClientRect().width > 0)
      .map(el => {
        const box = el.closest('.EditorCard,.card,section,.el-card,.el-form-item') || el.parentElement;
        return {
          alt: (el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim(),
          ctx: box ? (box.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 24) : '',
        };
      }).filter(o => o.alt));
    const add = icons.find(o => /add|添加|新增|upload|上传/i.test(o.alt));
    if (add) {
      await page.evaluate(alt => {
        const el = [...document.querySelectorAll('img[alt],button[aria-label]')]
          .find(x => (x.getAttribute('alt') || x.getAttribute('aria-label') || '').trim() === alt);
        if (el) el.click();
      }, add.alt);
      await page.waitForTimeout(800);
      n = await inputs.count();
    }
    if (!n) {
      die('页面上一共有 ' + icons.length + ' 个图标按钮，没有任何一个是可选文件的上传控件：\n' +
          (icons.map(o => '  ' + o.alt + (o.ctx ? '（在「' + o.ctx + '」区块）' : '')).join('\n') || '  （没有找到图标按钮）'));
    }
  }

  // 简历和证件照都是 file 控件，按路径判断该传哪个
  const isPhoto = /\.(jpe?g|png)$/i.test(file);
  const idx = isPhoto ? n - 1 : 0;

  await inputs.nth(idx).setInputFiles(file);

  const base = path.basename(file);
  // 等「页面上出现这个文件名」，而不是干等固定几秒；最多等 8 秒
  let shown = false;
  for (let i = 0; i < 16; i++) {
    await page.waitForTimeout(500);
    shown = await page.evaluate(n => document.body.innerText.indexOf(n) >= 0, base)
      .catch(() => false);
    if (shown && i >= 1) break;      // 至少等 1 秒，避免刚传上去还没渲染
  }
  // 控件里的 files 只是个中间态：飞书招聘这类系统接完文件就把原生控件清空，
  // 拿它当唯一依据会报假失败。真正的判据是「页面上出现了这个文件名」。
  const n2 = await inputs.nth(idx).evaluate(el => (el.files ? el.files.length : 0)).catch(() => 0);
  log((n2 > 0 || shown ? '✓ ' : '✗ ') + '已上传 ' + base +
      '（' + (n2 > 0 ? '控件里有文件' : shown ? '页面上已出现文件名' : '控件和页面都没看到') + '）');
  log('提示：上传可能触发简历解析并重置表单，下一步先 dump 确认字段状态');
  process.exit(0);
}

async function cmdText(maxChars) {
  const browser = await connect();
  const page = await currentPage(browser);
  const t = await page.evaluate((m) => document.body.innerText.replace(/\n{3,}/g, '\n\n').slice(0, m),
    parseInt(maxChars) || 3000);
  log(t);
  process.exit(0);
}

async function cmdShot(png) {
  const browser = await connect();
  const page = await currentPage(browser);
  await page.bringToFront();
  await page.waitForTimeout(400);
  await page.screenshot({ path: png || 'shot.png', fullPage: false });
  log('已截图: ' + (png || 'shot.png'));
  process.exit(0);
}

async function cmdPages() {
  const browser = await connect();
  browser.contexts()[0].pages().forEach((p, i) => log(i + '  ' + p.url()));
  process.exit(0);
}

/** panels —— 有没有「开着、已经填了值、还没保存」的编辑面板。
 *  切区块之前跑这一条：有就先点保存，没有再往下走。 */
async function cmdPanels() {
  const browser = await connect();
  const page = await currentPage(browser);
  const info = await withDeadline(page.evaluate(ops.panelExpression()), 15000, '扫编辑面板');
  log(JSON.stringify(info, null, 1));
  process.exit(info && info.found ? 1 : 0);
}

/** 给任意一个 await 加个上限。为什么必须有：page.evaluate 没有默认超时，
 *  页面一旦卡住（死循环、弹窗、脚本忙），命令就永远不返回——实测卡死过一次
 *  180 秒，node 进程一直挂着。有了它，最坏情况是「报一句超时然后退出」。 */
function withDeadline(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + '超过 ' + Math.round(ms / 1000) + ' 秒没返回')), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/** 日期控件：点开 → 清空 → 逐字敲 → 回车 → 回读。
 *  element-ui 的日期框是只读 input 加浮层，直接设 value 不触发选中，必须真的敲。
 *  级联地区不在这里做——选完上一级会重建下一级，追着改容易错位。 */
async function setDate(page, label, value) {
  try {
    const safe = String(label).replace(/"/g, '\\"');
    const loc = page.locator(`.el-form-item:has-text("${safe}") input`).first();
    if (!(await loc.count())) return { label, ok: false, why: '没找到日期控件' };
    await loc.click({ timeout: 5000 }).catch(() => {});
    await loc.fill('').catch(() => {});
    await page.keyboard.type(String(value), { delay: 30 });
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    const got = (await loc.inputValue().catch(() => '')) || '';
    const strip = s => String(s).replace(/[\s\-\/年月日]/g, '');
    return { label, ok: !!got && strip(got) === strip(value), got };
  } catch (e) {
    return { label, ok: false, why: String(e.message || e).slice(0, 80) };
  }
}

/**
 * auto <plan.json> [--page=地址片段] —— 一次进程内把一张表能填的都填完
 *
 * 为什么合并成一条：每条 browser.cjs 命令都要重新 require('playwright')（实测 2.9 秒）
 * 再连一次 CDP（1–2 秒）。一个字段一条命令的话，光进程开销就比干活本身久。
 *
 * plan.json 四段，都是可选：
 *   fill: [{label, value, nth}]   文字框 + 原生 select
 *   pick: [{label, value}]        自定义下拉（点开 → 读选项 → 唯一命中才点）
 *   date: [{label, value}]        日期框（逐字敲 + 回车）
 *   cascade: 不做，留给用户
 * 失败不中断，最后一并交失败清单——不许为了一个字段把整张表停住。
 */
async function cmdAuto(planFile) {
  if (!planFile) die('用法: auto <plan.json> [--page=地址片段]');
  const raw = fs.readFileSync(planFile);
  let text;
  if (raw.length >= 2 && raw[0] === 0xff && raw[1] === 0xfe) {
    text = new TextDecoder('utf-16le').decode(raw.subarray(2));
  } else {
    text = raw.toString('utf8').replace(/^﻿/, '');
  }
  const plan = JSON.parse(text);
  const fillItems = Array.isArray(plan.fill) ? plan.fill : [];
  const pickItems = Array.isArray(plan.pick) ? plan.pick : [];
  const dateItems = Array.isArray(plan.date) ? plan.date : [];

  const browser = await connect();
  const page = await currentPage(browser);
  page.setDefaultTimeout(5000);
  const out = { url: page.url(), fill: [], pick: [], date: [], failed: [] };

  // 每一段都设上限：任何一段卡住都只影响这一段，最后一定退出并给出可读原因。
  try {
    if (fillItems.length) {
      out.fill = await withDeadline(page.evaluate(ops.fillExpression(fillItems)), 20000, '填文字框');
      await page.waitForTimeout(200);
    }
    if (pickItems.length) {
      out.pick = await withDeadline(pickOnPage(page, ops, pickItems), 45000, '填自定义下拉');
      await page.waitForTimeout(200);
    }
    for (const d of dateItems) {
      out.date.push(await withDeadline(setDate(page, d.label, d.value), 15000, '填日期「' + d.label + '」')
        .catch(e => ({ label: d.label, ok: false, why: e.message })));
    }
  } catch (e) {
    out.error = String((e && e.message) || e);
  }

  for (const stage of ['fill', 'pick', 'date']) {
    for (const r of out[stage] || []) {
      if (!r.ok) out.failed.push({ stage: stage, label: r.label, why: r.why || r.got || '' });
    }
  }

  // 填完回读一遍，省得再单独跑一次 dump。回读失败不算整体失败，但要说出来。
  try {
    const info = await withDeadline(page.evaluate(ops.dumpExpression()), 15000, '回读表单');
    out.audit = info.audit;
    if (info.unsaved_panel && info.unsaved_panel.found) out.unsaved_panel = info.unsaved_panel;
  } catch (e) {
    out.audit = { error: String((e && e.message) || e) };
  }

  log(JSON.stringify(out, null, 1));
  process.exit((out.error || out.failed.length) ? 1 : 0);
}

(async () => {
  const [cmd, arg] = process.argv.slice(2);
  switch (cmd) {
    case 'launch': await cmdLaunch(arg); break;
    case 'dump': await cmdDump(); break;
    case 'fill': await cmdFill(arg); break;
    case 'pick': await cmdPick(arg); break;
    case 'auto': await cmdAuto(arg); break;
    case 'upload': await cmdUpload(arg); break;
    case 'text': await cmdText(arg); break;
    case 'shot': await cmdShot(arg); break;
    case 'pages': await cmdPages(); break;
    case 'panels': await cmdPanels(); break;
    case 'open': await cmdOpen(arg); break;
    case 'close-tabs': await cmdCloseTabs(arg); break;
    case 'close': {
      // 只杀本脚本启动的那个进程树，绝不按进程名批量杀（那样会连用户日常浏览器一起关掉）
      let pid = null;
      try { pid = parseInt(fs.readFileSync(PIDFILE, 'utf8').trim(), 10); } catch (e) {}
      if (!pid) { log('没有记录到自动化浏览器的 PID，跳过（不会去杀其他浏览器进程）'); process.exit(0); }
      try {
        require('child_process').execSync(`taskkill /PID ${pid} /T /F`, { stdio: 'ignore' });
        log('已关闭自动化浏览器（PID ' + pid + '）');
      } catch (e) {
        log('该 PID 已不存在，可能已经关掉了');
      }
      try { fs.unlinkSync(PIDFILE); } catch (e) {}
      process.exit(0);
      break;
    }
    default:
      log(fs.readFileSync(__filename, 'utf8').split('*/')[0]);
      process.exit(0);
  }
})();
