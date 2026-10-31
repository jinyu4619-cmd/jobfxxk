# -*- coding: utf-8 -*-
"""岗位筛选：把上万条岗位过滤成几十条，只输出紧凑清单

用法:
  python scripts/filter_jobs.py --city 杭州 --degree 硕士 --year 2027 --top 20
  python scripts/filter_jobs.py --keyword "用户运营,产品运营" --top 20
  python scripts/filter_jobs.py --list-cities        # 先看有哪些城市可选

筛选分三步：
  1. 硬门槛：届次 / 学历 / 城市 / 截止日期
  2. 关键词命中：岗位名与你的方向词比对，算一个粗分
  3. 按分数排序，输出前 N 条精简结果（不含原始长字段）
"""
import argparse, json, os, re, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import CFG, REPO, DATA_DIR, path_of
CACHE = CFG.get('job_cache', os.path.join(DATA_DIR, 'jobs.json')).replace('/', os.sep)


def load():
    if not os.path.exists(CACHE):
        sys.exit(f'岗位缓存不存在：{CACHE}\n先跑：node scripts/sync_jobs.cjs --only=jobs')
    d = json.load(open(CACHE, encoding='utf-8'))
    return d.get('rows', []), d.get('syncedAt', '')


def parse_deadline(s):
    """把「招满即止」「2026-11-30」之类转成可比较的值；返回 None 表示无截止限制"""
    s = (s or '').strip()
    if not s or '招满' in s or '长期' in s:
        return None
    m = re.search(r'(20\d{2})[-./年](\d{1,2})[-./月]?(\d{1,2})?', s)
    if not m:
        return None
    y, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3) or 1)
    try:
        return datetime(y, mo, dd)
    except ValueError:
        return None


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
    req = req or ''
    if not req:
        return True
    return str(year) in req or '不限' in req or '应届' in req


def score(row, kws):
    text = (row.get('岗位列表', '') + ' ' + row.get('企业名称', '') + ' ' +
            row.get('所属行业', '') + ' ' + row.get('独家情报（仅供参考）', ''))
    hits = [k for k in kws if k and k in text]
    return len(hits), hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--city')
    ap.add_argument('--degree', default='硕士')
    ap.add_argument('--year', default='2027')
    ap.add_argument('--keyword', default='', help='逗号分隔，如 用户运营,产品运营')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--list-cities', action='store_true')
    ap.add_argument('--include-expired', action='store_true')
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
    dropped = {'届次': 0, '学历': 0, '城市': 0, '过期': 0}

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
        s, hits = score(r, kws) if kws else (0, [])
        kept.append((s, hits, r))

    kept.sort(key=lambda x: -x[0])
    top = kept[:a.top]

    print(f'缓存时间：{synced}')
    print(f'总 {len(rows)} 条 → 硬门槛筛掉 ' +
          '、'.join(f'{k}{v}' for k, v in dropped.items()) +
          f' → 剩 {len(kept)} 条')
    if kws:
        print(f'关键词：{"、".join(kws)}')
    print()

    out = []
    for i, (s, hits, r) in enumerate(top, 1):
        item = {
            '序号': i,
            '公司': r.get('企业名称', ''),
            '岗位': (r.get('岗位列表', '') or '')[:60],
            '城市': r.get('工作城市', ''),
            '截止': r.get('截止日期', ''),
            '投递地址': r.get('投递地址', ''),
            '命中': hits,
            '情报': (r.get('独家情报（仅供参考）', '') or '')[:40],
        }
        out.append(item)
        print(f'{i:>2}. {item["公司"]}｜{item["岗位"]}')
        print(f'     {item["城市"]}｜截止 {item["截止"]}' +
              (f'｜命中 {"/".join(hits)}' if hits else ''))
        print(f'     {item["投递地址"][:80]}')

    dst = os.path.join(DATA_DIR, 'shortlist.json')
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(f'\n精简清单已存：{dst}')


main()
