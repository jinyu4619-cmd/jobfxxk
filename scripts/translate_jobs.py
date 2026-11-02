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

# 探针写到数据目录的 tmp/ 下，不写进 scripts/。
# 为什么：装成技能后代码在应用托管的目录里，那个目录可能只读，写进去就整个翻译步骤崩掉；
# 而且万一进程被掐断，残留的 _probe_meta.cjs 会被下次打包收进 .skill。
# 探针里对 ensure_deps / browser_config 的 require 因此要走绝对路径——Node 的模块解析
# 是按「脚本所在目录」往上找的，探针挪走了，相对路径就断了。
PROBE_PATH = os.path.join(DATA_DIR, 'tmp', '_probe_meta.cjs')

PROBE = r'''
require('__ENSURE__');
const { chromium } = require('playwright');
const { launch } = require('__BCFG__');
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
  // 打开表页，等它自己请求 tablesv3；拿到就立刻走，最多等 12 秒
  await page.goto(BASE + '/base/' + APP + '?table=' + TABLE, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  const t0 = Date.now();
  while (!metas[TABLE] && Date.now() - t0 < 12000) {
    await page.waitForTimeout(250);
  }
  // 还没抓到，换另外两张表各试一次（有的表要切过去才会拉元数据）
  if (!metas[TABLE]) {
    for (const tid of ['__REF__', '__MAJ__']) {
      if (!tid || metas[TABLE]) continue;
      await page.goto(BASE + '/base/' + APP + '?table=' + tid, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
      const t1 = Date.now();
      while (!metas[TABLE] && Date.now() - t1 < 6000) await page.waitForTimeout(250);
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

    def js(p):
        """Windows 路径转成 Node 能吃的写法：正斜杠 + 单引号转义"""
        return str(p).replace('\\', '/').replace("'", "\\'")

    probe = (PROBE
             .replace('__ENSURE__', js(os.path.join(HERE, 'ensure_deps.cjs')))
             .replace('__BCFG__', js(os.path.join(HERE, 'browser_config.cjs')))
             .replace('__APP__', src.get('app') or 'OGPubC12daD6HlsNcpwc7mR4nCd')
             .replace('__BASE__', src.get('base') or 'https://acn8c93z7tds.feishu.cn')
             .replace('__TABLE__', src.get('jobs') or 'tblFNPEDJ2kHQGc4')
             .replace('__REF__', src.get('referrals') or 'tblkwb9UTNkcvFpz')
             .replace('__MAJ__', src.get('majors') or 'tblBBEYMOBVzKv8D'))
    os.makedirs(os.path.dirname(PROBE_PATH), exist_ok=True)
    open(PROBE_PATH, 'w', encoding='utf-8').write(probe)
    env = dict(os.environ)
    # 仓库里的 node_modules 和自己装的 deps/ 都塞进 NODE_PATH，探针挪走了也能找到 playwright
    env['NODE_PATH'] = os.pathsep.join(
        [os.path.join(ROOT, 'node_modules'), os.path.join(DATA_DIR, 'deps', 'node_modules')])
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
