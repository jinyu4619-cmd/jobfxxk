# -*- coding: utf-8 -*-
"""标定模板：从 PDF 量行容量，从 docx 读槽位，生成锚点表（含字数窗口）

用法: python scripts/make_anchors.py [--capacity 50]

容量是从 PDF 里「占满整行」的那些行反推的。以前只看第一页——
两页简历的第一页往往最宽，会算偏，所以现在逐页扫。
"""
import json, os, re, sys, zipfile
from collections import defaultdict
sys.stdout.reconfigure(encoding='utf-8')
from pypdf import PdfReader

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, path_of

DOCX = (CFG.get('master_docx') or '').replace('/', os.sep)
PDF = (CFG.get('master_pdf') or '').replace('/', os.sep)
OUT = path_of('anchors', os.path.join('user', '锚点表.json'))

# 日期区间：2026.07-2026.10 / 2026年7月— / 2026/07 ~
DATE = re.compile(r'20\d{2}\s*[.\-/年]\s*\d{1,2}\s*月?\s*[-–—~至]')


def measure_capacity(pdf):
    """逐页统计满行的字数，取 90 分位作为每行容量

    返回 (容量, 可用宽度, 满行样本数)。样本太少说明简历排版不规则
    （分栏、表格、行宽忽长忽短），微调时要保守。
    """
    rd = PdfReader(pdf)
    counts = []
    width = 0

    for pg in rd.pages:
        H = float(pg.mediabox.height)
        lines = defaultdict(lambda: [1e9, -1e9, 0])

        def visit(text, cm, tm, fd, fs, H=H, lines=lines):
            if not text or not text.strip():
                return
            y = round(tm[5] / 4) * 4
            if 0 < y < H:
                n = len(re.sub(r'\s', '', text))
                lo, hi, c = lines[y]
                lines[y] = [min(lo, tm[4]), max(hi, tm[4]), c + n]

        pg.extract_text(visitor_text=visit)
        rows = [v for v in lines.values() if v[2] >= 10]
        if not rows:
            continue
        L = min(r[0] for r in rows)
        R = max(r[1] for r in rows)
        W = R - L
        width = max(width, W)
        if W <= 0:
            continue
        for v in rows:
            if (v[1] - L) / W > 0.95 and v[2] > 30:
                counts.append(v[2])

    if not counts:
        return 50, round(width), 0
    counts.sort()
    cap = counts[int(len(counts) * 0.9)]
    return cap, round(width), len(counts)


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


def has_textbox(docx):
    try:
        x = zipfile.ZipFile(docx).read('word/document.xml').decode('utf-8', 'ignore')
    except Exception:
        return False
    return '<w:txbxContent>' in x or '<wps:txbx>' in x


def main():
    for k, v, desc in [('master_docx', DOCX, '母版 Word'), ('master_pdf', PDF, '母版 PDF')]:
        if not v:
            sys.exit(f'config.json 里没填 {k}（{desc}）。先跑 python scripts/check_config.py')
        if not os.path.exists(v):
            sys.exit(f'{k}（{desc}）指向的文件不存在：{v}')

    cap_arg = None
    if '--capacity' in sys.argv:
        cap_arg = int(sys.argv[sys.argv.index('--capacity') + 1])

    cap, width, samples = measure_capacity(PDF)
    print(f'自动估到每行 {cap} 字（可用宽度 {width}pt，满行样本 {samples} 条）')

    # 重新标定时沿用上次的值：容量一变，所有字数窗口跟着变，
    # 之前调好的简历就会突然折行或留白。要改就显式给 --capacity。
    prev = None
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT, encoding='utf-8-sig')).get('capacity')
        except Exception:
            prev = None

    if cap_arg:
        cap = cap_arg
        print(f'  → 按你指定的 {cap} 字')
    elif prev:
        if prev != cap:
            print(f'  → 沿用上次标定的 {prev} 字（现在自动算出 {cap}，两者不一致时以你调过的为准）')
        cap = prev
    elif samples < 8:
        print('  ⚠ 满行样本太少（少于 8 条），自动估的不可信，先按 50 字处理。')
        print('    如果导出后发现折行，用 --capacity 手动指定。')
        cap = 50

    if samples < 15 and not prev and not cap_arg:
        print('  ⚠ 满行样本偏少，你的模板行宽不太统一（分栏或表格）。')
        print('    微调时按最保守的那档算，宁可短一点也别折行。')

    if has_textbox(DOCX):
        print('  ⚠ 你的简历文字在文本框里。Word 替换能改到，但下面的槽位表可能读不全，')
        print('    需要人工确认一遍锚点表。')

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
                              note=f'超过 {cap} 字会折行，日期被挤到下一行'))
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

    os.makedirs(os.path.dirname(OUT) or '.', exist_ok=True)
    json.dump(dict(capacity=cap, usable_pt=width, samples=samples, source=DOCX, slots=slots),
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n已写入', OUT)


if __name__ == '__main__':
    main()
