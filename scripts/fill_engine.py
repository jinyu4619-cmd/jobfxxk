# -*- coding: utf-8 -*-
"""填充引擎：窗口校验 → Word 原地替换 → 导出复验
用法: python fill_engine.py <草稿.json>
草稿.json: [{"old": "原文片段", "new": "替换后文本", "why": "为什么改"}, ...]
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')
from pypdf import PdfReader

BASE = r'D:\27秋招'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CFG, REPO, DATA_DIR, path_of
MASTER = CFG['master_docx']
ANCHOR = CFG['anchors']
WORK = os.path.join(BASE, '投递工作流', 'test')
ASCII = r'C:\Users\Administrator\AppData\Local\Claude-3p\local-agent-mode-sessions\96c170ca\00000000\b60ad9ed\outputs\fillwork'
BASE_PDF = CFG['master_pdf']


def nospace(s):
    return ''.join(str(s).split())


def load_anchors():
    a = json.load(open(ANCHOR, encoding='utf-8'))
    # 每条锚点记住开头若干字，用于匹配
    for s in a['slots']:
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


def _kill_word():
    """清掉残留的 WINWORD 进程，避免 COM 调用被卡住"""
    import time
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'WINWORD.EXE'],
                       capture_output=True, timeout=10)
        time.sleep(1)
    except Exception:
        pass


def apply_with_word(docx, pdf, pairs):
    """Word COM 逐对替换（含文本框），然后导出 PDF"""
    ps = ['''
$ErrorActionPreference = "Stop"
$w = New-Object -ComObject Word.Application
$w.Visible = $false
$w.DisplayAlerts = 0
$d = $w.Documents.Open("%s")
''' % docx]
    for old, new in pairs:
        o = old.replace('"', '`"')
        nw = new.replace('"', '`"')
        ps.append(f'''
foreach ($story in $d.StoryRanges) {{
  $r = $story
  do {{
    $f = $r.Find
    $f.ClearFormatting(); $f.Replacement.ClearFormatting()
    $f.Forward = $true; $f.Wrap = 0; $f.Format = $false
    $null = $f.Execute("{o}", $false, $false, $false, $false, $false, $true, 0, $false, "{nw}", 2)
    $r = $r.NextStoryRange
  }} while ($r -ne $null)
}}
''')
    ps.append(f'''
$d.Save()
$d.ExportAsFixedFormat("{pdf}", 17)
$pages = $d.ComputeStatistics(2)
$d.Close(0)
$w.Quit()
Write-Output "PAGES=$pages"
''')
    _kill_word()
    os.makedirs(ASCII, exist_ok=True)
    f = os.path.join(ASCII, '_apply.ps1')
    open(f, 'w', encoding='utf-8-sig').write('\n'.join(ps))
    r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', f],
                       capture_output=True, text=True)
    return (r.stdout or '') + (r.stderr or '')


def measure(pdf):
    pg = PdfReader(pdf).pages[0]
    H = float(pg.mediabox.height)
    ys = []
    pg.extract_text(visitor_text=lambda t, cm, tm, fd, fs:
                    ys.append(round(tm[5], 1)) if t and t.strip() and 20 < tm[5] < H else None)
    return dict(pages=len(PdfReader(pdf).pages),
                top=max(ys) if ys else 0,
                bottom=min(ys) if ys else 0,
                lines=len(set(round(y) for y in ys)))


def main(draft_path, out_name='微调版'):
    anchors = load_anchors()
    edits = json.load(open(draft_path, encoding='utf-8'))

    print(f'=== 窗口校验（每行 {anchors["capacity"]} 字）===\n')
    ok_list, bad_list = [], []
    for e in edits:
        ok, n, lo, hi, s = check(anchors, e['old'], e['new'])
        tag = '✓' if ok else ('✗' if ok is False else '?')
        rng = f'{lo}–{hi}' if lo is not None else '未匹配'
        print(f'  {tag} {n:>3} 字  窗口 {rng:<8}  {e["old"][:20]}…')
        (ok_list if ok else bad_list).append(e)

    print(f'\n通过 {len(ok_list)} 条，拒绝 {len(bad_list)} 条')
    if bad_list:
        print('\n以下条目超出字数窗口，未写入：')
        for e in bad_list:
            ok, n, lo, hi, s = check(anchors, e['old'], e['new'])
            print(f'  · {e["old"][:24]}… → {n} 字' + (f'，窗口 {lo}–{hi}' if lo else '，未匹配锚点'))
    if not ok_list:
        print('\n没有可写入的条目，退出。')
        return

    os.makedirs(WORK, exist_ok=True)
    tmp_docx = os.path.join(ASCII, 'work.docx')
    tmp_pdf = os.path.join(ASCII, 'work.pdf')
    shutil.copy2(MASTER, tmp_docx)
    if os.path.exists(tmp_pdf):
        os.remove(tmp_pdf)

    print('\n=== 执行替换 ===')
    log = apply_with_word(tmp_docx, tmp_pdf, [(e['old'], e['new']) for e in ok_list])
    print('  ' + log.strip().replace('\n', '\n  '))

    if not os.path.exists(tmp_pdf):
        print('  ✗ 未生成 PDF')
        return

    # 逐个确认替换是否真的发生（Word 是精确匹配，可能一条都没换上）
    txt = ''.join((pg.extract_text() or '') for pg in PdfReader(tmp_pdf).pages)
    txt = ''.join(txt.split())
    missed = [e for e in ok_list if ''.join(e['new'].split()) not in txt]
    if missed:
        print('\n=== 替换未生效 ===')
        for e in missed:
            print(f'  ✗ 未找到：{e[new][:30]}…')
            print(f'    原文片段在 docx 里可能带空格或标点不一致：{e[old][:30]}…')
    else:
        print('\n=== 替换全部生效 ===')

    a = measure(BASE_PDF)
    b = measure(tmp_pdf)
    print('\n=== 版面复验 ===')
    print(f'  页数   {a["pages"]} → {b["pages"]}')
    print(f'  首行 y {a["top"]:.0f} → {b["top"]:.0f}')
    print(f'  末行 y {a["bottom"]:.0f} → {b["bottom"]:.0f}')
    print(f'  行数   {a["lines"]} → {b["lines"]}')
    same = (a['pages'] == b['pages'] and abs(a['top']-b['top']) <= 2
            and abs(a['bottom']-b['bottom']) <= 2)
    print('  结论：' + ('版式未变 ✓' if same else '版式有位移 ✗'))

    out_docx = os.path.join(WORK, CFG['name'] + ' 27秋招 - ' + out_name + '.docx')
    out_pdf = os.path.join(WORK, CFG['name'] + ' 27秋招 - ' + out_name + '.pdf')
    shutil.copy2(tmp_docx, out_docx)
    shutil.copy2(tmp_pdf, out_pdf)
    print(f'\n产出：{out_pdf}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ASCII, 'draft.json'))



