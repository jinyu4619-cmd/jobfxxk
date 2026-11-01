#!/usr/bin/env node
/**
 * config.cjs —— 和 paths.py 同一套查找规则，给 Node 脚本用
 *   1. 环境变量 JOBFXXK_CONFIG
 *   2. 与仓库平级的 jobfxxk-data/config.json
 *   3. 仓库里的 config.json（旧布局）
 */
const fs = require('fs');
const path = require('path');

const REPO = path.resolve(__dirname, '..');

function configPath() {
  const cands = [];
  if (process.env.JOBFXXK_CONFIG) cands.push(process.env.JOBFXXK_CONFIG);
  // 与 paths.py 保持一致：指定了数据目录就只认它，避免 Node 脚本和 Python 脚本
  // 读到不同的配置（曾导致同步写进了另一套目录）
  if (process.env.JOBFXXK_DATA) {
    cands.push(path.join(process.env.JOBFXXK_DATA, 'config.json'));
  } else {
    cands.push(path.join(path.dirname(REPO), 'jobfxxk-data', 'config.json'));
    cands.push(path.join(REPO, 'config.json'));
  }
  for (const p of cands) {
    try { if (p && fs.existsSync(p)) return p; } catch (e) { /* ignore */ }
  }
  return null;
}

function config() {
  const p = configPath();
  if (!p) return {};
  try {
    // Windows 记事本存出来的文件带 BOM，JSON.parse 会直接报错
    return JSON.parse(fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, ''));
  } catch (e) { return {}; }
}

/** 个人数据目录：配置文件所在目录；没配置时就近取 jobfxxk-data */
function dataDir() {
  const p = configPath();
  if (p) return path.dirname(p);
  return path.join(path.dirname(REPO), 'jobfxxk-data');
}

/** 配置里的路径统一成正斜杠 → 本机绝对路径 */
function resolvePath(v, fallback) {
  const s = String(v || '').trim();
  if (s) return s.replace(/[\\/]+/g, path.sep);
  return fallback;
}

module.exports = { REPO, config, configPath, dataDir, resolvePath };
