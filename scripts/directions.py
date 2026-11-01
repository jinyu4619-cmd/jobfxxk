# -*- coding: utf-8 -*-
"""从已确认的经历里推出 3–4 个岗位方向，附上依据

用法:
  python scripts/directions.py            # 给人看
  python scripts/directions.py --json     # 给 agent 读，拿去渲染按钮

为什么要这个：直接拿「用户运营」这种词去筛岗，等于替用户拍了板。
方向应该从他自己的经历里长出来——哪几段支撑得起，才推哪个。
"""
import io, json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR

USER = os.path.join(DATA_DIR, 'user')
EXP = os.path.join(USER, 'experience.json')
PROFILE = os.path.join(USER, 'profile.json')

# 方向目录：名称、用来筛岗的关键词、判定用的信号词
CATALOG = [
    dict(id='user_ops', name='用户 / 人群运营',
         keywords=['用户运营', '人群运营', '会员运营', '用户增长', '社群运营', '用户分层'],
         signals=['用户分层', '用户洞察', '用户兴趣', '用户增长', '人群', '会员', '社群', '触达率']),
    dict(id='data_ops', name='数据运营 / 增长',
         keywords=['数据运营', '增长', '数据分析', '商业分析', '策略运营'],
         signals=['数据监控', '数据更新', '周度数据', '报告', '统计分析', '数据量级', '增长信号', '投放策略']),
    dict(id='ai_product', name='AI 产品 / AI 运营',
         keywords=['AI产品', '智能体', '产品经理', 'AI应用', 'AI运营'],
         signals=['AI工具', 'Codex', 'Coze', 'Prompt', '智能体', 'AI情绪', 'AI提效', '多模态']),
    dict(id='content_ops', name='内容 / 新媒体运营',
         keywords=['内容运营', '新媒体运营', '品牌内容', '文案', '媒介'],
         signals=['内容运营', '文案', '公众号', '微博', '小红书', '新媒体', '原创', '选题']),
    dict(id='channel_ops', name='渠道 / 电商运营',
         keywords=['渠道运营', '电商运营', '店铺运营', '线上运营', '销售运营'],
         signals=['渠道', '投放', '转化率', '店铺', '电商', '活动运营', '宣发']),
    dict(id='mt', name='运营管培生',
         keywords=['管培生', '管理培训生', '储备干部'],
         signals=['统筹', '全流程', '标准化', '复盘', 'SOP']),
]


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(io.open(path, encoding='utf-8-sig'))
    except Exception:
        return default


def exp_text(e):
    parts = [e.get('org', ''), e.get('role', '')]
    for b in e.get('bullets') or []:
        parts += [b.get('theme', ''), b.get('action', ''), b.get('method', ''), b.get('result', '')]
    return ' '.join(parts)


def score_all():
    exps = [e for e in load(EXP, []) if e.get('status') == 'confirmed']
    prof = load(PROFILE, {})
    intent = ' '.join(str(x) for x in (prof.get('intent') or {}).values())

    out = []
    for d in CATALOG:
        evidence, breadth = [], 0
        for e in exps:
            text = exp_text(e)
            matched = [s for s in d['signals'] if s in text]
            if matched:
                breadth += len(matched)
                evidence.append({'exp': e.get('id'), 'org': e.get('org'),
                                 'role': e.get('role'), 'signals': matched})
        # 主看「有几段经历撑得住」，不看命中多少个同义词——
        # 否则信号词写得多的方向会天然占便宜。
        base = len(evidence) * 2
        bonus = 2 if any(k in intent for k in d['keywords']) else 0
        out.append(dict(id=d['id'], name=d['name'], keywords=d['keywords'],
                        score=base + bonus, breadth=breadth, evidence=evidence,
                        from_intent=bool(bonus)))
    out.sort(key=lambda x: (-x['score'], -x['breadth'], -len(x['evidence'])))
    return out


def main():
    ranked = [d for d in score_all() if d['score'] > 0]
    if not ranked:
        print('经历库还是空的，先把经历确认完再来推方向。')
        return
    top = ranked[:4]

    if '--json' in sys.argv:
        print(json.dumps({'directions': top, 'others': [d['name'] for d in ranked[4:]]},
                         ensure_ascii=False, indent=1))
        return

    print(f'从 {len(ranked)} 个可能的方向里，挑出这 {len(top)} 个：\n')
    for i, d in enumerate(top, 1):
        print(f'{i}. {d["name"]}' + ('（你填期望岗位时提到过）' if d['from_intent'] else ''))
        for ev in d['evidence'][:3]:
            print(f'   依据：{ev["org"]} · {ev["role"]}（{"、".join(ev["signals"])}）')
        print(f'   筛岗关键词：{"、".join(d["keywords"][:4])}')
        print()
    if len(ranked) > 4:
        print('没进前四的：' + '、'.join(d['name'] for d in ranked[4:]))


if __name__ == '__main__':
    main()
