#!/usr/bin/env node
/**
 * sync_jobs.cjs —— 同步内置岗位数据源（飞书多维表格）
 *
 * 原理：该表开启了「获得链接的人可阅读」。直接 HTTP 请求会被判「Login Required」，
 *      但真实浏览器打开一次页面后会拿到访客态会话，之后在同源上下文里 fetch 即可取数。
 *      解压与字段映射全部放在页面内完成（DecompressionStream），只把紧凑的行数据传回 Node，
 *      避免每页 200KB 的 base64 往返导致的超时。
 *
 * 用法：
 *   node sync_jobs.cjs                  # 全部子表
 *   node sync_jobs.cjs --only=jobs      # 只同步岗位表（jobs|referrals|majors）
 *   node sync_jobs.cjs --quiet
 *   node sync_jobs.cjs --out=./data
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

const APP = 'OGPubC12daD6HlsNcpwc7mR4nCd';
const BASE = 'https://acn8c93z7tds.feishu.cn';

const TABLES = {
  jobs: { id: 'tblFNPEDJ2kHQGc4', name: '27秋招汇总表' },
  referrals: { id: 'tblkwb9UTNkcvFpz', name: '内推码分享' },
  majors: { id: 'tblBBEYMOBVzKv8D', name: '如何根据专业选岗位' },
};

// 字段映射兜底表：运行时解析失败时用它（2026-10-31 从原表实测得到）
const FIELD_FALLBACK = {
  tblFNPEDJ2kHQGc4: {
    fldD6LZP6B: '企业名称', fld5BgzZQw: '岗位列表', fldSODVLa3: '所属行业',
    fldB60nVbW: '工作城市', fldTY1Jivl: '学历要求', fldPFHXwXE: '届次要求',
    flduPHNsF6: '招聘类型', fld9l6fs3y: '企业类型', fldqa7rJB9: '开始日期',
    flds3D2J9H: '截止日期', fld1Lj3xrx: '公告地址', fldhHg1lnl: '投递地址',
    fld1jlDOuX: '独家标签', fldBnmbCFn: '独家情报（仅供参考）',
    fld7vhxjEF: '更新日期', fldkifevW3: '倒计时', fldzEU4VZ2: 'end_time',
    fldCjU4O4N: 'version', fldPGBucXT: 'url_md5',
  },
  tblkwb9UTNkcvFpz: {
    fldZSwOL8Q: '公司名', fldpTjCCPF: '内推码', fld3KNhZtN: '内推地址',
    fldc7ujAYp: '备注信息', fld6tFzbqA: '更新日期',
  },
  tblBBEYMOBVzKv8D: { fldNgoy01C: '专业', fldQWXOuC7: '说明' },
};

// 只保留筛岗真正用得到的字段，其余丢弃（缓存体积可降 80%）
const SLIM = {
  tblFNPEDJ2kHQGc4: ['企业名称', '岗位列表', '所属行业', '工作城市', '学历要求',
                     '届次要求', '招聘类型', '截止日期', '投递地址', '独家情报（仅供参考）'],
  tblkwb9UTNkcvFpz: ['公司名', '内推码', '内推地址', '备注信息'],
  tblBBEYMOBVzKv8D: ['专业', '说明'],
};

const PAGE_SIZE = 200;
const CHUNK = 10;              // 每批评估拉取的页数
const THROTTLE_MS = 120;
const MAX_RETRY = 3;

const args = process.argv.slice(2);
const quiet = args.includes('--quiet');
const onlyArg = args.find(a => a.startsWith('--only='));
const only = onlyArg ? onlyArg.split('=')[1].split(',') : null;
const outDir = (args.find(a => a.startsWith('--out=')) || '--out=./data').split('=')[1];

const log = (...a) => { if (!quiet) console.log(...a); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

// 在页面内执行：连续拉取若干页，就地解压并映射成行
const PULL = async ({ base, app, tableId, fieldNames, startOffset, pages, pageSize }) => {
  const gunzip = async (b64) => {
    const bin = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
    const stream = new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'));
    const buf = await new Response(stream).arrayBuffer();
    return JSON.parse(new TextDecoder().decode(buf));
  };
  const val = (v) => {
    const x = v && v.value;
    if (x == null) return '';
    if (Array.isArray(x)) {
      return x.map(i => {
        if (typeof i === 'string') return i;
        if (i == null) return '';
        // 链接型字段同时带 link 和 text，投递地址要的是真链接，所以 link 优先
        return i.link ?? i.text ?? i.name ?? '';
      }).filter(Boolean).join(' / ');
    }
    if (typeof x === 'object') return x.link ?? x.text ?? x.name ?? '';
    return String(x);
  };

  const rows = [];
  let total = null;
  let failed = 0;

  for (let p = 0; p < pages; p++) {
    const offset = startOffset + p * pageSize;
    let ok = false;
    for (let attempt = 1; attempt <= 3 && !ok; attempt++) {
      try {
        const r = await fetch(`${base}/space/api/v1/bitable/${app}/records?tableID=${tableId}&offset=${offset}`, { credentials: 'include' });
        const t = await r.text();
        if (!t.includes('"code":0')) throw new Error('code!=0');
        const body = await gunzip(JSON.parse(t).data.records);
        total = body.tableRecordNum ?? total;
        const map = body.recordMap || {};
        const ids = Object.keys(map);
        if (!ids.length) { ok = true; failed++; break; }
        for (const rid of ids) {
          const row = { _id: rid };
          for (const [fid, v] of Object.entries(map[rid])) row[fieldNames[fid] || fid] = val(v);
          rows.push(row);
        }
        ok = true;
      } catch (e) {
        await new Promise(r => setTimeout(r, 400 * attempt));
      }
    }
    if (!ok) failed++;
  }
  return { rows, total, failed };
};

(async () => {
  const started = Date.now();
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const page = await (await browser.newContext()).newPage();

  log('启动浏览器，建立访客会话…');

  // 字段名只在页面自己请求 tablesv3 时才会带全，直接 fetch 拿不到，所以先挂监听
  let metaBody = null;
  page.on('response', (res) => {
    if (/tablesv3/.test(res.url()) && !metaBody) {
      res.text().then(t => { if (!metaBody) metaBody = t; }).catch(() => {});
    }
  });

  // 必须带 ?table= 打开，否则页面不会请求该表的元数据，字段名和选项映射都拿不到
  await page.goto(`${BASE}/base/${APP}?table=${TABLES.jobs.id}`, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  await page.waitForTimeout(6000);

  const probe = await page.evaluate(async (u) => {
    try {
      const r = await fetch(u, { credentials: 'include' });
      const j = await r.json();
      return { code: j.code, hasRecords: !!(j.data && j.data.records) };
    } catch (e) { return { err: String(e).slice(0, 60) }; }
  }, `${BASE}/space/api/v1/bitable/${APP}/records?tableID=${TABLES.jobs.id}&offset=0`);

  if (!(probe.code === 0 && probe.hasRecords)) {
    console.error('会话建立失败：' + JSON.stringify(probe));
    await browser.close();
    process.exit(2);
  }
  log('会话可用\n');

  fs.mkdirSync(outDir, { recursive: true });
  const summary = [];

  for (const [key, t] of Object.entries(TABLES)) {
    if (only && !only.includes(key)) continue;

    // 字段名：优先用页面加载时捕获的那份，取不到再直接请求一次
    let metaRaw = metaBody || '';
    if (!metaRaw) {
      metaRaw = await page.evaluate(async (u) => {
        try { const r = await fetch(u, { credentials: 'include' }); return await r.text(); } catch (e) { return ''; }
      }, `${BASE}/space/api/bitable/${APP}/tablesv3/`);
    }

    let fieldNames = {};
    const optMaps = {};          // 字段名 → { 选项ID: 选项名 }
    try {
      const j = JSON.parse(metaRaw);
      for (const [tid, payload] of Object.entries(j.data || {})) {
        if (tid !== t.id) continue;
        const m = JSON.parse(zlib.gunzipSync(Buffer.from(payload, 'base64')).toString('utf8'));
        for (const [fid, f] of Object.entries(m.fieldMap || {})) {
          fieldNames[fid] = f.name;
          const opts = (f.property && f.property.options) || [];
          if (opts.length) {
            const map = {};
            for (const o of opts) map[o.id] = o.name;
            optMaps[f.name] = map;
          }
        }
      }
    } catch (e) {
      log('  ⚠ 字段名解析失败：' + String(e).slice(0, 60));
    }
    if (!Object.keys(fieldNames).length) {
      const fb = FIELD_FALLBACK[t.id];
      if (fb) {
        fieldNames = { ...fb };
        log('  字段名解析失败，已套用内置映射表');
      } else {
        log('  ⚠ 未取到字段名，本次导出的键名会是内部 ID');
      }
    }

    let offset = 0, total = null, failedPages = 0;
    const rows = [];
    const seen = new Set();

    while (offset < 100000) {
      const r = await page.evaluate(PULL, {
        base: BASE, app: APP, tableId: t.id, fieldNames,
        startOffset: offset, pages: CHUNK, pageSize: PAGE_SIZE,
      });
      if (r.total != null) total = r.total;
      failedPages += r.failed || 0;
      for (const row of r.rows) {
        if (seen.has(row._id)) continue;
        seen.add(row._id);
        // 单选项返回的是内部 ID（optXXX），翻成中文
        for (const [k, v] of Object.entries(row)) {
          const map = optMaps[k];
          if (!map || typeof v !== 'string' || !v.includes('opt')) continue;
          row[k] = v.split(/\s*\/\s*/)
            .map(tok => map[tok] || tok)
            .join(' / ');
        }
        rows.push(row);
      }
      log(`  ${t.name}: ${rows.length}/${total ?? '?'}`);
      if (!r.rows.length) break;
      if (total != null && rows.length >= total) break;
      offset += CHUNK * PAGE_SIZE;
      await sleep(THROTTLE_MS);
    }

    // 按白名单裁剪字段
    const keep = SLIM[t.id];
    let finalRows = rows;
    if (keep && keep.length) {
      finalRows = rows.map(r => {
        const o = { _id: r._id };
        for (const k of keep) if (r[k] !== undefined) o[k] = r[k];
        return o;
      });
    }

    const out = {
      syncedAt: new Date().toISOString(),
      source: `${BASE}/base/${APP}?table=${t.id}`,
      tableId: t.id,
      tableName: t.name,
      fields: Object.values(fieldNames),
      recordCount: finalRows.length,
      tableRecordNum: total,
      complete: total == null || rows.length >= total,
      failedPages,
      rows: finalRows,
    };
    const file = path.join(outDir, key + '.json');
    fs.writeFileSync(file, JSON.stringify(out), 'utf8');
    summary.push({ name: t.name, rows: rows.length, total, failedPages, file });
  }

  await browser.close();

  console.log('\n=== 同步结果 ===');
  summary.forEach(s => console.log(`  ${s.name}: ${s.rows}/${s.total ?? '?'} 条${s.failedPages ? `（${s.failedPages} 页失败）` : ''}`));
  console.log(`  耗时 ${((Date.now() - started) / 1000).toFixed(1)}s`);
  process.exit(0);
})().catch(e => { console.error('ERR ' + String(e).slice(0, 300)); process.exit(1); });
