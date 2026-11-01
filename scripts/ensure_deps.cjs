#!/usr/bin/env node
/**
 * ensure_deps.cjs —— 让 node 找得到装在数据目录里的依赖
 *
 * 为什么要把依赖挪出仓库：
 *   装成 skill 之后，代码在应用托管的目录里，那个目录可能是只读的，
 *   也可能在升级时被整个替换。依赖写在那里，要么装不上，要么升级就没了。
 *   所以依赖装到数据目录的 deps/ 下，代码保持干净。
 *
 * 用法：在 require('playwright') 之前 require 一次本文件。
 */
const fs = require('fs');
const path = require('path');
const Module = require('module');

const REPO = path.resolve(__dirname, '..');

function dataDir() {
  try {
    const { dataDir: d } = require('./config.cjs');
    return d();
  } catch (e) {
    return path.join(path.dirname(REPO), 'jobfxxk-data');
  }
}

const local = path.join(REPO, 'node_modules');
const shared = path.join(dataDir(), 'deps', 'node_modules');

const extra = [local, shared].filter(p => {
  try { return fs.existsSync(p); } catch (e) { return false; }
});

if (extra.length) {
  const cur = (process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean);
  const merged = [...extra, ...cur.filter(p => !extra.includes(p))];
  process.env.NODE_PATH = merged.join(path.delimiter);
  Module._initPaths();      // 让 NODE_PATH 立刻生效，不重启进程
}

module.exports = { REPO, dataDir, local, shared };
