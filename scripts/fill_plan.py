# -*- coding: utf-8 -*-
"""填表计划：把 dump 出来的表单字段，对到信息库上，算出哪些能填、哪些要问

用法:
  node scripts/browser.cjs dump --page=... > dump.json
  python scripts/fill_plan.py dump.json

输出三桶：
  auto   —— 信息库里有，直接填（给出 label/value/来源路径）
  ask    —— 库里没有，必须问用户（**一次全问完，不要一条一条问**）
  manual —— 需要用户点（自定义下拉 / 日期区间 / 单选）

为什么要有它：以前是「填到哪个字段发现没有，就停下来问一次」，一页表单十几个
字段能问十几轮，用户被反复打断。把差异一次算出来，问一轮就够。
"""
import json, os, re, sys

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR

USER = os.path.join(DATA_DIR, 'user')

# 网申里同一个东西的叫法差异极大，先按别名对齐
ALIASES = {
    'basic.name': ['姓名', '名字', '真实姓名', '中文姓名'],
    'basic.gender': ['性别'],
    'basic.birth': ['出生日期', '出生年月', '生日'],
    'basic.phone': ['手机', '手机号', '手机号码', '联系电话', '电话', '联系方式'],
    'basic.email': ['邮箱', '电子邮箱', '电子邮件', 'email', 'e-mail'],
    'basic.political': ['政治面貌', '党派'],
    'basic.hometown': ['籍贯', '户籍所在地', '户籍'],
    'basic.city': ['现居城市', '现居住地', '所在城市', '目前所在地'],
    'basic.height': ['身高'],
    'basic.weight': ['体重'],
    'basic.id_type': ['证件类型'],
    'basic.unified': ['是否统招', '统招'],
    'basic.fresh_grad': ['应届', '是否应届', '生源类别'],
    'basic.grad_date': ['毕业时间', '毕业年月', '预计毕业时间', '预计毕业年月'],
    'basic.work_years': ['工作年限', '工作经验'],
    'edu.master.school': ['硕士学校', '毕业院校', '学校名称', '院校'],
    'edu.master.college': ['学院', '学院名称', '所在学院'],
    'edu.master.major': ['专业', '专业名称', '所学专业'],
    'edu.master.degree': ['学历', '最高学历', '学位'],
    'intent.cities': ['期望城市', '意向城市', '期望工作地'],
    'intent.jobs': ['期望岗位', '意向岗位', '应聘岗位', '求职意向'],
    'intent.available': ['到岗时间', '可到岗时间', '最快到岗'],
    'skill.english': ['英语等级', '外语水平', '英语水平'],
}

MANUAL_TYPES = {'custom-select', 'date-range', 'radio', 'checkbox', 'select'}

# 这些标签里的「姓名」「电话」指的是别人，不是本人。
# 2026-11-03 在洽洽上真出过事：basic.name 被推成「亲属姓名」、basic.phone 被推成
# 「亲属联系方式」，照 auto 桶填下去就是把用户自己填成他爸。子串匹配天生会犯这个错，
# 只能按语义挡一道：命中下面任何一个词，一律进 ask，永远不进 auto。
OTHER_PERSON = ('亲属', '紧急联系人', '父母', '父亲', '母亲', '配偶', '监护人',
                '家庭成员', '家庭电话', '推荐人', '证明人', '介绍人')


def is_other_person(label):
    n = norm(label)
    return any(k in n for k in OTHER_PERSON)


def dig(d, path):
    cur = d
    for k in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def load(name, default):
    p = os.path.join(USER, name)
    if not os.path.exists(p):
        return default
    try:
        return json.load(open(p, encoding='utf-8-sig'))
    except Exception:
        return default


def norm(s):
    return re.sub(r'[\s:：*（）()]+', '', str(s or ''))


def build_index(profile):
    """路径 → 值，再配上别名，做成一张「叫法 → 值」的索引"""
    idx = {}
    for path, names in ALIASES.items():
        v = dig(profile, path)
        if v in (None, '', []):
            continue
        for n in names:
            idx[norm(n)] = (path, v)
    return idx


def match(label, idx):
    n = norm(label)
    if not n:
        return None
    if n in idx:
        return idx[n]
    # 表单标签常带后缀，比如「姓名（必填）」「手机号码*」
    for key, val in idx.items():
        if key and (key in n or n in key):
            return val
    return None


def main():
    if len(sys.argv) < 2:
        sys.exit('用法：python scripts/fill_plan.py <browser.cjs dump 输出的 json>')
    path = sys.argv[1]
    raw_bytes = open(path, 'rb').read()
    data = None
    # PowerShell 重定向写出来的是 UTF-16，记事本可能存成 GBK，都认
    for enc in ('utf-8-sig', 'utf-16', 'gbk'):
        try:
            data = json.loads(raw_bytes.decode(enc))
            break
        except Exception:
            data = None
    if data is None:
        sys.exit('读不了这个文件：不是合法的 JSON，或者编码不认识')
    raw = data
    rows = raw.get('rows') if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        sys.exit('这不像 dump 的输出，应该有 rows 数组。')

    profile = load('profile.json', {})
    idx = build_index(profile)

    auto, ask, manual = [], [], []
    for r in rows:
        label = (r.get('label') or '').strip()
        typ = r.get('type') or ''
        if not label:
            continue
        if typ in MANUAL_TYPES:
            manual.append({'label': label, 'type': typ,
                           'current': r.get('value') or ''})
            continue
        if (r.get('value') or '').strip():
            continue                      # 系统已经填好了，不用管
        if is_other_person(label):
            # 问的是别人，不是本人；库里没有就等用户说，绝不拿本人的信息去顶
            ask.append({'label': label, 'type': typ})
            continue
        hit = match(label, idx)
        if hit:
            auto.append({'label': label, 'value': str(hit[1]),
                         'from': hit[0], 'nth': 0})
        else:
            ask.append({'label': label, 'type': typ})

    out = {
        'ok': True,
        'counts': {'auto': len(auto), 'ask': len(ask), 'manual': len(manual)},
        'hint': ('下面这些信息库里没有，一次问完，不要一条一条问：' + '、'.join(x['label'] for x in ask))
                if ask else '信息库够用，没有要问的。',
        'auto': auto,
        'ask': ask,
        'manual': manual,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
