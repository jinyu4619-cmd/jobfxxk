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
    s = str(s or '').strip()
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
    """从台账里读出已经投过的公司，推荐时排掉

    两个来源：脚本维护的台账，以及用户自己维护的总表（如果有）。
    总表是人工写的，格式不统一，所以只认「序号是数字、第二列像公司名」的行。
    """
    names = set()
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
            names.add(comp)
    return names


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--direction', default='', help='临时指定方向：user_ops/data_ops/ai_product/content_ops/channel_ops/mt')
    ap.add_argument('--city', default='')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--max-stale', type=int, default=45)
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
    now = datetime.now()
    dropped = {'届次': 0, '学历': 0, '城市': 0, '过期': 0, '陈旧': 0, '已投': 0}

    cands = []
    for r in rows:
        comp = (r.get('企业名称') or '').strip()
        if comp in applied:
            dropped['已投'] += 1
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
            # 三级权重：精确 100、类别 10、推断 1。岗位名是细分职位的最准，
            # 「运营类」这种打包只能算半个命中，得读 JD 才知道里面有什么。
            score = len(exact) * 100 + len(cat) * 10 + min(len(inferred), 3)
            cands.append(dict(
                company=comp, position=pos, city=r.get('工作城市'), deadline=r.get('截止日期'),
                link=r.get('投递地址'), industry=r.get('所属行业'),
                exact=sorted(set(exact)), category=sorted(set(cat)),
                inferred=inferred[:3], score=score, total_positions=len(positions)))

    cands.sort(key=lambda x: (-x['score'], x['company']))
    seen = set()
    picked = []
    for c in cands:
        k = (c['company'], c['position'])
        if k in seen:
            continue
        seen.add(k)
        picked.append(c)
        if len(picked) >= a.top:
            break

    if a.json:
        print(json.dumps({'directions': directions, 'dropped': dropped,
                          'items': picked}, ensure_ascii=False, indent=1))
        return

    print(f'方向：{"、".join(DIRECTIONS[d]["name"] for d in directions)}')
    print(f'总 {len(rows)} 家公司 → 排掉 ' + '、'.join(f'{k}{v}' for k, v in dropped.items()))
    print(f'按单个岗位展开、去掉已投公司后，选出 {len(picked)} 条\n')

    for i, c in enumerate(picked, 1):
        print(f'{i:>2}. {c["company"]}｜{c["position"]}')
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
        print()


if __name__ == '__main__':
    main()
