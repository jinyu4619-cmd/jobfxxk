# -*- coding: utf-8 -*-
"""把岗位缓存里的选项 ID 翻译成中文（单独一步，不用重跑 90 秒的同步）

用法: python scripts/translate_jobs.py
它会：
  1. 起无头浏览器打开岗位表，拦下 tablesv3 响应
  2. 取出每个字段的中文名和「选项ID → 选项名」映射
  3. 就地改写 data/jobs.json，把 optXXX 换成中文
  4. 把映射存到 data/options.json，以后字段变了自己能查
"""
import json, os, subprocess, sys, tempfile
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CFG, REPO, DATA_DIR, path_of
CACHE = CFG.get('job_cache', os.path.join(DATA_DIR, 'jobs.json')).replace('/', os.sep)
OPTFILE = os.path.join(DATA_DIR, 'options.json')

PROBE = r'''
const { chromium } = require('playwright');
const zlib = require('zlib');
const APP = 'OGPubC12daD6HlsNcpwc7mR4nCd';
const BASE = 'https://acn8c93z7tds.feishu.cn';
const TABLE = 'tblFNPEDJ2kHQGc4';
(async () => {
  const b = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await (await b.newContext()).newPage();
  const metas = {};
  page.on('response', async (res) => {
    if (!/tablesv3/.test(res.url())) return;
    try {
      const j = JSON.parse(await res.text());
      for (const [tid, payload] of Object.entries(j.data || {})) {
        if (metas[tid]) continue;
        metas[tid] = JSON.parse(zlib.gunzipSync(Buffer.from(payload, 'base64')).toString('utf8'));
      }
    } catch (e) {}
  });
  for (const tid of [TABLE, 'tblkwb9UTNkcvFpz', 'tblBBEYMOBVzKv8D']) {
    await page.goto(BASE + '/base/' + APP + '?table=' + tid, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(9000);
    if (metas[TABLE]) break;
  }
  await b.close();
  const m = metas[TABLE];
  if (!m) { console.log('NO_CAPTURE'); process.exit(1); }
  const out = { fields: {}, options: {} };
  for (const [fid, f] of Object.entries(m.fieldMap || {})) {
    out.fields[fid] = f.name;
    const opts = (f.property && f.property.options) || [];
    if (opts.length) {
      const map = {};
      for (const o of opts) map[o.id] = o.name;
      out.options[f.name] = map;
    }
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
'''


def main():
    if not os.path.exists(CACHE):
        sys.exit('岗位缓存不存在，先跑 sync_jobs.cjs --only=jobs')

    tmp = os.path.join(tempfile.gettempdir(), 'probe_meta.cjs')
    open(tmp, 'w', encoding='utf-8').write(PROBE)

    node = shutil_which('node') or r'D:\nodejs\node.exe'
    env = dict(os.environ)
    env.setdefault('NODE_PATH', CFG.get('node_path', ''))
    r = subprocess.run([node, tmp], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', env=env, timeout=240)
    out = (r.stdout or '').strip().splitlines()
    if not out or out[-1] == 'NO_CAPTURE':
        sys.exit('没抓到表结构：' + (r.stderr or '')[:200])

    meta = json.loads(out[-1])
    opts = meta['options']
    print(f'拿到 {len(opts)} 个字段的选项映射：' + '、'.join(opts.keys()))

    d = json.load(open(CACHE, encoding='utf-8'))
    rows = d.get('rows', [])
    changed = 0
    for row in rows:
        for k, v in list(row.items()):
            m = opts.get(k)
            if not m or not isinstance(v, str) or 'opt' not in v:
                continue
            parts = [p.strip() for p in v.split('/')]
            new = ' / '.join(m.get(p, p) for p in parts if p)
            if new != v:
                row[k] = new
                changed += 1
    d['rows'] = rows
    d['translated'] = True
    json.dump(d, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False)
    json.dump(opts, open(OPTFILE, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(f'已翻译 {changed} 个单元格')
    print('示例：', {k: rows[0].get(k) for k in list(opts.keys())[:3]})
    print('映射表已存：', OPTFILE)


def shutil_which(name):
    from shutil import which
    return which(name)


main()
