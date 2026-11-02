# -*- coding: utf-8 -*-
"""阶段验收：每个高风险阶段跑一条命令，输出机器可读的通过/不通过

用法:
  python scripts/verify.py profile                 # 阶段 2 结束：档案齐了吗
  python scripts/verify.py resume                  # 简历两版本都在吗
  python scripts/verify.py layout <微调后的.pdf>    # 阶段 6 结束：版式位移了吗
  python scripts/verify.py tracker                 # 阶段 10 结束：台账自洽吗
  python scripts/verify.py jobs                    # 岗位库能筛吗

统一输出 {"ok": bool, "check": "...", "issues": [...], ...}
不通过时 issues 里每条都是能直接念给用户的一句话。
"""
import json, os, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR


def p(v):
    return (v or '').replace('/', os.sep)


def finish(name, issues, **detail):
    print(json.dumps({'ok': not issues, 'check': name, 'issues': issues, **detail},
                     ensure_ascii=False, indent=1))
    return 0 if not issues else 1


def run_py(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
    return r.stdout, r.stderr, r.returncode


# ---------- profile ----------

def check_profile():
    out, err, rc = run_py('profile.py', 'check')
    try:
        d = json.loads(out)
    except Exception:
        return finish('profile', ['档案检查跑不起来：' + (err or out)[:120]])
    issues = []
    for x in d.get('issues', []):
        if x['kind'] == 'missing_field':
            issues.append(f'档案缺「{x["detail"]}」')
        elif x['kind'] == 'unconfirmed_exp':
            issues.append(f'经历没确认过：{x["detail"]}')
        elif x['kind'] == 'unchecked_metrics':
            issues.append(f'数字没核过就确认了：{x["detail"]}')
    return finish('profile', issues)


# ---------- resume ----------

def check_resume():
    issues = []
    docx, pdf = p(CFG.get('master_docx')), p(CFG.get('master_pdf'))
    for path, label, ext in [(docx, '母版 Word', '.docx'), (pdf, '母版 PDF', '.pdf')]:
        if not path:
            issues.append(f'还没配{label}')
        elif not os.path.exists(path):
            issues.append(f'{label}找不到：{path}')
        elif not path.lower().endswith(ext):
            issues.append(f'{label}不是 {ext} 文件')
    if not issues:
        # Word 比 PDF 新，说明改了 docx 但没重新导出 PDF。
        # 这种状态下锚点表量的是旧版面，版式比对也在拿新产出比旧母版，结论会是假的。
        if (os.path.getmtime(docx) - os.path.getmtime(pdf)) > 60:
            issues.append('Word 比 PDF 新——你改了 docx 但没重新导出 PDF。'
                          '先用 Word 把这份 docx 另存为 PDF 覆盖掉旧的，再重跑 make_anchors。')
        from pypdf import PdfReader
        try:
            n = len(PdfReader(pdf).pages)
            txt = (PdfReader(pdf).pages[0].extract_text() or '').strip()
            if not txt:
                issues.append('母版 PDF 抽不出文字，可能是扫描件或图片转的，没法量版式')
            detail = {'pages': n, 'chars_on_first_page': len(txt)}
        except Exception as e:
            issues.append('母版 PDF 打不开：' + str(e)[:80])
            detail = {}
    else:
        detail = {}
    return finish('resume', issues, **detail)


# ---------- layout ----------

def check_layout(target):
    master = p(CFG.get('master_pdf'))
    if not master or not os.path.exists(master):
        return finish('layout', ['母版 PDF 不在，没法比对版式'])
    if not os.path.exists(target):
        return finish('layout', [f'找不到产出文件：{target}'])
    try:
        import fill_engine
        a = fill_engine.measure(master)
        b = fill_engine.measure(target)
    except Exception as e:
        return finish('layout', ['量版式失败：' + str(e)[:100]])

    issues = []
    if a['pages'] != b['pages']:
        issues.append(f'页数变了：{a["pages"]} → {b["pages"]}')
    if abs(a['top'] - b['top']) > 2:
        issues.append(f'首行位置移了 {a["top"]:.0f} → {b["top"]:.0f}')
    if abs(a['bottom'] - b['bottom']) > 2:
        issues.append(f'末行位置移了 {a["bottom"]:.0f} → {b["bottom"]:.0f}')
    if a['lines'] != b['lines']:
        issues.append(f'行数变了：{a["lines"]} → {b["lines"]}')
    return finish('layout', issues, before=a, after=b)


# ---------- tracker ----------

VALID = {'待投递', '已投递', '筛选中', '测评/笔试', '面试中', 'offer', '已终止'}


def check_tracker():
    path = p(CFG.get('tracker')) or os.path.join(DATA_DIR, '投递台账.md')
    if not os.path.exists(path):
        return finish('tracker', [], note='台账还没建，第一次投递时生成')
    text = open(path, encoding='utf-8-sig').read()
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if not cells or not cells[0].isdigit():
            continue
        rows.append((cells + [''] * 11)[:11])

    issues = []
    seen = set()
    for r in rows:
        if r[0] in seen:
            issues.append(f'序号重复：{r[0]}')
        seen.add(r[0])
        if r[8] not in VALID:
            issues.append(f'第 {r[0]} 条状态「{r[8]}」不认识')
        if not r[1] or not r[2]:
            issues.append(f'第 {r[0]} 条缺公司或岗位')
        if len(r) < 11:
            issues.append(f'第 {r[0]} 条列数不够，表格可能被打散')

    order = [int(r[0]) for r in rows]
    if order != sorted(order):
        issues.append('序号不是升序，表格可能被手改过')
    return finish('tracker', issues, rows=len(rows), path=path)


# ---------- jobs ----------

def check_jobs():
    cache = p(CFG.get('job_cache')) or os.path.join(DATA_DIR, 'data', 'jobs.json')
    if not os.path.exists(cache):
        return finish('jobs', ['岗位缓存还没同步，先跑 node scripts/sync_jobs.cjs --only=jobs'])
    try:
        d = json.load(open(cache, encoding='utf-8-sig'))
    except Exception as e:
        return finish('jobs', ['岗位缓存读不了：' + str(e)[:80]])
    rows = d.get('rows') or []
    issues = []
    if not rows:
        issues.append('岗位缓存是空的，同步可能失败了')
    if not d.get('translated'):
        issues.append('缓存还没翻译（城市/学历还是 optXXX 内部 ID），筛岗会一条都筛不出来。'
                      '重跑一次 setup.py 的同步，或单独跑 python scripts/translate_jobs.py')
    stale = {'届次要求', '学历要求', '工作城市'} - set(rows[0].keys() if rows else [])
    if stale:
        issues.append('缓存里缺字段：' + '、'.join(sorted(stale)) + '，同步版本太旧，重跑一次')
    return finish('jobs', issues, rows=len(rows), synced_at=d.get('syncedAt'))


# ---------- draft（用户排完版后的硬伤检查） ----------

PLACEHOLDERS = ['李晓明', '某大学', '某公司', '某咨询', '某某', 'zhangsan',
                '13800138000', '请填写', 'XXX', '示例']


def check_draft(path):
    if not os.path.exists(path):
        return finish('draft', [f'找不到文件：{path}'])
    import re, zipfile
    try:
        x = zipfile.ZipFile(path).read('word/document.xml').decode('utf-8', 'ignore')
    except Exception as e:
        return finish('draft', ['docx 打不开：' + str(e)[:80]])

    paras = []
    for p in re.findall(r'<w:p\b[^>]*>.*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p))
        paras.append(t)
    full = ''.join(paras)
    flat = re.sub(r'\s', '', full)

    issues = []
    left = [w for w in PLACEHOLDERS if w in flat]
    if left:
        issues.append('还有模板里的示例内容没换掉：' + '、'.join(left) + '——这些会被 HR 看到')
    if not flat:
        issues.append('文档里一个字都没有')
    empty = sum(1 for t in paras if not t.strip())
    if empty > 6:
        issues.append(f'有 {empty} 个空段落，排完版后建议删掉')
    return finish('draft', issues, chars=len(flat), paragraphs=len(paras),
                  placeholders_left=left)


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    cmd = argv[0]
    if cmd == 'profile':
        return check_profile()
    if cmd == 'resume':
        return check_resume()
    if cmd == 'layout':
        if len(argv) < 2:
            sys.exit('用法：verify.py layout <微调后的.pdf>')
        return check_layout(argv[1])
    if cmd == 'tracker':
        return check_tracker()
    if cmd == 'jobs':
        return check_jobs()
    if cmd == 'draft':
        if len(argv) < 2:
            sys.exit('用法：verify.py draft <要检查的.docx>')
        return check_draft(argv[1])
    if cmd == 'all':
        return check_all()
    sys.exit(__doc__)


def check_all():
    """一次跑完所有能自动跑的验收

    为什么要有它：单个检查各管一段，模型得自己记住「该跑哪几个、跑完怎么看」。
    跑一遍汇总，判断就变成看一个结论，不同模型给出的结论也一致。
    """
    results = []
    for fn in (check_profile, check_resume, check_tracker, check_jobs):
        # 每个 check_* 直接 print JSON 并返回码；这里改成捕获它的输出
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn()
        try:
            results.append(json.loads(buf.getvalue()))
        except ValueError:
            results.append({'check': fn.__name__, 'ok': False,
                            'issues': ['输出不是 JSON']})

    failed = [r for r in results if not r.get('ok')]
    print('=== 验收汇总 ===\n')
    for r in results:
        print('%s %s' % ('✓' if r.get('ok') else '✗', r.get('check', '?')))
        for x in (r.get('issues') or [])[:6]:
            print('    · ' + str(x))
    print()
    print('全部通过。' if not failed else '%d 项没过，按上面的问题逐条修。' % len(failed))
    print(json.dumps({'ok': not failed,
                      'checks': [{'check': r.get('check'), 'ok': bool(r.get('ok')),
                                  'issues': r.get('issues') or []} for r in results]},
                     ensure_ascii=False, indent=1))
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
