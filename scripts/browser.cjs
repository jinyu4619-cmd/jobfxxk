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
 *   node scripts/browser.cjs shot <png>        截图，给用户看当前状态
 *   node scripts/browser.cjs pages             列出所有标签页
 *   node scripts/browser.cjs close             关掉自动化浏览器
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

const ROOT = path.resolve(__dirname, '..');
const PARENT = path.dirname(ROOT);

// 个人数据放在仓库外面（与仓库平级的 jobfxxk-data），仓库本身保持干净
const DATA = path.join(PARENT, 'jobfxxk-data');
const CFG_PATH = [process.env.JOBFXXK_CONFIG, path.join(DATA, 'config.json'), path.join(ROOT, 'config.json')]
  .filter(Boolean).find(p => fs.existsSync(p));
const CFG = CFG_PATH ? JSON.parse(fs.readFileSync(CFG_PATH, 'utf8')) : {};

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

async function cmdUpload(file) {
  if (!file) die('用法: upload <文件绝对路径>');
  if (!fs.existsSync(file)) die('文件不存在: ' + file);
  const browser = await connect();
  const page = await currentPage(browser);

  const inputs = page.locator('input[type=file]');
  const n = await inputs.count();
  if (!n) die('这一页没有上传控件');

  // 简历和证件照都是 file 控件，按路径判断该传哪个
  const isPhoto = /\.(jpe?g|png)$/i.test(file);
  const idx = isPhoto ? n - 1 : 0;

  await inputs.nth(idx).setInputFiles(file);
  await page.waitForTimeout(3000);

  // 文件控件不会在页面文字里显示文件名，直接读控件本身的 files
  const n2 = await inputs.nth(idx).evaluate(el => (el.files ? el.files.length : 0)).catch(() => 0);
  log((n2 > 0 ? '✓ ' : '✗ ') + '已上传 ' + path.basename(file) + '（第 ' + (idx + 1) + ' 个上传框，控件确认 ' + n2 + ' 个文件）');
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

(async () => {
  const [cmd, arg] = process.argv.slice(2);
  switch (cmd) {
    case 'launch': await cmdLaunch(arg); break;
    case 'dump': await cmdDump(); break;
    case 'fill': await cmdFill(arg); break;
    case 'upload': await cmdUpload(arg); break;
    case 'text': await cmdText(arg); break;
    case 'shot': await cmdShot(arg); break;
    case 'pages': await cmdPages(); break;
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
