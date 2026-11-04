# -*- coding: utf-8 -*-
"""按「每条描述两行且第二行顶满」检查简历 PDF。

以 ⚫ 开头的行开启一条；续行不能是结构行（带起止日期、或板块标题）。
合格 = 恰 2 行且第二行 >= 45 字。
"""
import re
import sys
from pypdf import PdfReader

DATE = re.compile(r'\d{4}\.\d{2}\s*-\s*\d{4}\.\d{2}')
HEADS = ('实习经历', '项目经历', '校园经历', '教育背景', '个人总结', '教育 背景')
MIN_SECOND = 45


def is_struct(l):
    if DATE.search(l):
        return True
    if any(l.startswith(h) or l.replace(' ', '').startswith(h) for h in HEADS):
        return True
    if len(l) <= 12 and not l.startswith('⚫'):
        return True
    return False


def bullets(path):
    t = PdfReader(path).pages[0].extract_text()
    lines = [l.replace(' ', '').strip() for l in t.splitlines()]
    lines = [l for l in lines if len(l) >= 8]
    out, cur = [], None
    for l in lines:
        if l.startswith('⚫'):
            if cur:
                out.append(cur)
            cur = [l]
        elif cur is not None:
            if is_struct(l):
                out.append(cur)
                cur = None
            else:
                cur.append(l)
    if cur:
        out.append(cur)
    return out


def main():
    total_bad = 0
    for path in sys.argv[1:]:
        bs = bullets(path)
        bad = 0
        print('\n===', path.split('\\')[-1], '｜共', len(bs), '条 ===')
        for b in bs:
            n = len(b)
            second = len(b[1]) if n >= 2 else 0
            ok = (n == 2 and second >= MIN_SECOND)
            print(f'  {"✓" if ok else "✗"} {n}行 第二行{second:>2}字  {b[0][1:16]}')
            if not ok:
                bad += 1
        total_bad += bad
        print('  结论：' + ('全部达标' if not bad else f'{bad} 条不达标'))
    print('\n合计不达标：', total_bad)


if __name__ == '__main__':
    main()
