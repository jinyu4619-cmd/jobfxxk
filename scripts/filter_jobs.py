# -*- coding: utf-8 -*-
"""岗位筛选：把上万条岗位过滤成几十条，只输出紧凑清单

用法:
  python scripts/filter_jobs.py --city 杭州 --degree 硕士 --year 2027 --top 20
  python scripts/filter_jobs.py --keyword "用户运营,产品运营" --top 20
  python scripts/filter_jobs.py --list-cities        # 先看有哪些城市可选

筛选分三步：
  1. 硬门槛：届次 / 学历 / 城市 / 截止日期
  2. 关键词命中：岗位名与你的方向词比对，算一个粗分
  3. 按分数排序（同分看截止日期），输出前 N 条精简结果
"""
import argparse, json, os, re, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

CACHE = (CFG.get('job_cache') or os.path.join(DATA_DIR, 'jobs.json')).replace('/', os.sep)
DATE_RE = re.compile(r'(20\d{2})[-./年](\d{1,2})[-./月]?(\d{1,2})?')


def load():
    if not os.path.exists(CACHE):
        sys.exit(f'岗位缓存不存在：{CACHE}\n先跑：node scripts/sync_jobs.cjs --only=jobs')
    d = json.load(open(CACHE, encoding='utf-8-sig'))
    return d.get('rows', []), d.get('syncedAt', '')


def to_date(s):
    """把各种日期写法转成 datetime

    支持：2026-11-30 / 2026年11月 / 1791820800000（毫秒时间戳，飞书日期字段就长这样）
    """
    s = str(s or '').strip()
    if not s:
        return None
    if re.fullmatch(r'\d{13}', s):          # 毫秒时间戳
        try:
            return datetime.fromtimestamp(int(s) / 1000)
        except (ValueError, OSError):
            return None
    if re.fullmatch(r'\d{10}', s):          # 秒时间戳
        try:
            return datetime.fromtimestamp(int(s))
        except (ValueError, OSError):
            return None
    m = DATE_RE.search(s)
    if not m:
        return None
    y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return datetime(y, mo, dd)
    except ValueError:
        return None


def parse_deadline(s):
    """截止日期；「招满即止」「长期」返回 None 表示无明确死线"""
    s = (s or '').strip()
    if not s or '招满' in s or '长期' in s:
        return None
    return to_date(s)


def degree_ok(req, mine):
    """学历要求是否覆盖我的学历。
    「本科 / 硕士 / 博士」表示本科及以上都行，所以看的是最低要求，不是最高。
    """
    req = req or ''
    if not req or '不限' in req:
        return True
    order = ['大专', '本科', '硕士', '博士']
    levels = [x for x in order if x in req]
    if not levels:
        return True
    lowest = min(levels, key=lambda x: order.index(x))
    if mine not in order:
        return True
    return order.index(mine) >= order.index(lowest)


def year_ok(req, year):
    """届次匹配：把要求里的年份全部取出来再判断。

    只看字面量「2027」会误杀「26届/27届」，那是覆盖 2027 届的。
    """
    req = (req or '').strip()
    if not req:
        return True
    if '不限' in req or '应届' in req:
        return True
    years = {int(y) for y in re.findall(r'(20\d{2})', req)}
    if not years:
        years = {2000 + int(y) for y in re.findall(r'(?<!\d)(\d{2})(?=\s*届)', req)}
    return not years or int(year) in years


def stale_note(row, now):
    """数据新鲜度提示。字段缺失就跳过，不硬判。"""
    notes = []
    upd = to_date(row.get('更新日期', ''))
    if upd and (now - upd).days > 30:
        notes.append(f'数据{(now - upd).days}天没更新')
    if '招满' in (row.get('截止日期', '') or ''):
        start = to_date(row.get('开始日期', ''))
        if start and (now - start).days > 60:
            notes.append(f'招满即止，已开始{(now - start).days}天，可能早关了')
    return ' / '.join(notes)


def score(row, kws):
    text = (row.get('岗位列表', '') + ' ' + row.get('企业名称', '') + ' ' +
            row.get('所属行业', '') + ' ' + row.get('独家情报（仅供参考）', ''))
    hits = [k for k in kws if k and k in text]
    return len(hits), hits


def deadline_key(dl):
    """同分时按截止日期升序，没死线的排最后"""
    return dl or datetime.max


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--city')
    ap.add_argument('--degree', default='硕士')
    ap.add_argument('--year', default='2027')
    ap.add_argument('--keyword', default='', help='逗号分隔，如 用户运营,产品运营')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--list-cities', action='store_true')
    ap.add_argument('--include-expired', action='store_true')
    ap.add_argument('--max-stale', type=int, default=0,
                    help='更新日期超过 N 天的直接丢掉；0 = 只标注不丢')
    a = ap.parse_args()

    rows, synced = load()

    if a.list_cities:
        from collections import Counter
        c = Counter()
        for r in rows:
            for city in re.split(r'[/、,\s]+', r.get('工作城市', '') or ''):
                if city:
                    c[city] += 1
        for city, n in c.most_common(30):
            print(f'{city}\t{n}')
        return

    kws = [k.strip() for k in a.keyword.split(',') if k.strip()]
    now = datetime.now()
    dropped = {'届次': 0, '学历': 0, '城市': 0, '过期': 0, '陈旧': 0}

    kept = []
    for r in rows:
        if not year_ok(r.get('届次要求', ''), a.year):
            dropped['届次'] += 1
            continue
        if not degree_ok(r.get('学历要求', ''), a.degree):
            dropped['学历'] += 1
            continue
        if a.city and a.city not in (r.get('工作城市', '') or ''):
            dropped['城市'] += 1
            continue
        dl = parse_deadline(r.get('截止日期', ''))
        if dl and dl < now and not a.include_expired:
            dropped['过期'] += 1
            continue
        # 大部分岗位没有截止日期，只能看「更新日期」判断还开不开着
        if a.max_stale:
            upd = to_date(r.get('更新日期', ''))
            if upd and (now - upd).days > a.max_stale:
                dropped['陈旧'] += 1
                continue
        s, hits = score(r, kws) if kws else (0, [])
        kept.append((s, hits, r, dl))

    kept.sort(key=lambda x: (-x[0], deadline_key(x[3])))
    top = kept[:a.top]

    print(f'缓存时间：{synced}')
    print(f'总 {len(rows)} 条 → 硬门槛筛掉 ' +
          '、'.join(f'{k}{v}' for k, v in dropped.items()) +
          f' → 剩 {len(kept)} 条')
    if kws:
        print(f'关键词：{"、".join(kws)}')

    # 零命中必须明说，否则模型会把「随便几条」当成匹配结果往下走
    best = kept[0][0] if kept else 0
    if kws and best == 0:
        print('\n⚠ 没有一条命中方向词。下面这些只是过了硬门槛，')
        print('  不代表适合你。换个说法再筛，或者去掉关键词先看看这个城市有什么。')
    print()

    out = []
    for i, (s, hits, r, dl) in enumerate(top, 1):
        item = {
            '序号': i,
            '公司': r.get('企业名称', ''),
            '岗位': (r.get('岗位列表', '') or '')[:60],
            '城市': r.get('工作城市', ''),
            '截止': r.get('截止日期', ''),
            '投递地址': r.get('投递地址', ''),
            '命中': hits,
            '情报': (r.get('独家情报（仅供参考）', '') or '')[:40],
            '提示': stale_note(r, now),
        }
        out.append(item)
        print(f'{i:>2}. {item["公司"]}｜{item["岗位"]}')
        print(f'     {item["城市"]}｜截止 {item["截止"]}' +
              (f'｜命中 {"/".join(hits)}' if hits else ''))
        if item['提示']:
            print(f'     ⚠ {item["提示"]}')
        print(f'     {item["投递地址"][:80]}')

    dst = os.path.join(DATA_DIR, 'shortlist.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n精简清单已存：{dst}')
    if kws and best == 0:
        print('注意：清单里的条目一条都没命中关键词，别当成匹配结果用。')


if __name__ == '__main__':
    main()
