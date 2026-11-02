# -*- coding: utf-8 -*-
"""填充引擎：窗口校验 → 事实与说法校验 → Word 原地替换 → 导出复验

用法: python scripts/fill_engine.py <草稿.json> [--out 版名] [--allow 词]
草稿.json: [{"old": "原文片段", "new": "替换后文本", "why": "为什么改"}, ...]

三道关的顺序：字数窗口（本文件） → 数字/日期/奖项是否有出处 → 禁用夸大词（lint_draft.py）。
三关全过才动 Word，任何一关不过都不产出文件——不生成一版没验过的简历。

产出直接进简历库：docx 进 <resume_dir>/docx/，pdf 进 <resume_dir>/pdf/，文件名带版本名。
中间文件放数据目录的 tmp/ 里，不落在仓库里，也不落会话临时目录。
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')
from pypdf import PdfReader

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR


def need(key, desc):
    v = (CFG.get(key) or '').replace('/', os.sep)
    if not v:
        print(f'config.json 里没填 {key}（{desc}）。先跑 python scripts/setup.py --check 看看缺什么。')
        sys.exit(2)
    if not os.path.exists(v):
        print(f'{key}（{desc}）指向的文件不存在：{v}')
        sys.exit(2)
    return v


MASTER = (CFG.get('master_docx') or '').replace('/', os.sep)
BASE_PDF = (CFG.get('master_pdf') or '').replace('/', os.sep)
ANCHOR = (CFG.get('anchors') or '').replace('/', os.sep)
RESUME_DIR = (CFG.get('resume_dir') or '').replace('/', os.sep)
TMP = os.path.join(DATA_DIR, 'tmp')


def out_dirs():
    """产出直接进简历库的 docx/ 与 pdf/，不再单开「微调版」目录。
    每个岗位一版，同一个版本名在两处一一对应。"""
    base = (CFG.get('work_dir') or '').replace('/', os.sep) or RESUME_DIR or DATA_DIR
    return os.path.join(base, 'docx'), os.path.join(base, 'pdf')


def nospace(s):
    return ''.join(str(s).split())


def docx_raw_texts(docx):
    """把 docx 里的段落原文取出来，**保留原有空格**

    为什么不能用去掉空格的版本：Word 的查找是字面匹配。文档里
    「某大学 学生会  宣传部部长」如果被我们抹成「某大学学生会宣传部部长」，
    这串字在文件里根本不存在，查找必然失败。
    """
    import re, zipfile
    x = zipfile.ZipFile(docx).read('word/document.xml').decode('utf-8', 'ignore')
    out = []
    for p in re.findall(r'<w:p\b[^>]*>.*?</w:p>', x, re.S):
        t = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p))
        if t.strip():
            out.append(t)
    return out


def resolve_old(raws, old):
    """在一段真实文本里，找到和 old 去空格后等价的那一段（含它原本的空格）

    例：old =「某大学学生会宣传部部长」→ 真实文本里是「某大学学生会 宣传部部长」
    返回真实的那一段，供 Word 精确查找。
    """
    target = nospace(old)
    if not target:
        return None
    for raw in raws:
        idx = [i for i, ch in enumerate(raw) if not ch.isspace()]
        flat = ''.join(raw[i] for i in idx)
        k = flat.find(target)
        if k < 0:
            continue
        start = idx[k]
        end = idx[k + len(target) - 1] + 1
        return raw[start:end]
    return None


def load_anchors():
    if not ANCHOR:
        print('config.json 里没填 anchors（锚点表）。先跑 python scripts/make_anchors.py。')
        sys.exit(2)
    if not os.path.exists(ANCHOR):
        print(f'锚点表还没生成：{ANCHOR}\n先跑 python scripts/make_anchors.py。')
        sys.exit(2)
    a = json.load(open(ANCHOR, encoding='utf-8-sig'))
    for s in a.get('slots', []):
        s['head'] = s.get('full') or s['text'].rstrip('…')
    return a


def check(anchors, old, new):
    """按「替换后整个槽位的新长度」判断，而不是替换片段的长度"""
    for s in anchors['slots']:
        head = s['head']
        if not head:
            continue
        if nospace(old) not in head and head not in nospace(old):
            continue
        result = head.replace(nospace(old), nospace(new))
        n = len(result)
        return (s['lo'] <= n <= s['hi']), n, s['lo'], s['hi'], s
    return None, len(nospace(new)), None, None, None


def ps_escape(s):
    """PowerShell 双引号字符串里的危险字符"""
    return (str(s).replace('`', '``').replace('"', '`"')
            .replace('\r', ' ').replace('\n', ' '))


def apply_with_word(docx, pdf, pairs):
    """Word COM 逐对替换（含文本框），然后导出 PDF

    只操作自己打开的那个 Word 实例，结束时在 finally 里 Quit。
    绝不按进程名批量杀 WORD——用户可能开着没保存的文档。
    """
    ps = ['''
$ErrorActionPreference = "Stop"
$w = $null; $d = $null
try {
  $w = New-Object -ComObject Word.Application
  $w.Visible = $false
  $w.DisplayAlerts = 0
  $d = $w.Documents.Open("%s")
''' % ps_escape(docx)]
    for old, new in pairs:
        ps.append('''
  foreach ($story in $d.StoryRanges) {
    $r = $story
    do {
      $f = $r.Find
      $f.ClearFormatting(); $f.Replacement.ClearFormatting()
      $f.Forward = $true; $f.Wrap = 0; $f.Format = $false
      $null = $f.Execute("%s", $false, $false, $false, $false, $false, $true, 0, $false, "%s", 2)
      $r = $r.NextStoryRange
    } while ($r -ne $null)
  }
''' % (ps_escape(old), ps_escape(new)))
    ps.append('''
  $d.Save()
  $d.ExportAsFixedFormat("%s", 17)
  $pages = $d.ComputeStatistics(2)
  Write-Output "PAGES=$pages"
}
catch {
  Write-Output ("ERROR=" + $_.Exception.Message)
}
finally {
  if ($d -ne $null) { try { $d.Close(0) } catch {} }
  if ($w -ne $null) { try { $w.Quit() } catch {} }
}
''' % ps_escape(pdf))

    os.makedirs(TMP, exist_ok=True)
    f = os.path.join(TMP, '_apply.ps1')
    open(f, 'w', encoding='utf-8-sig').write('\n'.join(ps))
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', f],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return 'ERROR=Word 超过 180 秒没响应，已放弃。可能有对话框挡住了，或者文档太大。'
    return (r.stdout or '') + (r.stderr or '')


def measure(pdf):
    rd = PdfReader(pdf)
    pg = rd.pages[0]
    H = float(pg.mediabox.height)
    ys = []
    pg.extract_text(visitor_text=lambda t, cm, tm, fd, fs:
                    ys.append(round(tm[5], 1)) if t and t.strip() and 20 < tm[5] < H else None)
    return dict(pages=len(rd.pages),
                top=max(ys) if ys else 0,
                bottom=min(ys) if ys else 0,
                lines=len(set(round(y) for y in ys)))


def find_draft(argv):
    """第一个不是选项、也不是选项取值的参数，就是草稿路径"""
    skip = False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a in ('--out', '--allow'):
            skip = True
            continue
        if not a.startswith('--'):
            return a
    return os.path.join(TMP, 'draft.json')


def find_allow(argv):
    """--allow 可以重复出现，收集它的所有取值"""
    out = []
    for i, a in enumerate(argv):
        if a == '--allow' and i + 1 < len(argv):
            out.append(argv[i + 1])
    return out


def run_lint(edits, out_name, allow):
    """把将写入的改动交给 lint_draft.py 过事实关和说法关

    只校验窗口内、确实会被写进简历的那些条目——被窗口拒掉的改动不会落盘，
    不该拦着整份草稿。
    """
    os.makedirs(TMP, exist_ok=True)
    p = os.path.join(TMP, '_lint_input.json')
    json.dump(edits, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    cmd = [sys.executable, os.path.join(HERE, 'lint_draft.py'), p, '--record', out_name]
    for a in allow:
        cmd += ['--allow', a]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=120)
    except subprocess.TimeoutExpired:
        return '校验超时，已放弃。', False
    return (r.stdout or '') + (r.stderr or ''), r.returncode == 0


def main():
    argv = sys.argv
    draft_path = find_draft(argv)
    # 每个岗位一版，必须给出版本名（公司+岗位），否则会互相覆盖
    if '--out' not in argv:
        print('要给 --out 指定这一版的名字，例如 --out "Babycare 线上渠道运营"。')
        print('不加的话，第二家公司会把第一家的文件直接覆盖掉。')
        sys.exit(2)
    out_name = argv[argv.index('--out') + 1]

    MASTER_PATH = need('master_docx', '母版 Word')
    BASE_PDF_PATH = need('master_pdf', '母版 PDF')
    if not os.path.exists(draft_path):
        print(f'找不到草稿文件：{draft_path}')
        sys.exit(2)

    anchors = load_anchors()
    edits = json.load(open(draft_path, encoding='utf-8-sig'))

    print(f'=== 窗口校验（每行 {anchors.get("capacity", "?")} 字）===\n')
    ok_list, bad_list = [], []
    for e in edits:
        ok, n, lo, hi, s = check(anchors, e['old'], e['new'])
        tag = '✓' if ok else ('✗' if ok is False else '?')
        rng = f'{lo}–{hi}' if lo is not None else '未匹配'
        print(f'  {tag} {n:>3} 字  窗口 {rng:<8}  {e["old"][:20]}…')
        (ok_list if ok else bad_list).append(e)

    print(f'\n通过 {len(ok_list)} 条，拒绝 {len(bad_list)} 条')
    if bad_list:
        print('\n以下条目没有写入：')
        for e in bad_list:
            ok, n, lo, hi, s = check(anchors, e['old'], e['new'])
            print(f'  · {e["old"][:24]}… → {n} 字' +
                  (f'，窗口 {lo}–{hi}' if lo is not None else '，未匹配到锚点'))
    if not ok_list:
        print('\n没有可写入的条目，退出。')
        return 1

    print('\n=== 事实与说法校验 ===')
    log, passed = run_lint(ok_list, out_name, find_allow(argv))
    for line in log.splitlines():
        if line.strip():
            print('  ' + line.strip())
    if not passed:
        print('\n校验没过，没有写出任何文件。改草稿重跑；两次都过不了就换回原版，'
              '并告诉用户这条改不好。')
        return 1

    os.makedirs(TMP, exist_ok=True)
    tmp_docx = os.path.join(TMP, 'work.docx')
    tmp_pdf = os.path.join(TMP, 'work.pdf')
    shutil.copy2(MASTER_PATH, tmp_docx)
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)

    print('\n=== 执行替换 ===')
    raws = docx_raw_texts(tmp_docx)
    pairs, unresolved, adjusted = [], [], 0
    for e in ok_list:
        real = resolve_old(raws, e['old'])
        if real is None:
            unresolved.append(e)
            real = e['old']
        elif real != e['old']:
            adjusted += 1
        pairs.append((real, e['new']))
    if adjusted:
        print(f'  {adjusted} 条按文档里的实际空格做了对齐')
    if unresolved:
        print('  以下片段在 docx 里找不到对应的文字，多半跨了段落：')
        for e in unresolved:
            print('    · ' + e['old'][:30])
    log = apply_with_word(tmp_docx, tmp_pdf, pairs).strip()
    for line in log.splitlines():
        if line.strip():
            print('  ' + line.strip())

    if 'ERROR=' in log:
        print('\n导出失败。最常见的原因是没装 Word，或者 Word 的 COM 被系统策略禁用了。')
        return 1
    if not os.path.exists(tmp_pdf):
        print('  ✗ 未生成 PDF。检查是不是没装 Word，或 Word 弹出了对话框。')
        return 1

    txt = ''.join((pg.extract_text() or '') for pg in PdfReader(tmp_pdf).pages)
    txt = ''.join(txt.split())
    missed = [e for e in ok_list if ''.join(e['new'].split()) not in txt]
    if missed:
        print('\n=== 替换未生效 ===')
        for e in missed:
            print(f'  ✗ 没换上：{e["new"][:30]}…')
            print(f'    原文片段在 docx 里可能带空格或标点不一致：{e["old"][:30]}…')
    else:
        print('\n=== 替换全部生效 ===')

    a = measure(BASE_PDF_PATH)
    b = measure(tmp_pdf)
    print('\n=== 版面复验 ===')
    print(f'  页数   {a["pages"]} → {b["pages"]}')
    print(f'  首行 y {a["top"]:.0f} → {b["top"]:.0f}')
    print(f'  末行 y {a["bottom"]:.0f} → {b["bottom"]:.0f}')
    print(f'  行数   {a["lines"]} → {b["lines"]}')
    same = (a['pages'] == b['pages'] and abs(a['top'] - b['top']) <= 2
            and abs(a['bottom'] - b['bottom']) <= 2)
    print('  结论：' + ('版式未变 ✓' if same else '版式有位移 ✗'))

    docx_dir, pdf_dir = out_dirs()
    os.makedirs(docx_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)
    name = CFG.get('name') or '简历'
    stem = f'{name} - {out_name}'
    out_docx = os.path.join(docx_dir, stem + '.docx')
    out_pdf = os.path.join(pdf_dir, stem + '.pdf')
    shutil.copy2(tmp_docx, out_docx)
    shutil.copy2(tmp_pdf, out_pdf)
    print(f'\n产出：\n  {out_docx}\n  {out_pdf}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
