# -*- coding: utf-8 -*-
"""推荐岗位：按单个岗位推，不按公司行；每条都带得走的依据

和 filter_jobs.py 的区别：
  filter_jobs —— 把公司那一行当一个整体，看关键词在不在里面，出了清单还得人挑
  recommend   —— 把「岗位列表」拆成一个个岗位，逐条打分，并说清凭什么推它

两条通道：
  1. 直接命中：岗位名里出现了方向关键词，可靠
  2. 能力推导：从已确认的经历里抽出能力，再映射到岗位族。
     这条是推断，输出里会标「由哪段经历推出」，不会和直接命中混在一起

用法:
  python scripts/recommend.py                      # 按档案里的方向推
  python scripts/recommend.py --direction 管培     # 临时换方向
  python scripts/recommend.py --city 深圳 --top 15
  python scripts/recommend.py --json
"""
import argparse, io, json, os, re, sys
from datetime import datetime
from functools import lru_cache
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import CFG, DATA_DIR

USER = os.path.join(DATA_DIR, 'user')
CACHE = (CFG.get('job_cache') or os.path.join(DATA_DIR, 'data', 'jobs.json')).replace('/', os.sep)
TRACKER = (CFG.get('tracker') or '').replace('/', os.sep)

# 岗位库里的岗位名大多是「类别」而不是具体职位：管培生 620 条、运营类 91 条、
# 产品运营类 30 条，但没有一条叫「用户运营」。所以分三级命中——
# 精确（细分职位名）> 类别（运营类这种打包）> 推断（能力映射）。
DIRECTIONS = {
    'user_ops':   dict(name='用户 / 人群运营',
                       exact=['用户运营', '人群运营', '会员运营', '用户增长', '社群运营', '私域运营'],
                       category=['运营类', '运营管理类', '业务运营类', '市场运营类', '产品运营类']),
    'data_ops':   dict(name='数据运营 / 增长',
                       exact=['数据运营', '增长运营', '数据分析', '商业分析', '策略运营', '投放运营'],
                       category=['数据分析类', '数据类', '大数据类', '经营分析']),
    'ai_product': dict(name='AI 产品 / AI 运营',
                       exact=['AI产品', 'AI产品经理', '智能体', 'AI运营', 'AI应用', '大模型'],
                       category=['产品类', '产品经理', 'AI类', '算法类']),
    'content_ops': dict(name='内容 / 新媒体运营',
                        exact=['内容运营', '新媒体运营', '品牌内容', '文案策划', '短视频运营'],
                        category=['品牌类', '市场类', '媒介类', '设计类']),
    'channel_ops': dict(name='渠道 / 电商运营',
                        exact=['渠道运营', '电商运营', '店铺运营', '线上运营', '销售运营', '直播运营'],
                        category=['销售类', '营销类', '电商类', '零售类']),
    'mt':         dict(name='运营管培生',
                       exact=['运营管培生', '管培生', '管理培训生', '储备干部', '培训生'],
                       category=['管培生类', '管培类', '储备干部类']),
}

# 能力 → 岗位族。能力从经历的 bullet 里抽，映射是固定的，可解释、可复现。
CAPABILITIES = [
    dict(cap='用户分层与实验', sig=['用户分层', '分层', '实验', '推荐位选品', '用户兴趣'],
         families=['用户运营', '人群运营', '会员运营', '用户增长', '增长运营']),
    dict(cap='指标体系与数据监控', sig=['数据监控', '数据更新', '周度数据', '报表', '口径', '异常'],
         families=['数据运营', '商业分析', '数据分析', '策略运营']),
    dict(cap='投放与增长策略', sig=['投放策略', '增长信号', '下载量', '转化率', '触达'],
         families=['增长运营', '投放运营', '用户增长', '渠道运营']),
    dict(cap='AI 工具与智能体', sig=['Codex', 'Coze', 'Prompt', '智能体', 'AI工具', '多模态'],
         families=['AI产品', 'AI产品经理', 'AI运营', 'AI应用', '产品经理']),
    dict(cap='内容与文案', sig=['内容运营', '文案', '公众号', '微博', '小红书', '选题', '原创'],
         families=['内容运营', '新媒体运营', '品牌内容', '文案策划']),
    dict(cap='活动策划与执行', sig=['活动', '策划', '现场', '宣发', '场地'],
         families=['活动运营', '市场策划', '品牌运营', '市场专员']),
    dict(cap='流程沉淀与统筹', sig=['SOP', '复盘', '标准化', '统筹', '全流程', 'checklist'],
         families=['管培生', '管理培训生', '储备干部', '运营专员']),
]

DATE_RE = re.compile(r'(20\d{2})[-./年](\d{1,2})[-./月]?(\d{1,2})?')


def to_date(s):
    # 12000 行里日期字符串高度重复（只有几百种），缓存后能把日期解析的开销砍掉大半
    return _to_date(str(s or '').strip())


@lru_cache(maxsize=4096)
def _to_date(s):
    if re.fullmatch(r'\d{13}', s):
        return datetime.fromtimestamp(int(s) / 1000)
    m = DATE_RE.search(s)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return None


def load_json(path, default):
    try:
        return json.load(io.open(path, encoding='utf-8-sig'))
    except Exception:
        return default


def applied_companies():
    """从台账里读出「投过哪家的哪个岗位」

    两个来源：脚本维护的台账，以及用户自己维护的总表（如果有）。
    总表是人工写的，格式不统一，所以只认「序号是数字、第二列像公司名」的行。

    返回 {公司: {岗位, ...}}。岗位为空集表示只知道公司、不知道岗位——
    这种保守处理，整家排掉。按岗位粒度去重是有意义的：一家公司挂 28 个岗位，
    投了其中一个就把另外 27 个一起丢掉，是白白浪费。
    """
    out = {}
    paths = []
    if TRACKER:
        paths.append(TRACKER)
        paths.append(os.path.join(os.path.dirname(TRACKER), '投递跟踪总表.md'))
    for p in dict.fromkeys(paths):
        if not p or not os.path.exists(p):
            continue
        for line in io.open(p, encoding='utf-8-sig'):
            s = line.strip()
            if not s.startswith('|'):
                continue
            cells = [c.strip() for c in s.strip('|').split('|')]
            if len(cells) < 2 or not cells[0].isdigit():
                continue
            comp = cells[1]
            if not comp or comp in ('公司', '公司名称', '---') or set(comp) <= set('-: '):
                continue
            pos = cells[2] if len(cells) > 2 else ''
            if pos in ('岗位', '---') or set(pos) <= set('-: '):
                pos = ''
            out.setdefault(comp, set())
            if pos:
                out[comp].add(pos)
    return out


def split_list(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r'[，,、;；\n]', str(v or '')) if x.strip()]


def exclude_rules():
    """判据卡里的排除线：3–5 条不退让的否决项

    为什么单列：没有它，同样一份岗位库今天筛出 15 条、明天筛出另外 15 条，
    每次都在重新判断同一个问题。写成规则就变成一次判断、永久生效。
    """
    prof = load_json(os.path.join(USER, 'profile.json'), {})
    return split_list((prof.get('intent') or {}).get('exclude'))


def wanted_cities():
    prof = load_json(os.path.join(USER, 'profile.json'), {})
    return split_list((prof.get('intent') or {}).get('cities'))


LIST_DIR = os.path.join(DATA_DIR, 'lists')
HISTORY = os.path.join(LIST_DIR, '已推荐.jsonl')


def load_history():
    """以前推过的（公司, 岗位）。重复推荐是信任杀手，所以要留档。"""
    seen = set()
    if not os.path.exists(HISTORY):
        return seen
    for line in io.open(HISTORY, encoding='utf-8-sig'):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            seen.add((d.get('company', ''), d.get('position', '')))
        except ValueError:
            continue
    return seen


def confidence(item):
    """信息确定度 0–20：这条信息本身有多可信、多久没更新

    只算能从数据里直接看出来的三项，不加任何主观判断。
    投递地址是不是真门户，是今天真实翻过的坑——岗位库里 39 条「产品运营」，
    只有 6 条是真网申页，其余是公众号文章。
    """
    pts, why = 0, []
    link = item.get('link') or ''
    if link and 'mp.weixin' not in link and 'shortapis' not in link:
        pts += 10; why.append('真门户')
    elif link:
        why.append('公众号/短链')
    dl = item.get('deadline') or ''
    if to_date(dl):
        pts += 6; why.append('有明确截止日')
    elif '招满' in dl:
        why.append('招满即止')
    upd = to_date(item.get('updated_at'))
    if upd:
        age = (datetime.now() - upd).days
        if age <= 7:
            pts += 4; why.append('7 天内更新')
        elif age <= 30:
            why.append(f'{age} 天未更新')
    return min(pts, 20), '、'.join(why)


def score_of(item, cities):
    """0–100 分，四个维度都能从数据里算出来

    刻意不加「竞争烈度」「成长性」——岗位库里没有招录比、团队规模这类数据，
    硬打分就是编数字。宁可维度少，也不要一个看起来精确的假分数。
    """
    hit = 60 if item['exact'] else (30 if item['category'] else 15)
    conf, _ = confidence(item)
    city = 0
    if cities:
        city = 10 if any(c in (item.get('city') or '') for c in cities) else 0
    else:
        city = 5
    upd = to_date(item.get('updated_at'))
    fresh = 10 if upd and (datetime.now() - upd).days <= 7 else (
        5 if upd and (datetime.now() - upd).days <= 30 else 0)
    return hit + conf + city + fresh


def tier_of(score):
    if score >= 75:
        return 'P0', '48 小时内投'
    if score >= 60:
        return 'P1', '一周内投'
    if score >= 45:
        return 'P2', '只记录'
    return '', '不进清单'


def capabilities_of(exp_id, text):
    """这段经历支撑得起哪些能力"""
    out = []
    for c in CAPABILITIES:
        if any(s in text for s in c['sig']):
            out.append(c)
    return out


def exp_text(e):
    parts = [e.get('org', ''), e.get('role', '')]
    for b in e.get('bullets') or []:
        parts += [b.get('theme', ''), b.get('action', ''), b.get('method', ''), b.get('result', '')]
    return ' '.join(parts)


def decide_directions(arg):
    """用档案里存的方向；没存就用 directions.py 的推断"""
    if arg:
        return [arg]
    prof = load_json(os.path.join(USER, 'profile.json'), {})
    d = (prof.get('intent') or {}).get('directions') or ''
    ids = [x.strip() for x in str(d).replace('，', ',').split(',') if x.strip()]
    ids = [i for i in ids if i in DIRECTIONS]
    if ids:
        return ids
    # 没存过就按能力最强的那两个
    caps = []
    for e in load_json(os.path.join(USER, 'experience.json'), []):
        if e.get('status') != 'confirmed':
            continue
        caps += capabilities_of(e.get('id'), exp_text(e))
    score = {}
    for c in CAPABILITIES:
        for fam in c['families']:
            for did, dd in DIRECTIONS.items():
                if any(k in fam or fam in k for k in dd['exact']):
                    score[did] = score.get(did, 0) + 1
    return [k for k, _ in sorted(score.items(), key=lambda x: -x[1])[:2]] or ['user_ops']


WEIGHTS = 'v2｜命中 60 + 确定度 20 + 城市 10 + 新鲜 10'


def match_basis(c):
    if c['exact']:
        return '精确：' + '、'.join(c['exact'])
    if c['category']:
        return '类别：' + '、'.join(c['category']) + '（要读 JD）'
    if c['inferred']:
        ev = c['inferred'][0]
        return f'推断：{ev["org"]} 的「{ev["cap"]}」'
    return ''


def write_archive(picked, directions, dropped, dup_rate, stamp):
    """固定列的清单 + 一句话结论，顺手记进历史（下次不再推同样的）

    为什么要留档：不留档就算不出重复率，也没法回溯「三周前为什么推了这条」。
    """
    os.makedirs(LIST_DIR, exist_ok=True)
    path = os.path.join(LIST_DIR, f'岗位清单_{stamp}.md')
    p0 = [c for c in picked if c['tier'] == 'P0']
    lines = [
        f'# 岗位清单 {stamp}',
        '',
        f'> 方向：{"、".join(DIRECTIONS[d]["name"] for d in directions)}｜'
        f'权重 {WEIGHTS}｜与历史重复 {dup_rate * 100:.0f}%',
        f'> 排掉：' + '、'.join(f'{k}{v}' for k, v in dropped.items()),
        '',
        '| # | 优先级 | 公司 | 岗位 | 城市 | 截止 | 投递入口 | 匹配依据 | 确定度 |',
        '|---|---|---|---|---|---|---|---|---|',
    ]
    for i, c in enumerate(picked, 1):
        link = c.get('link') or ''
        link_cell = f'[入口]({link})' if link.startswith('http') else (link or '待找')
        lines.append('| {} | {} | {} | {} | {} | {} | {} | {} | {}/20 |'.format(
            i, c['tier'], c['company'], c['position'], c.get('city') or '',
            c.get('deadline') or '', link_cell, match_basis(c), c['confidence']))
    lines += ['', '## 一句话结论', '']
    if p0:
        top = p0[0]
        lines.append(f'只做一件的话：先投 **{top["company"]}｜{top["position"]}**'
                     f'（{top["score"]} 分，{top["tier_hint"]}）。')
    elif picked:
        lines.append('本轮没有 75 分以上的，按截止日排队即可，不必抢。')
    else:
        lines.append('本轮没有够格的，建议放宽方向或补信息源。')
    lines += ['', '## 待办', '']
    for c in picked[:5]:
        lines.append(f'- [ ] {c["company"]}｜{c["position"]}：核对截止日与限投数后投递')
    io.open(path, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')

    with io.open(HISTORY, 'a', encoding='utf-8') as f:
        for c in picked:
            f.write(json.dumps({'company': c['company'], 'position': c['position'],
                                'date': stamp, 'score': c['score']}, ensure_ascii=False) + '\n')
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--direction', default='', help='临时指定方向：user_ops/data_ops/ai_product/content_ops/channel_ops/mt')
    ap.add_argument('--city', default='')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--max-stale', type=int, default=45)
    ap.add_argument('--same-company', action='store_true',
                    help='公司投过但岗位不同，也纳入推荐（默认排除）')
    ap.add_argument('--no-archive', action='store_true', help='不写清单文件、不记历史')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    data = load_json(CACHE, {})
    rows = data.get('rows') or []
    if not rows:
        sys.exit('岗位缓存是空的，先跑 sync_jobs.cjs 和 translate_jobs.py')

    directions = decide_directions(a.direction)
    exact_keys, cat_keys = [], []
    for d in directions:
        exact_keys += DIRECTIONS[d]['exact']
        cat_keys += DIRECTIONS[d]['category']
    all_keys = exact_keys + cat_keys

    # 经历 → 能力 → 岗位族
    cap_families = {}
    for e in load_json(os.path.join(USER, 'experience.json'), []):
        if e.get('status') != 'confirmed':
            continue
        for c in capabilities_of(e.get('id'), exp_text(e)):
            for fam in c['families']:
                cap_families.setdefault(fam, []).append(
                    dict(cap=c['cap'], exp=e.get('id'), org=e.get('org'), role=e.get('role')))

    applied = applied_companies()
    ex_rules = exclude_rules()
    cities = wanted_cities()
    history = load_history()
    now = datetime.now()
    dropped = {'届次': 0, '学历': 0, '城市': 0, '过期': 0, '陈旧': 0,
               '已投': 0, '排除线': 0, '推过': 0}
    same_company = 0
    exclude_hits = {}

    cands = []
    for r in rows:
        comp = (r.get('企业名称') or '').strip()
        pos_text_all = r.get('岗位列表') or ''

        # 已投：按「公司 + 岗位」判，不再整家一刀切
        if comp in applied:
            known = applied[comp]
            same_pos = any(k and (k in pos_text_all or pos_text_all in k) for k in known)
            if not known or same_pos or not a.same_company:
                if known and not same_pos and not a.same_company:
                    same_company += 1
                dropped['已投'] += 1
                continue

        # 排除线：判据卡里写死的否决项，命中一条就淘汰，并记下是哪条
        hay = ' '.join([comp, pos_text_all, r.get('工作城市') or '', r.get('所属行业') or ''])
        hit = next((x for x in ex_rules if x and x in hay), '')
        if hit:
            dropped['排除线'] += 1
            exclude_hits[hit] = exclude_hits.get(hit, 0) + 1
            continue

        if '2027' not in (r.get('届次要求') or '') and '不限' not in (r.get('届次要求') or ''):
            dropped['届次'] += 1
            continue
        deg = r.get('学历要求') or ''
        if deg and '硕士' not in deg and '不限' not in deg:
            dropped['学历'] += 1
            continue
        if a.city and a.city not in (r.get('工作城市') or ''):
            dropped['城市'] += 1
            continue
        dl = to_date(r.get('截止日期'))
        if dl and dl < now:
            dropped['过期'] += 1
            continue
        upd = to_date(r.get('更新日期'))
        if upd and (now - upd).days > a.max_stale:
            dropped['陈旧'] += 1
            continue

        # 关键：拆成一个个岗位，而不是把整行当一个
        pos_text = r.get('岗位列表') or ''
        positions = [p.strip() for p in re.split(r'[，,、/]', pos_text) if p.strip()]
        if not positions:
            positions = ['（未写明岗位）']

        for pos in positions:
            exact = [k for k in exact_keys if k in pos]
            cat = [k for k in cat_keys if k in pos]
            inferred = []
            # 推断只认「岗位族名出现在岗位名里」，反过来不算——
            # 否则「运营」「AI」这种单字岗位会把所有族都匹配上。
            if len(pos) >= 3:
                for fam, ev in cap_families.items():
                    # 推断也必须落在所选方向里，否则「用户运营」方向会推出 AI 产品岗
                    if fam in pos and any(fam in k or k in fam for k in all_keys):
                        inferred += ev
            if not exact and not cat and not inferred:
                continue
            if (comp, pos) in history:
                dropped['推过'] += 1
                continue
            item = dict(
                company=comp, position=pos, city=r.get('工作城市'), deadline=r.get('截止日期'),
                link=r.get('投递地址'), industry=r.get('所属行业'),
                updated_at=r.get('更新日期'),
                exact=sorted(set(exact)), category=sorted(set(cat)),
                inferred=inferred[:3], total_positions=len(positions))
            item['confidence'], item['confidence_why'] = confidence(item)
            item['score'] = score_of(item, cities)
            item['tier'], item['tier_hint'] = tier_of(item['score'])
            cands.append(item)

    cands.sort(key=lambda x: (-x['score'], -x['confidence'], x['company']))
    seen = set()
    picked = []
    for c in cands:
        if not c['tier']:          # 45 分以下不进清单
            continue
        k = (c['company'], c['position'])
        if k in seen:
            continue
        seen.add(k)
        picked.append(c)
        if len(picked) >= a.top:
            break

    dup_rate = 0.0
    total_seen = len(cands) + dropped['推过']
    if total_seen:
        dup_rate = dropped['推过'] / float(total_seen)

    stamp = datetime.now().strftime('%Y-%m-%d')
    archive = ''
    if not a.no_archive:
        archive = write_archive(picked, directions, dropped, dup_rate, stamp)

    if a.json:
        print(json.dumps({'directions': directions, 'dropped': dropped,
                          'dup_rate': round(dup_rate, 3), 'archive': archive,
                          'exclude': ex_rules, 'items': picked}, ensure_ascii=False, indent=1))
        return

    print(f'方向：{"、".join(DIRECTIONS[d]["name"] for d in directions)}')
    age_h = None
    try:
        age_h = (datetime.now().timestamp() - os.path.getmtime(CACHE)) / 3600
    except OSError:
        pass
    if age_h is not None:
        when = datetime.fromtimestamp(os.path.getmtime(CACHE)).strftime('%m-%d %H:%M')
        line = f'数据：{len(rows)} 条，截至 {when}（{age_h:.0f} 小时前）'
        if age_h > 12:
            line += '  ← 超过 12 小时了，建议先同步：node scripts/sync_jobs.cjs --only=jobs'
        print(line)
    print(f'总 {len(rows)} 家公司 → 排掉 ' + '、'.join(f'{k}{v}' for k, v in dropped.items()))
    if same_company:
        print(f'  其中 {same_company} 家是「投过这家的别的岗位」——想去的话加 --same-company')
    if not ex_rules:
        print('  ⚠ 排除线没设置。筛出来会带随机性，建议先说清 3–5 条绝对不投的')
    else:
        print(f'  排除线：{"、".join(ex_rules)}')
        if exclude_hits:
            detail = '；'.join(f'{k} 挡掉 {v} 条' for k, v in exclude_hits.items())
            print(f'  实际挡掉：{detail}')
    print(f'选出 {len(picked)} 条，与历史重复 {dup_rate * 100:.0f}%\n')

    for i, c in enumerate(picked, 1):
        print(f'{i:>2}. [{c["tier"]}] {c["company"]}｜{c["position"]}   {c["score"]} 分'
              f'（确定度 {c["confidence"]}/20）')
        line = f'     {c["city"]}｜截止 {c["deadline"]}'
        if c['total_positions'] > 1:
            line += f'｜该公司共 {c["total_positions"]} 个岗位'
        print(line)
        if c['exact']:
            print(f'     ✅ 精确命中：{"、".join(c["exact"])}')
        elif c['category']:
            print(f'     ⚠ 类别命中：{"、".join(c["category"])}——这是打包岗位，'
                  f'具体做什么要读 JD 才知道')
        if c['inferred'] and not c['exact']:
            ev = c['inferred'][0]
            print(f'     🔍 推断：由 {ev["org"]} 的「{ev["cap"]}」推出，没有直接支撑，你自己判断')
        if c['confidence_why']:
            print(f'     ℹ 可信度来源：{c["confidence_why"]}')
        print()
    if archive:
        print('清单已存：' + archive)


if __name__ == '__main__':
    main()
