#!/usr/bin/env node
/**
 * browser_config.cjs —— 统一的浏览器选择
 *
 * 三个脚本（sync_jobs / fetch_jd / translate 的探针）都要开无头浏览器。
 * 以前各自写死 channel:'msedge'，用户选了 Chrome 也不生效。
 * 现在统一走这里：先读 config.browser_path，再退 Edge，再退 Chrome，最后用 Playwright 自带内核。
 */
const fs = require('fs');
const { config, configPath } = require('./config.cjs');

function candidates(headless) {
  const c = config();
  const out = [];
  const exe = String(c.browser_path || '').trim();
  if (exe) {
    try { if (fs.existsSync(exe)) out.push({ executablePath: exe, headless }); } catch (e) { /* ignore */ }
  }
  out.push({ channel: 'msedge', headless });     // Windows 自带，不用额外下载
  out.push({ channel: 'chrome', headless });
  out.push({ headless });                        // Playwright 自带 chromium
  return out;
}

/**
 * 按优先级尝试启动，返回第一个成功的 browser。
 * @param {object} chromium playwright 的 chromium
 */
async function launch(chromium, opts = {}) {
  const headless = opts.headless !== false;
  let last = null;
  for (const o of candidates(headless)) {
    try {
      return await chromium.launch(o);
    } catch (e) {
      last = e;
    }
  }
  const hint = configPath()
    ? '试过 config.browser_path / Edge / Chrome / Playwright 内置内核，都起不来。'
    : '没找到 config.json。先按 README 配一下，或者装一个浏览器。';
  throw new Error(hint + '\n最后一次报错：' + String((last && last.message) || last).slice(0, 300));
}

function browserName() {
  const c = config();
  if (c.browser_name) return c.browser_name;
  const m = String(c.browser_path || '').match(/([^\\/]+)\.exe$/i);
  return m ? m[1] : '默认浏览器';
}

module.exports = { launch, browserName };
