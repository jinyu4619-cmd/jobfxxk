# -*- coding: utf-8 -*-
"""投递台账：追加记录、更新状态、重算统计

用法:
  # 新增（投递成功后）
  python scripts/write_tracker.py --company "某公司" --position "用户运营管培生" \
      --city 杭州 --resume "微调版" --link "https://..." [--referral XXX] [--referral-from 用户]

  # 建草稿（阶段 5 定完志愿、还没投）
  python scripts/write_tracker.py --company "某公司" --position "产品运营" --status 待投递

  # 投出去之后把草稿改成已投递
  python scripts/write_tracker.py --update 3 --status 已投递

  # 只看，不写
  python scripts/write_tracker.py --list

台账文件路径取自 config.tracker。台账由本脚本整表维护，
你手写在表格之外的内容（备注、链接）会原样保留。
"""
import argparse, os, re, sys
from datetime import date
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

COLUMNS = ['#', '公司', '岗位', '城市', '投递日期', '简历版本',
           '内推码', '内推码来源', '状态', '下一步', '链接']

PENDING = {'待投递'}
ACTIVE = {'已投递', '筛选中', '测评/笔试', '面试中'}
OFFER = {'offer'}
CLOSED = {'已终止'}
ALL_STATUS = PENDING | ACTIVE | OFFER | CLOSED

SECTION = '## 投递记录'
STATS_PREFIX = '> 最后更新'

# ---- 总表模式 ----
# 用户 2026-11-04 明确「总表是唯一权威」。总表的表结构跟台账不一样：
# 7 列、按投递日期倒序、顶部还有「一、当前统计」，所以不能拿台账那套直接写。
MASTER_SECTION = '## 三、在流程中的全部记录'
MASTER_COLUMNS = ['#', '公司', '岗位', '地点', '投递日期', '状态', '备注']
MASTER_STAT_LABELS = {'累计投递': '累计投递（实际提交过）', '在流程中': '仍在流程中'}


def is_master(text):
    return any(l.strip().startswith(MASTER_SECTION) for l in text.splitlines())


def _master_bounds(lines):
    head = next((i for i, l in enumerate(lines) if l.strip().startswith(MASTER_SECTION)), None)
    if head is None:
        return None, None
    start = next((i for i in range(head + 1, len(lines)) if lines[i].lstrip().startswith('|')), None)
    if start is None:
        return head, None
    end = start
    while end < len(lines) and lines[end].lstrip().startswith('|'):
        end += 1
    return start, end


def _master_rows(lines, start, end):
    rows = []
    for i in range(start, end):
        cells = [c.strip() for c in lines[i].strip().strip('|').split('|')]
        if not cells or cells[0] in ('#', '') or is_separator(cells):
            continue
        rows.append((cells + [''] * len(MASTER_COLUMNS))[:len(MASTER_COLUMNS)])
    return rows


def _master_bump(lines, key, delta):
    """只按增量改顶部统计的数字，不重算——总表里有些历史条目不在表内，重算会越算越小"""
    label = MASTER_STAT_LABELS[key]
    for i, l in enumerate(lines):
        if l.strip().startswith('|') and label in l:
            m = re.search(r'\*\*(\d+)\*\*', l)
            if m:
                lines[i] = l[:m.start(1)] + str(int(m.group(1)) + delta) + l[m.end(1):]
            return


def master_save(path, lines):
    open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')


def master_add(path, a):
    lines = open(path, encoding='utf-8-sig').read().splitlines()
    start, end = _master_bounds(lines)
    if start is None or end is None:
        print('总表里找不到「三、在流程中的全部记录」那张表，停手不改文件。')
        sys.exit(1)
    rows = _master_rows(lines, start, end)
    before = len(rows)
    remark = f'简历：{a.resume}' + (f'；{a.link}' if a.link else '')
    rows.insert(0, ['', a.company, a.position, a.city or '待确认',
                    date.today().isoformat(), a.status, remark])
    rows = sorted(rows, key=lambda r: r[4], reverse=True)      # 稳定排序：同日期新加的在前

    table = ['| ' + ' | '.join(MASTER_COLUMNS) + ' |',
             '|' + '|'.join(['---'] * len(MASTER_COLUMNS)) + '|']
    for i, r in enumerate(rows, 1):
        r[0] = str(i)
        table.append('| ' + ' | '.join(cell(c) for c in r) + ' |')
    lines[start:end] = table

    if a.status in ACTIVE:
        _master_bump(lines, '累计投递', 1)
        _master_bump(lines, '在流程中', 1)
    elif a.status not in PENDING:
        _master_bump(lines, '累计投递', 1)

    master_save(path, lines)
    return before + 1


def master_list(path):
    lines = open(path, encoding='utf-8-sig').read().splitlines()
    start, end = _master_bounds(lines)
    if start is None or end is None:
        print('总表里找不到那张表。')
        return
    for r in _master_rows(lines, start, end):
        print(f'{r[0]:>3}  {r[1]} · {r[2]}  [{r[5]}]  {r[3]}  {r[4]}')


TEMPLATE = f"""# 投递台账

> 最后更新：{{today}} ｜ 累计投递 0 ｜ 待投递 0 ｜ 在流程中 0 ｜ 已终止 0 ｜ offer 0

{SECTION}

"""


def ledger_path():
    p = (CFG.get('tracker') or '').replace('/', os.sep)
    if p:
        # 兼容旧配置：tracker 指到一个目录时，台账放在目录里
        if os.path.isdir(p) or p.endswith(os.sep):
            return os.path.join(p, '投递台账.md')
        return p
    return os.path.join(DATA_DIR, '投递台账.md')


def cell(v):
    """表格单元格：竖线会把列打散，换行会把行打断"""
    return str(v).replace('|', '｜').replace('\r', ' ').replace('\n', ' ').strip()


def stats_line(rows):
    n = len(rows)
    pending = sum(1 for r in rows if r[8] in PENDING)
    active = sum(1 for r in rows if r[8] in ACTIVE)
    offers = sum(1 for r in rows if r[8] in OFFER)
    closed = sum(1 for r in rows if r[8] in CLOSED)
    unknown = n - pending - active - offers - closed
    if unknown:
        closed = closed + unknown          # 兜底：认不出的状态算已终止，保证分项加起来等于总数
    return (f'{STATS_PREFIX}：{date.today().isoformat()} ｜ 累计投递 {n - pending} ｜ '
            f'待投递 {pending} ｜ 在流程中 {active} ｜ 已终止 {closed} ｜ offer {offers}')


def is_separator(cells):
    return all(re.fullmatch(r':?-{2,}:?', c) for c in cells if c)


def read_rows(text):
    """读出所有数据行。表头、分隔行、空行都跳过"""
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if not cells or is_separator(cells):
            continue
        if not cells[0].isdigit():
            continue
        cells = (cells + [''] * len(COLUMNS))[:len(COLUMNS)]
        rows.append(cells)
    return rows


def render_table(rows):
    out = ['| ' + ' | '.join(COLUMNS) + ' |',
           '|' + '|'.join(['---'] * len(COLUMNS)) + '|']
    for r in rows:
        out.append('| ' + ' | '.join(cell(c) for c in r) + ' |')
    return out


def splice(text, rows):
    """只替换投递记录那张表和统计行，表格之外的文字原样保留"""
    lines = text.splitlines()

    for i, l in enumerate(lines):
        if l.startswith(STATS_PREFIX):
            lines[i] = stats_line(rows)
            break

    head = next((i for i, l in enumerate(lines) if l.strip() == SECTION), None)
    if head is None:
        # 文件里没有这一节：追加到末尾，不破坏已有内容
        if lines and lines[-1].strip():
            lines.append('')
        lines += [SECTION, ''] + render_table(rows)
        return '\n'.join(lines) + '\n'

    start = next((i for i in range(head + 1, len(lines))
                  if lines[i].lstrip().startswith('|')), None)
    if start is None:
        lines[head + 1:head + 1] = [''] + render_table(rows)
        return '\n'.join(lines) + '\n'

    end = start
    while end < len(lines) and lines[end].lstrip().startswith('|'):
        end += 1
    lines[start:end] = render_table(rows)
    return '\n'.join(lines) + '\n'


def save(path, rows):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    if os.path.exists(path):
        text = open(path, encoding='utf-8-sig').read()
        if not any(l.startswith(STATS_PREFIX) for l in text.splitlines()):
            text = stats_line(rows) + '\n\n' + text
    else:
        text = TEMPLATE.format(today=date.today().isoformat())
    open(path, 'w', encoding='utf-8').write(splice(text, rows))


def find(rows, idx):
    for i, r in enumerate(rows):
        if r[0] == str(idx):
            return i
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--company')
    ap.add_argument('--position')
    ap.add_argument('--city', default='')
    ap.add_argument('--resume', default='微调版')
    ap.add_argument('--link', default='')
    ap.add_argument('--referral', default='')
    ap.add_argument('--referral-from', dest='referral_from', default='')
    ap.add_argument('--status', default='已投递')
    ap.add_argument('--next', dest='next_step', default='')
    ap.add_argument('--update', type=int, help='更新第 N 条的状态')
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()

    path = ledger_path()
    text = open(path, encoding='utf-8-sig').read() if os.path.exists(path) else ''
    master = bool(text) and is_master(text)

    if a.list and master:
        master_list(path)
        return

    rows = read_rows(text)

    if a.list:
        if not rows:
            print('台账还是空的。')
            return
        for r in rows:
            print(f'{r[0]:>3}  {r[1]} · {r[2]}  [{r[8]}]  {r[3]}  {r[4]}')
        print('\n' + stats_line(rows).lstrip('> '))
        return

    if a.update:
        i = find(rows, a.update)
        if i is None:
            print(f'台账里没有第 {a.update} 条。用 --list 看看现有编号。')
            sys.exit(1)
        if a.status:
            if a.status not in ALL_STATUS:
                print(f'状态「{a.status}」不认识。可选：{" / ".join(sorted(ALL_STATUS))}')
                sys.exit(1)
            rows[i][8] = a.status
        if a.next_step:
            rows[i][9] = a.next_step
        if a.resume:
            rows[i][5] = a.resume
        if a.link:
            rows[i][10] = a.link
        save(path, rows)
        print(f'已更新第 {a.update} 条：{rows[i][1]} · {rows[i][2]} → {rows[i][8]}')
        print(f'台账：{path}')
        return

    if not a.company or not a.position:
        print('新增记录要给 --company 和 --position；只改状态用 --update N --status X。')
        sys.exit(1)
    if a.status not in ALL_STATUS:
        print(f'状态「{a.status}」不认识。可选：{" / ".join(sorted(ALL_STATUS))}')
        sys.exit(1)

    if master:
        n = master_add(path, a)
        print(f'已写入总表：{a.company} · {a.position}（{a.status}），现共 {n} 条')
        print(f'总表：{path}')
        return

    idx = max((int(r[0]) for r in rows if r[0].isdigit()), default=0) + 1
    rows.append([
        str(idx), a.company, a.position, a.city, date.today().isoformat(),
        a.resume, a.referral or '未使用', a.referral_from or '-', a.status,
        a.next_step or ('等通知' if a.status in ACTIVE else '-'), a.link,
    ])
    save(path, rows)

    print(f'已写入第 {idx} 条：{a.company} · {a.position}（{a.status}）')
    print(f'台账：{path}')
    print(stats_line(rows).lstrip('> '))


if __name__ == '__main__':
    main()
