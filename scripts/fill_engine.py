# -*- coding: utf-8 -*-
"""填充引擎：窗口校验 → Word 原地替换 → 导出复验

用法: python scripts/fill_engine.py <草稿.json> [--out 版名]
草稿.json: [{"old": "原文片段", "new": "替换后文本", "why": "为什么改"}, ...]

产出落在 config.resume_dir/微调版/ 下（docx + pdf 两份）。
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


def out_dir():
    d = (CFG.get('work_dir') or '').replace('/', os.sep)
    if d:
        return d
    if RESUME_DIR:
        return os.path.join(RESUME_DIR, '微调版')
    return os.path.join(DATA_DIR, '微调版')


def nospace(s):
    return ''.join(str(s).split())


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
    for a in argv[1:]:
        if not a.startswith('--'):
            return a
    return os.path.join(TMP, 'draft.json')


def main():
    argv = sys.argv
    draft_path = find_draft(argv)
    out_name = '微调版'
    if '--out' in argv:
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
        return

    os.makedirs(TMP, exist_ok=True)
    tmp_docx = os.path.join(TMP, 'work.docx')
    tmp_pdf = os.path.join(TMP, 'work.pdf')
    shutil.copy2(MASTER_PATH, tmp_docx)
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)

    print('\n=== 执行替换 ===')
    log = apply_with_word(tmp_docx, tmp_pdf, [(e['old'], e['new']) for e in ok_list]).strip()
    for line in log.splitlines():
        if line.strip():
            print('  ' + line.strip())

    if 'ERROR=' in log:
        print('\n导出失败。最常见的原因是没装 Word，或者 Word 的 COM 被系统策略禁用了。')
        return
    if not os.path.exists(tmp_pdf):
        print('  ✗ 未生成 PDF。检查是不是没装 Word，或 Word 弹出了对话框。')
        return

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

    d = out_dir()
    os.makedirs(d, exist_ok=True)
    name = CFG.get('name') or '简历'
    stem = f'{name} - {out_name}'
    out_docx = os.path.join(d, stem + '.docx')
    out_pdf = os.path.join(d, stem + '.pdf')
    shutil.copy2(tmp_docx, out_docx)
    shutil.copy2(tmp_pdf, out_pdf)
    print(f'\n产出：\n  {out_docx}\n  {out_pdf}')


if __name__ == '__main__':
    main()
