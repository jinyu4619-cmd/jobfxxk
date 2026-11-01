# -*- coding: utf-8 -*-
"""把岗位缓存里的选项 ID 翻译成中文（单独一步，不用重跑 90 秒的同步）

用法: python scripts/translate_jobs.py
它会：
  1. 起无头浏览器打开岗位表，拦下 tablesv3 响应
  2. 取出每个字段的中文名和「选项ID → 选项名」映射
  3. 就地改写岗位缓存，把 optXXX 换成中文
  4. 把映射存到 data/options.json，以后字段变了自己能查
"""
import json, os, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

CACHE = (CFG.get('job_cache') or os.path.join(DATA_DIR, 'jobs.json')).replace('/', os.sep)
OPTFILE = os.path.join(DATA_DIR, 'options.json')

# 探针必须落在 scripts/ 下面：Node 的模块解析是从「脚本所在目录」往上找 node_modules，
# 放 %TEMP% 里永远找不到装在仓库里的 playwright。
PROBE_PATH = os.path.join(HERE, '_probe_meta.cjs')

PROBE = r'''
require('./ensure_deps.cjs');
const { chromium } = require('playwright');
const { launch } = require('./browser_config.cjs');
const zlib = require('zlib');
const APP = '__APP__';
const BASE = '__BASE__';
const TABLE = '__TABLE__';
(async () => {
  const b = await launch(chromium, { headless: true });
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
  // 元数据是页面自己请求的，不一定会立刻到；最多试三次，抓到就停
  for (let i = 0; i < 3 && !metas[TABLE]; i++) {
    await page.goto(BASE + '/base/' + APP + '?table=' + TABLE, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
    await page.waitForTimeout(9000);
  }
  if (!metas[TABLE]) {
    for (const tid of ['__REF__', '__MAJ__']) {
      if (!tid) continue;
      await page.goto(BASE + '/base/' + APP + '?table=' + tid, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      await page.waitForTimeout(9000);
      if (metas[TABLE]) break;
    }
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


def node_bin():
    from shutil import which
    return which('node') or r'D:\nodejs\node.exe'


def main():
    if not os.path.exists(CACHE):
        sys.exit('岗位缓存不存在，先跑 node scripts/sync_jobs.cjs --only=jobs')

    src = CFG.get('job_source') or {}
    probe = (PROBE
             .replace('__APP__', src.get('app') or 'OGPubC12daD6HlsNcpwc7mR4nCd')
             .replace('__BASE__', src.get('base') or 'https://acn8c93z7tds.feishu.cn')
             .replace('__TABLE__', src.get('jobs') or 'tblFNPEDJ2kHQGc4')
             .replace('__REF__', src.get('referrals') or 'tblkwb9UTNkcvFpz')
             .replace('__MAJ__', src.get('majors') or 'tblBBEYMOBVzKv8D'))
    open(PROBE_PATH, 'w', encoding='utf-8').write(probe)
    env = dict(os.environ)
    env['NODE_PATH'] = os.path.join(ROOT, 'node_modules')   # 不覆盖成空字符串
    try:
        r = subprocess.run([node_bin(), PROBE_PATH], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=env,
                           cwd=ROOT, timeout=300)
    finally:
        try:
            os.remove(PROBE_PATH)
        except OSError:
            pass

    out = (r.stdout or '').strip().splitlines()
    if not out or out[-1] == 'NO_CAPTURE':
        sys.exit('没抓到表结构：' + ((r.stderr or '').strip()[:300] or '页面可能改版了，或者网络不通'))

    meta = json.loads(out[-1])
    opts = meta['options']
    if not opts:
        sys.exit('抓到了表结构，但里面没有任何带选项的字段。表可能改版了。')
    print(f'拿到 {len(opts)} 个字段的选项映射：' + '、'.join(opts.keys()))

    d = json.load(open(CACHE, encoding='utf-8-sig'))
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
    if rows:
        print('示例：', {k: rows[0].get(k) for k in list(opts.keys())[:3]})
    print('映射表已存：', OPTFILE)


if __name__ == '__main__':
    main()
