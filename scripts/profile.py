# -*- coding: utf-8 -*-
"""个人信息库：普通信息进 profile.json，敏感信息进 DPAPI 加密文件

字段表以 `references/问题库.md` 为准，分三级：
  P2 常规   → profile.json（明文，本地）
  P1 敏感   → sensitive.bin（DPAPI 加密）
  P0 极敏感 → sensitive.bin，且默认不代填

用法:
  python scripts/profile.py show                   # 看现有档案（敏感项打码）
  python scripts/profile.py missing                # 还缺什么（JSON，分三档）
  python scripts/profile.py set --file a.json      # 写入/合并（自动按级别分流）
  python scripts/profile.py review                 # 整体复述确认（采集完成的最后一步）
  python scripts/profile.py exp list|set|confirm   # 经历库
  python scripts/profile.py check                  # 体检

**中文一律走 --file，不要用管道。** Windows 下 `echo '{...}' | python` 会把中文转成 `?????`。
"""
import io, json, os, sys
from contextlib import redirect_stdout
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR
import secure_store as ss

USER = os.path.join(DATA_DIR, 'user')
PROFILE = os.path.join(USER, 'profile.json')
EXP = os.path.join(USER, 'experience.json')
LOG = os.path.join(USER, 'confirm-log.md')

# (编号, 路径, 名称, 级别, 必填, 提示)   —— 级别见 references/问题库.md 9.1
FIELDS = [
    ('A1', 'basic.name', '姓名', 'P2', True, '与证件完全一致'),
    ('A2', 'basic.gender', '性别', 'P2', True, '男 / 女 / 保密'),
    ('A3', 'basic.birth', '出生日期', 'P2', True, '年月日都要，简历上常只有年月'),
    ('A4', 'basic.phone', '手机号码', 'P2', True, '11 位'),
    ('A5', 'basic.email', '邮箱', 'P2', True, '与简历一致'),
    ('A6', 'basic.political', '政治面貌', 'P1', False, '中共党员 / 共青团员 / 群众 / 民主党派'),
    ('A7', 'basic.hometown', '籍贯', 'P1', False, '省+市；系统默认值常是「全国」，必须改'),
    ('A8', 'basic.city', '现居城市', 'P2', False, ''),
    ('A9', 'basic.height', '身高', 'P1', False, '厘米；消费品、零售、国企常问'),
    ('A10', 'basic.weight', '体重', 'P1', False, '公斤'),
    ('A11', 'basic.id_type', '证件类型', 'P2', True, '默认身份证'),
    ('A12', 'basic.id_no', '证件号码', 'P0', True, '18 位；默认不代填，见隐私协议'),
    ('A13', 'basic.photo', '证件照', 'P1', False, '白底 JPG/PNG，≤200KB'),
    ('A14', 'basic.unified', '是否统招', 'P2', False, '全日制一律「是」'),
    ('A15', 'basic.fresh_grad', '应届/往届', 'P2', True, '2027 届 → 应届'),
    ('A16', 'basic.grad_date', '毕业时间', 'P2', True, '精确到年月'),
    ('A17', 'basic.work_years', '工作年限', 'P2', False, '应届一般选「无工作年限」'),

    ('B1', 'edu.master.school', '硕士学校', 'P2', True, ''),
    ('B2', 'edu.master.college', '硕士学院', 'P2', True, '简历几乎不写，网申必问'),
    ('B3', 'edu.master.major', '硕士专业', 'P2', True, ''),
    ('B4', 'edu.master.degree', '硕士学历', 'P2', True, '硕士研究生'),
    ('B6a', 'edu.master.start', '硕士入学', 'P2', True, '年月'),
    ('B6b', 'edu.master.grad', '硕士毕业', 'P2', True, '年月'),
    ('B7', 'edu.master.gpa', '硕士成绩/排名', 'P2', False, '无明确排名可填「专业前 X%」'),
    ('B9', 'edu.bachelor.school', '本科学校', 'P2', True, ''),
    ('B2b', 'edu.bachelor.college', '本科学院', 'P2', True, ''),
    ('B3b', 'edu.bachelor.major', '本科专业', 'P2', True, ''),
    ('B4b', 'edu.bachelor.degree', '本科学历', 'P2', True, '本科'),
    ('B6c', 'edu.bachelor.start', '本科入学', 'P2', True, '年月'),
    ('B6d', 'edu.bachelor.grad', '本科毕业', 'P2', True, '年月'),
    ('B5', 'edu.bachelor.first', '是否第一学历', 'P2', False, '本科 = 是'),

    ('C6', 'skill.english', '英语等级', 'P2', False, 'CET-4/6、雅思托福'),
    ('C6b', 'skill.english_score', '英语分数', 'P2', False, '分数要准'),
    ('C7', 'skill.certificates', '证书', 'P2', False, '计算机/专业证书'),
    ('C8', 'skill.tags', '技能标签', 'P2', False, '与简历口径对齐'),

    ('D1', 'intent.industry', '期望行业', 'P2', False, '与该岗位行业一致'),
    ('D2', 'intent.jobs', '期望岗位', 'P2', True, '多个方向逗号分隔'),
    ('D3', 'intent.cities', '期望城市', 'P2', True, '按优先级排'),
    ('D6', 'intent.available', '到岗时间', 'P2', True, '很多公司会卡这个'),
    ('D8', 'intent.travel', '是否接受出差', 'P2', False, '渠道、零售类常问'),

    ('E2a', 'contact.emergency', '紧急联系人', 'P0', False, '姓名与关系；默认不代填'),
    ('E2b', 'contact.emergency_phone', '紧急联系人电话', 'P0', False, '默认不代填'),
    ('E4', 'background.hukou', '户口所在地', 'P1', False, '国企、事业单位常见'),
    ('E4b', 'background.archive', '档案所在地', 'P1', False, '同上'),
]

LEVEL_OF = {f[1]: f[3] for f in FIELDS}
SENSITIVE_LEVELS = {'P0', 'P1'}
MODE_KEY = '_privacy_mode'          # A=全部代填 B=只填P2 C=全自己填


# ---------- 读写 ----------

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


def load_sensitive():
    try:
        return ss.load() or {}
    except Exception:
        return {}


def save_sensitive(d):
    with redirect_stdout(io.StringIO()):     # secure_store.save 会打印，这里不需要
        ss.save(d)


def flatten(d, prefix=''):
    out = {}
    for k, v in d.items():
        p = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, p))
        else:
            out[p] = v
    return out


def unflatten(flat):
    out = {}
    for p, v in flat.items():
        cur = out
        ks = p.split('.')
        for k in ks[:-1]:
            cur = cur.setdefault(k, {})
        cur[ks[-1]] = v
    return out


def all_values():
    """普通 + 敏感合成一份扁平视图"""
    v = flatten(load(PROFILE, {}))
    v.update({k: x for k, x in flatten(load_sensitive()).items()})
    return v


def dig(flat, path):
    return flat.get(path)


# ---------- 命令 ----------

def mask(v):
    s = str(v)
    if len(s) <= 4:
        return '*' * len(s)
    return s[:2] + '*' * (len(s) - 4) + s[-2:]


def cmd_show(_):
    flat = all_values()
    p = load(PROFILE, {})
    if p.get(MODE_KEY):
        print(f'隐私处理方式：{p[MODE_KEY]}'
              f'（A=全部代填 B=只填常规 C=全自己填）')
    shown = 0
    for _id, path, label, level, _req, _hint in FIELDS:
        v = dig(flat, path)
        if v in (None, '', []):
            continue
        shown += 1
        print(f'{label}：{mask(v) if level in SENSITIVE_LEVELS else v}')
    if not shown:
        print('还没有档案。')

    exps = load(EXP, [])
    if exps:
        print(f'\n经历 {len(exps)} 条：')
        for e in exps:
            mark = {'confirmed': '✓', 'draft': '·', 'confirming': '?', 'deprecated': '×'}.get(e.get('status'), '?')
            print(f'  {mark} {e.get("id")}  {e.get("org","")} · {e.get("role","")}')
    m = missing()
    if m:
        print(f'\n还缺 {len(m)} 项：' + '、'.join(x['label'] for x in m[:12]))


def missing():
    flat = all_values()
    mode = load(PROFILE, {}).get(MODE_KEY)
    out = []
    for _id, path, label, level, req, hint in FIELDS:
        v = dig(flat, path)
        if v not in (None, '', []):
            continue
        # 用户选了 C（全自己填），敏感项不进缺口，只在填表时提示
        if level in SENSITIVE_LEVELS and mode == 'C':
            tier = 'self_fill'
        elif level in SENSITIVE_LEVELS and not mode:
            tier = 'needs_auth'
        elif req:
            tier = 'required'
        else:
            tier = 'suggest'
        out.append({'id': _id, 'path': path, 'label': label, 'level': level,
                    'tier': tier, 'hint': hint})
    return out


def cmd_missing(_):
    m = missing()
    order = ['required', 'needs_auth', 'suggest', 'self_fill']
    print(json.dumps({
        'required_count': sum(1 for x in m if x['tier'] == 'required'),
        'needs_auth': [x for x in m if x['tier'] == 'needs_auth'],
        'missing': sorted(m, key=lambda x: order.index(x['tier'])),
    }, ensure_ascii=False, indent=1))


def read_input():
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

    flat_in = flatten(incoming)
    normal, secret = {}, {}
    unknown = []
    for path, v in flat_in.items():
        lvl = LEVEL_OF.get(path)
        if lvl in SENSITIVE_LEVELS:
            secret[path] = v
        elif lvl == 'P2':
            normal[path] = v
        else:
            # 字段表里没有的键：留在普通文件里，但提示一句
            normal[path] = v
            unknown.append(path)

    prof = flatten(load(PROFILE, {}))
    prof.update(normal)
    save(PROFILE, unflatten(prof))

    if secret:
        sec = flatten(load_sensitive())
        sec.update(secret)
        # 加密文件里用扁平键（basic.id_no 这种），不要套一层又一层
        save_sensitive(sec)
        print(f'其中 {len(secret)} 项敏感信息已加密存放（不进 profile.json）')

    if unknown:
        print('这些字段不在问题库里，先按普通信息存了：' + '、'.join(unknown[:5]))

    left = [x['label'] for x in missing() if x['tier'] == 'required']
    print(f'已更新。必填还差 {len(left)} 项' + ('：' + '、'.join(left) if left else '，齐了。'))


def cmd_mode(mode):
    if mode not in ('A', 'B', 'C'):
        sys.exit('用法：profile.py mode A|B|C')
    p = load(PROFILE, {})
    p[MODE_KEY] = mode
    save(PROFILE, p)
    desc = {'A': 'P0/P1 也由我代填，加密存在本地',
            'B': '我只填常规信息，证件号和紧急联系人你自己输',
            'C': '全部你自己填，我只提示该填什么'}[mode]
    print(f'已记下隐私处理方式 {mode}：{desc}')


def cmd_review(_):
    """采集完成的最后一步：整体念一遍让用户确认"""
    flat = all_values()
    for _id, path, label, level, _req, _hint in FIELDS:
        v = dig(flat, path)
        if v in (None, '', []):
            continue
        print(f'  {label}：{mask(v) if level in SENSITIVE_LEVELS else v}')
    p = load(PROFILE, {})
    p['_reviewed_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    save(PROFILE, p)
    print('\n已记下这次整体确认的时间。有错就说，我改完重新念一遍。')


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
            exps[by_id[eid]].update(e)
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
        print(f'{e.get("id")}  [{e.get("status","draft")}]  {e.get("org","")} · {e.get("role","")}  '
              f'{e.get("start","")}-{e.get("end","") or "至今"}  数字：{"、".join(e.get("metrics_all") or []) or "无"}')


def cmd_exp_confirm(argv):
    if len(argv) < 3:
        sys.exit('用法：exp confirm <id> [--metrics 80%,95%]')
    eid = argv[2]
    exps = load(EXP, [])
    hit = next((e for e in exps if e.get('id') == eid), None)
    if not hit:
        sys.exit(f'没有 {eid} 这条。用 exp list 看看现有编号。')

    # 有数字的经历，必须逐个报一遍，且和记录一致才允许确认
    nums = hit.get('metrics_all') or []
    if nums:
        if '--metrics' not in argv:
            sys.exit('这条有数字：' + '、'.join(nums) +
                     '\n确认前必须逐个念给用户听，然后用 --metrics 把你核过的数字报回来，例如 '
                     '--metrics "' + ','.join(nums) + '"')
        said = {x.strip() for x in argv[argv.index('--metrics') + 1].replace('，', ',').split(',') if x.strip()}
        if said != set(nums):
            sys.exit('对不上。记录里是：' + '、'.join(nums) + '\n你报的是：' + '、'.join(sorted(said)) +
                     '\n要么用户改了数字（那就先改记录），要么漏念了。')

    hit['status'] = 'confirmed'
    hit['confirmed_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    save(EXP, exps)
    os.makedirs(USER, exist_ok=True)
    line = (f'- {datetime.now().strftime("%Y-%m-%d %H:%M")} 确认 {eid} · {hit.get("org","")} '
            f'{hit.get("role","")}｜数字：{"、".join(nums) or "无"}\n')
    if not os.path.exists(LOG):
        io.open(LOG, 'w', encoding='utf-8').write('# 确认记录\n\n')
    io.open(LOG, 'a', encoding='utf-8').write(line)
    print(f'{eid} 已确认，记进 confirm-log。')


def cmd_check(_):
    issues = []
    for x in missing():
        if x['tier'] == 'required':
            issues.append({'kind': 'missing_field', 'path': x['path'], 'detail': x['label']})
        elif x['tier'] == 'needs_auth':
            issues.append({'kind': 'privacy_mode_not_set', 'path': x['path'], 'detail': x['label']})
    for e in load(EXP, []):
        if e.get('status') != 'confirmed':
            issues.append({'kind': 'unconfirmed_exp', 'id': e.get('id'),
                           'detail': f'{e.get("org","")} {e.get("role","")}'})
    if not load(PROFILE, {}).get('_reviewed_at'):
        issues.append({'kind': 'not_reviewed', 'detail': '还没做整体复述确认'})
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
    if cmd == 'mode':
        return cmd_mode(argv[1] if len(argv) > 1 else '')
    if cmd == 'review':
        return cmd_review(None)
    if cmd == 'check':
        return cmd_check(None)
    if cmd == 'exp':
        sub = argv[1] if len(argv) > 1 else 'list'
        if sub == 'list':
            return cmd_exp_list(None)
        if sub == 'set':
            return cmd_exp_set(None)
        if sub == 'confirm':
            return cmd_exp_confirm(argv)
    sys.exit(__doc__)


if __name__ == '__main__':
    main()
