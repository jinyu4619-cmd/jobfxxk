#!/usr/bin/env node
/**
 * browser.cjs —— 网申表单的浏览器操作引擎
 *
 * 一条命令一个动作，模型按需调用，不用每次重写 Playwright 代码。
 *
 * 用法：
 *   node scripts/browser.cjs launch <url>      用 config 里选定的浏览器开窗口（带调试端口）
 *   node scripts/browser.cjs dump              导出当前页所有表单字段（字段名/类型/当前值）
 *   node scripts/browser.cjs fill <json>       逐个填文字框并回读验证
 *   node scripts/browser.cjs shot <png>        截图，给用户看当前状态
 *   node scripts/browser.cjs pages             列出所有标签页
 *   node scripts/browser.cjs close             关掉自动化浏览器
 *
 * fill 的 json 格式：
 *   [{"label":"姓名","value":"张三"},{"label":"邮箱","value":"a@b.com"}]
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

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
  const info = await page.evaluate(() => {
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
    const rows = [];
    document.querySelectorAll('input,textarea,select').forEach((el, i) => {
      if (!vis(el)) return;
      const type = (el.getAttribute('type') || el.tagName).toLowerCase();
      if (type === 'hidden' || type === 'file') return;
      // 向上找最近的、只包住这一个控件的容器，拿它的文字当字段名
      let p = el.parentElement, label = '', d = 0;
      while (p && d < 6) {
        const n = p.querySelectorAll('input,textarea,select').length;
        const t = (p.innerText || '').replace(/\s+/g, ' ').trim();
        if (n === 1 && t && t.length < 40) { label = t; break; }
        if (n > 1) break;
        p = p.parentElement; d++;
      }
      rows.push({
        i, type, label: label.slice(0, 30),
        placeholder: el.getAttribute('placeholder') || '',
        value: (el.value || '').toString().slice(0, 40),
        readonly: el.readOnly ? 'Y' : '',
      });
    });
    return { url: location.href, title: document.title, rows };
  });
  log(JSON.stringify(info, null, 1));
  process.exit(0);
}

async function cmdFill(jsonPath) {
  if (!jsonPath) die('用法: fill <json文件路径>');
  const items = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  const browser = await connect();
  const page = await currentPage(browser);

  const result = await page.evaluate((items) => {
    const out = [];
    const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 2 && r.height > 2; };
    const setVal = (el, v) => {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement : HTMLInputElement;
      const setter = Object.getOwnPropertyDescriptor(proto.prototype, 'value').set;
      setter.call(el, v);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.dispatchEvent(new Event('blur', { bubbles: true }));
    };
    for (const it of items) {
      // 按字段名找控件
      const fi = [...document.querySelectorAll('input,textarea')].filter(vis).find(el => {
        let p = el.parentElement, d = 0;
        while (p && d < 6) {
          const n = p.querySelectorAll('input,textarea,select').length;
          const t = (p.innerText || '').replace(/\s+/g, ' ').trim();
          if (n === 1 && t && t.includes(it.label)) return true;
          if (n > 1) break;
          p = p.parentElement; d++;
        }
        return false;
      });
      if (!fi) { out.push({ label: it.label, ok: false, why: '没找到该字段' }); continue; }
      setVal(fi, it.value);
      out.push({ label: it.label, ok: fi.value === it.value, got: fi.value.slice(0, 30) });
    }
    return out;
  }, items);

  result.forEach(r => log((r.ok ? '✓ ' : '✗ ') + r.label + (r.ok ? '' : ' → ' + (r.why || r.got))));
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
