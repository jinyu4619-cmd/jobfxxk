# -*- coding: utf-8 -*-
"""投递台账：每次投递成功后追加一行，并重算统计

用法:
  python scripts/write_tracker.py --company "某公司" --position "用户运营管培生" \
      --city 杭州 --resume "微调版" --link "https://..." [--referral XXX] [--referral-from 用户]

台账位置：与 config.tracker 同目录下的「投递台账.md」，不会动你原来那张表。
"""
import argparse, json, os, re, sys
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CFG, REPO, DATA_DIR, path_of

TRACKER_CFG = CFG.get('tracker', '').replace('/', os.sep)
DIR = os.path.dirname(TRACKER_CFG) if TRACKER_CFG else ROOT
LEDGER = os.path.join(DIR, '投递台账.md')

HEADER = """# 投递台账

> 最后更新：{today} ｜ 累计投递 {n} ｜ 在流程中 {active} ｜ 已终止 {closed} ｜ offer {offers}

## 投递记录

| # | 公司 | 岗位 | 城市 | 投递日期 | 简历版本 | 内推码 | 内推码来源 | 状态 | 下一步 |
|---|---|---|---|---|---|---|---|---|---|
"""

ACTIVE = {'已投递', '筛选中', '测评/笔试', '面试中'}
CLOSED = {'已终止'}


def read_rows(text):
    rows = []
    for line in text.splitlines():
        if line.startswith('|') and not re.match(r'^\|\s*[-:# ]', line):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) >= 10 and cells[0].isdigit():
                rows.append(cells)
    return rows


def write(rows):
    n = len(rows)
    active = sum(1 for r in rows if r[8] in ACTIVE)
    closed = sum(1 for r in rows if r[8] in CLOSED)
    offers = sum(1 for r in rows if r[8] == 'offer')
    body = HEADER.format(today=date.today().isoformat(), n=n, active=active, closed=closed, offers=offers)
    for r in rows:
        body += '| ' + ' | '.join(r) + ' |\n'
    open(LEDGER, 'w', encoding='utf-8').write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--company', required=True)
    ap.add_argument('--position', required=True)
    ap.add_argument('--city', default='')
    ap.add_argument('--resume', default='微调版')
    ap.add_argument('--link', default='')
    ap.add_argument('--referral', default='')
    ap.add_argument('--referral-from', default='')
    ap.add_argument('--status', default='已投递')
    ap.add_argument('--next', dest='next_step', default='等通知')
    a = ap.parse_args()

    os.makedirs(DIR, exist_ok=True)
    text = open(LEDGER, encoding='utf-8').read() if os.path.exists(LEDGER) else HEADER.format(
        today=date.today().isoformat(), n=0, active=0, closed=0, offers=0)
    rows = read_rows(text)

    idx = (max(int(r[0]) for r in rows) + 1) if rows else 1
    rows.append([
        str(idx), a.company, a.position, a.city, date.today().isoformat(),
        a.resume, a.referral or '未使用', a.referral_from or '-', a.status, a.next_step,
    ])
    write(rows)

    print(f'已写入第 {idx} 条：{a.company} · {a.position}')
    print(f'台账：{LEDGER}')
    n = len(rows)
    active = sum(1 for r in rows if r[8] in ACTIVE)
    print(f'累计投递 {n} ｜ 在流程中 {active}')


main()
