# -*- coding: utf-8 -*-
"""个人档案：静态信息 + 经历库，只问缺的，问过就不再问

用法:
  python scripts/profile.py show                   # 看现有档案（敏感字段打码）
  python scripts/profile.py missing                # 还缺哪些必填项（JSON，给 agent 读）
  python scripts/profile.py set --file a.json      # 写入/合并静态信息（推荐：先写文件）
  python scripts/profile.py exp set --file b.json  # 写入/合并经历（按 id 合并）
  python scripts/profile.py exp list               # 列出经历与状态
  python scripts/profile.py exp confirm exp_001    # 确认一条，写 confirm-log
  python scripts/profile.py check                  # 体检：未确认、数字没核过的

**中文一律走 --file，不要用管道。** Windows 下 `echo '{...}' | python` 会把中文转成
`?????`（管道的编码是控制台代码页）。先写成一个 UTF-8 的 json 文件再传路径。

文件都在数据目录的 user/ 下，不进仓库：
  profile.json / experience.json / confirm-log.md
"""
import io, json, os, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR

USER = os.path.join(DATA_DIR, 'user')
PROFILE = os.path.join(USER, 'profile.json')
EXP = os.path.join(USER, 'experience.json')
LOG = os.path.join(USER, 'confirm-log.md')
SENSITIVE = {'id_no', 'phone', 'emergency_phone'}

# (路径, 名称, 是否必填, 提示)
FIELDS = [
    ('basic.name', '姓名', True, '和证件一致'),
    ('basic.gender', '性别', True, '男 / 女 / 保密'),
    ('basic.birth', '出生日期', True, '年月日都要，简历上常只有年月'),
    ('basic.phone', '手机号', True, '11 位'),
    ('basic.email', '邮箱', True, '建议与简历一致'),
    ('basic.political', '政治面貌', True, '中共党员 / 共青团员 / 群众 / 民主党派'),
    ('basic.hometown', '籍贯', True, '省 + 市，系统默认值常是「全国」，要改'),
    ('basic.city', '现居城市', True, ''),
    ('basic.id_type', '证件类型', True, '默认身份证'),
    ('basic.id_no', '证件号码', True, '加密保存，填表时才取'),
    ('basic.height', '身高', False, '部分行业会问，厘米'),
    ('basic.weight', '体重', False, '部分行业会问，公斤'),
    ('basic.unified', '是否统招', True, '全日制一律填「是」'),
    ('basic.grad_year', '毕业年份', True, '如 2027'),
    ('basic.grad_date', '毕业时间', True, '精确到年月'),
    ('edu.master.school', '硕士学校', True, ''),
    ('edu.master.college', '硕士学院', True, '简历普遍不写，网申几乎都问'),
    ('edu.master.major', '硕士专业', True, ''),
    ('edu.master.start', '硕士入学', True, '年月'),
    ('edu.master.grad', '硕士毕业', True, '年月'),
    ('edu.bachelor.school', '本科学校', True, ''),
    ('edu.bachelor.college', '本科学院', False, ''),
    ('edu.bachelor.major', '本科专业', True, ''),
    ('edu.bachelor.start', '本科入学', True, '年月'),
    ('edu.bachelor.grad', '本科毕业', True, '年月'),
    ('intent.jobs', '期望岗位', True, '可以是多个方向，逗号分隔'),
    ('intent.cities', '意向城市', True, '按优先级排'),
    ('intent.industry', '意向行业', False, ''),
    ('intent.available', '到岗时间', True, '很多公司会卡这个，写具体月份'),
    ('contact.emergency', '紧急联系人', False, '姓名与关系'),
    ('contact.emergency_phone', '紧急联系人电话', False, ''),
]


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(io.open(path, encoding='utf-8-sig'))
    except Exception as e:
        sys.exit(f'{os.path.basename(path)} 读不了（{e}）。检查是不是被编辑器改坏了。')


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, 'w', encoding='utf-8').write(json.dumps(obj, ensure_ascii=False, indent=1))


def dig(d, path):
    cur = d
    for k in path.split('.'):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def put(d, path, value):
    keys = path.split('.')
    cur = d
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def mask(v):
    s = str(v)
    if len(s) <= 4:
        return '*' * len(s)
    return s[:2] + '*' * (len(s) - 4) + s[-2:]


def missing():
    p = load(PROFILE, {})
    out = []
    for path, label, required, hint in FIELDS:
        v = dig(p, path)
        if v in (None, '', [], {}):
            out.append({'path': path, 'label': label, 'required': required, 'hint': hint})
    return out


def cmd_show(_):
    p = load(PROFILE, {})
    if not p:
        print('还没有档案。')
    else:
        for path, label, required, _hint in FIELDS:
            v = dig(p, path)
            if v in (None, '', []):
                continue
            key = path.split('.')[-1]
            print(f'{label}：{mask(v) if key in SENSITIVE else v}')

    exps = load(EXP, [])
    if exps:
        print(f'\n经历 {len(exps)} 条：')
        for e in exps:
            mark = {'confirmed': '✓', 'draft': '·', 'confirming': '?', 'deprecated': '×'}.get(
                e.get('status'), '?')
            print(f'  {mark} {e.get("id")}  {e.get("org","")} · {e.get("role","")}  '
                  f'{e.get("start","")}-{e.get("end","") or "至今"}')
    else:
        print('\n还没有经历条目。')

    m = missing()
    req = [x for x in m if x['required']]
    if req:
        print(f'\n还缺 {len(req)} 项必填：' + '、'.join(x['label'] for x in req))


def cmd_missing(_):
    m = missing()
    print(json.dumps({'missing': m, 'required_count': sum(1 for x in m if x['required'])},
                     ensure_ascii=False, indent=1))


def read_input():
    """优先读 --file 指定的 UTF-8 文件；没有就读 stdin，并强制按 UTF-8 解码

    管道在 Windows 下按控制台代码页转码，中文会变成 ?????，所以文档里推荐用 --file。
    """
    argv = sys.argv
    if '--file' in argv:
        path = argv[argv.index('--file') + 1]
        if not os.path.exists(path):
            sys.exit(f'找不到输入文件：{path}')
        return io.open(path, encoding='utf-8-sig').read()
    return sys.stdin.buffer.read().decode('utf-8-sig', errors='replace')


def cmd_set(_):
    try:
        incoming = json.loads(read_input() or '{}')
    except Exception as e:
        sys.exit('输入不是合法 JSON：' + str(e)[:80])
    if not isinstance(incoming, dict):
        sys.exit('要传一个 JSON 对象，例如 {"basic":{"gender":"女"}}')
    p = load(PROFILE, {})
    merge(p, incoming)
    save(PROFILE, p)
    still = [x['label'] for x in missing() if x['required']]
    print(f'已更新。必填还差 {len(still)} 项' + ('：' + '、'.join(still) if still else '，齐了。'))


def cmd_exp_set(_):
    try:
        incoming = json.loads(read_input() or '[]')
    except Exception as e:
        sys.exit('输入不是合法 JSON：' + str(e)[:80])
    if isinstance(incoming, dict):
        incoming = [incoming]
    exps = load(EXP, [])
    by_id = {e.get('id'): i for i, e in enumerate(exps)}
    added = updated = 0
    for e in incoming:
        eid = e.get('id') or f'exp_{len(exps) + added + 1:03d}'
        e['id'] = eid
        if eid in by_id:
            merge(exps[by_id[eid]], e)
            updated += 1
        else:
            e.setdefault('status', 'draft')
            exps.append(e)
            by_id[eid] = len(exps) - 1
            added += 1
    save(EXP, exps)
    print(f'新增 {added} 条，更新 {updated} 条，共 {len(exps)} 条。')
    draft = [e['id'] for e in exps if e.get('status') != 'confirmed']
    if draft:
        print('还没确认：' + '、'.join(draft))


def cmd_exp_list(_):
    exps = load(EXP, [])
    if not exps:
        print('没有经历条目。')
        return
    for e in exps:
        print(f'{e.get("id")}  [{e.get("status","draft")}]  '
              f'{e.get("org","")} · {e.get("role","")}  '
              f'{e.get("start","")}-{e.get("end","") or "至今"}  '
              f'数字：{"、".join(e.get("metrics_all") or []) or "无"}')


def cmd_exp_confirm(eid):
    exps = load(EXP, [])
    hit = next((e for e in exps if e.get('id') == eid), None)
    if not hit:
        sys.exit(f'没有 {eid} 这条。用 exp list 看看现有编号。')
    hit['status'] = 'confirmed'
    hit['confirmed_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    save(EXP, exps)
    os.makedirs(USER, exist_ok=True)
    nums = '、'.join(hit.get('metrics_all') or []) or '无数字'
    line = (f'- {datetime.now().strftime("%Y-%m-%d %H:%M")} 确认 {eid} · '
            f'{hit.get("org","")} {hit.get("role","")}｜数字：{nums}\n')
    if not os.path.exists(LOG):
        io.open(LOG, 'w', encoding='utf-8').write('# 确认记录\n\n')
    io.open(LOG, 'a', encoding='utf-8').write(line)
    print(f'{eid} 已确认，记进 confirm-log。')


def cmd_check(_):
    issues = []
    for x in missing():
        if x['required']:
            issues.append({'kind': 'missing_field', 'path': x['path'], 'detail': x['label']})
    exps = load(EXP, [])
    for e in exps:
        if e.get('status') != 'confirmed':
            issues.append({'kind': 'unconfirmed_exp', 'id': e.get('id'),
                           'detail': f'{e.get("org","")} {e.get("role","")}'})
        nums = e.get('metrics_all') or []
        if nums and not e.get('confirmed_at'):
            issues.append({'kind': 'unchecked_metrics', 'id': e.get('id'),
                           'detail': '、'.join(nums)})
    print(json.dumps({'ok': not issues, 'issues': issues}, ensure_ascii=False, indent=1))


def main():
    argv = sys.argv[1:]
    if not argv:
        return cmd_show(None)
    cmd = argv[0]
    if cmd == 'show':
        return cmd_show(None)
    if cmd == 'missing':
        return cmd_missing(None)
    if cmd == 'set':
        return cmd_set(None)
    if cmd == 'check':
        return cmd_check(None)
    if cmd == 'exp':
        sub = argv[1] if len(argv) > 1 else 'list'
        if sub == 'list':
            return cmd_exp_list(None)
        if sub == 'set':
            return cmd_exp_set(None)
        if sub == 'confirm':
            if len(argv) < 3:
                sys.exit('用法：exp confirm <id>')
            return cmd_exp_confirm(argv[2])
    sys.exit(__doc__)


if __name__ == '__main__':
    main()
