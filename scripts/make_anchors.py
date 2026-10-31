# -*- coding: utf-8 -*-
"""标定模板：从 PDF 量行容量，从 docx 读槽位，生成锚点表（含字数窗口）
用法: python scripts/make_anchors.py [--capacity 50]
"""
import json, os, re, sys, zipfile
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from pypdf import PdfReader

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CFG, REPO, DATA_DIR, path_of

DOCX = CFG['master_docx']
PDF = CFG['master_pdf']
OUT = CFG['anchors']


def measure_capacity(pdf):
    """从 PDF 满行反推每行能放多少字（去空格口径）"""
    pg = PdfReader(pdf).pages[0]
    H = float(pg.mediabox.height)
    lines = defaultdict(lambda: [1e9, -1e9, 0])

    def visit(t, cm, tm, fd, fs):
        if not t or not t.strip():
            return
        y = round(tm[5] / 4) * 4
        if 0 < y < H:
            n = len(re.sub(r'\s', '', t))
            lo, hi, c = lines[y]
            lines[y] = [min(lo, tm[4]), max(hi, tm[4]), c + n]

    pg.extract_text(visitor_text=visit)
    rows = [v for v in lines.values() if v[2] >= 10]
    if not rows:
        return 50, 0
    L = min(r[0] for r in rows)
    R = max(r[1] for r in rows)
    W = R - L
    full = sorted(v[2] for v in rows if (v[1] - L) / W > 0.95 and v[2] > 30)
    cap = full[int(len(full) * 0.9)] if full else 50
    return cap, round(W)


def read_paragraphs(docx):
    x = zipfile.ZipFile(docx).read('word/document.xml').decode('utf-8', 'ignore')
    x = re.sub(r'<w:proofErr[^>]*/>', '', x)
    out, seen = [], set()
    for p in re.findall(r'<w:p\b[^>]*>.*?</w:p>', x, re.S):
        t = re.sub(r'\s', '', ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p)))
        if t and t not in seen:      # 去重：AlternateContent 存了两份副本
            seen.add(t)
            out.append(t)
    return out


def main():
    cap_arg = None
    if '--capacity' in sys.argv:
        cap_arg = int(sys.argv[sys.argv.index('--capacity') + 1])

    cap, width = measure_capacity(PDF)
    if cap_arg:
        cap = cap_arg
    print(f'每行容量 {cap} 字（可用宽度 {width}pt）')

    DATE = re.compile(r'20\d{2}\.\d{2}\s*-')
    slots = []
    for t in read_paragraphs(DOCX):
        n = len(t)
        if n < 12:
            continue
        is_header = bool(DATE.search(t)) or ('实习' in t and n < 30)
        lines = max(1, -(-n // cap))
        if is_header:
            slots.append(dict(kind='标题行', text=t[:22] + '…', full=t, chars=n, lines=lines,
                              lo=0, hi=cap,
                              note='超过 %d 字会折行，日期被挤到下一行' % cap))
        else:
            lo = (lines - 1) * cap + 12
            hi = lines * cap
            slots.append(dict(kind='正文', text=t[:22] + '…', full=t, chars=n, lines=lines,
                              lo=lo, hi=hi,
                              note='低于 %d 字会缩成 %d 行留白；高于 %d 字会多占一行压到下一块'
                                   % (lo, lines - 1, hi)))

    print(f'槽位 {len(slots)} 个\n')
    print('%-6s%6s%4s   %-12s%s' % ('类型', '字数', '行', '窗口', '开头'))
    print('-' * 60)
    for s in slots:
        print('%-6s%6d%4d   %-12s%s' % (s['kind'], s['chars'], s['lines'],
                                        f"{s['lo']}–{s['hi']}", s['text']))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(dict(capacity=cap, usable_pt=width, source=DOCX, slots=slots),
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n已写入', OUT)


main()
