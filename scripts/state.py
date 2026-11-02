# -*- coding: utf-8 -*-
"""读写进度状态，断了能接上

支持多条线并行：一次投三家公司时，每家各自有阶段和待办，不会互相覆盖。

用法:
  python scripts/state.py show                     # 当前这条 + 全部线
  python scripts/state.py list                     # 只看全部线的一览
  python scripts/state.py set --phase "阶段 8 · 填表中" --company 美团 \
      --position 产品运营岗应届 --todo "性别未选,到岗时间未选"
  python scripts/state.py todo --add "上传后弹窗要手动关"
  python scripts/state.py clear

状态文件在数据目录的 user/state.json，和仓库分开，不进 Git。

结构（新）：
  {
    "current": {"company": "...", "position": "..."},
    "jobs": [{"company","position","phase","resume","todo","updated_at"}, ...],
    "phase": "...", "company": "...", "position": "...", "todo": [...]
  }
顶层那几个字段是给旧读法留的镜像，等价于 current 那条。
"""
import argparse, json, os, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR

PATH = os.path.join(DATA_DIR, 'user', 'state.json')


def now():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load():
    if not os.path.exists(PATH):
        return {}
    try:
        return json.load(open(PATH, encoding='utf-8-sig'))
    except Exception:
        return {}


def save(d):
    d['updated_at'] = now()
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    json.dump(d, open(PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def key(c, p):
    return (c or '').strip(), (p or '').strip()


def jobs_of(d):
    """取全部线；老格式（只有顶层字段）自动升级成一条"""
    jobs = d.get('jobs')
    if isinstance(jobs, list):
        return jobs
    if d.get('company') or d.get('position') or d.get('phase'):
        return [{'company': d.get('company', ''), 'position': d.get('position', ''),
                 'phase': d.get('phase', ''), 'resume': d.get('resume_version', ''),
                 'todo': d.get('todo', [])}]
    return []


def upsert(d, company, position, fields):
    """按公司+岗位定位一条线，没有就新建，有就更新"""
    jobs = jobs_of(d)
    c, p = key(company, position)
    for j in jobs:
        if (j.get('company') or '').strip() == c and ((j.get('position') or '').strip() == p or not p):
            j.update(fields)
            j.setdefault('company', c)
            j['updated_at'] = now()
            d['jobs'] = jobs
            return j
    j = {'company': c, 'position': p, 'todo': [], 'updated_at': now()}
    j.update(fields)
    jobs.append(j)
    d['jobs'] = jobs
    return j


def mirror(d, job):
    """顶层字段同步成当前这条，兼容老读法"""
    d['company'] = job.get('company', '')
    d['position'] = job.get('position', '')
    d['phase'] = job.get('phase', '')
    d['resume_version'] = job.get('resume', '')
    d['todo'] = job.get('todo', [])
    d['current'] = {'company': d['company'], 'position': d['position']}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', nargs='?', default='show',
                    choices=['show', 'set', 'todo', 'list', 'clear'])
    ap.add_argument('--phase')
    ap.add_argument('--company')
    ap.add_argument('--position')
    ap.add_argument('--resume', dest='resume_version')
    ap.add_argument('--login', choices=['done', 'pending', 'failed'],
                    help='这条线的登录状态：用户登录完回你之后再记 done')
    ap.add_argument('--todo', help='逗号分隔的待办')
    ap.add_argument('--add')
    ap.add_argument('--done', help='从待办里划掉某项')
    a = ap.parse_args()

    d = load()

    if a.cmd == 'clear':
        if os.path.exists(PATH):
            os.remove(PATH)
        print('状态已清空')
        return

    if a.cmd == 'show':
        if not d:
            print('没有进行中的流程。')
            return
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return

    if a.cmd == 'list':
        jobs = jobs_of(d)
        if not jobs:
            print('还没有任何一条线。')
            return
        print('共 %d 条线：' % len(jobs))
        for i, j in enumerate(jobs, 1):
            todo = ('｜待办 ' + '、'.join(j.get('todo') or [])) if j.get('todo') else ''
            login = {'done': '｜已登录', 'pending': '｜待登录', 'failed': '｜登录失败'}.get(
                j.get('login'), '')
            print('  %d. %s｜%s  →  %s%s%s' % (
                i, j.get('company') or '（未写公司）', j.get('position') or '（未写岗位）',
                j.get('phase') or '（未开始）', login, todo))
        return

    if a.cmd == 'set':
        fields = {}
        if a.phase:
            fields['phase'] = a.phase
        if a.resume_version:
            fields['resume'] = a.resume_version
        if a.todo is not None:
            fields['todo'] = [x.strip() for x in a.todo.split(',') if x.strip()]
        if a.login:
            fields['login'] = a.login

        company = a.company or (d.get('company') or '')
        position = a.position if a.position is not None else (d.get('position') or '')
        job = upsert(d, company, position, fields)
        mirror(d, job)
        save(d)
        n = len(jobs_of(d))
        print('状态已更新：%s｜%s → %s（共 %d 条线）' % (
            job.get('company') or '', job.get('position') or '',
            job.get('phase') or '', n))
        if job.get('todo'):
            print('待办：' + '、'.join(job['todo']))
        return

    if a.cmd == 'todo':
        jobs = jobs_of(d)
        if not jobs:
            sys.exit('还没有任何一条线，先 set 一条。')
        cur_c = (d.get('company') or '').strip()
        cur_p = (d.get('position') or '').strip()
        job = next((j for j in jobs if (j.get('company') or '').strip() == cur_c
                    and (j.get('position') or '').strip() == cur_p), jobs[-1])
        todo = job.get('todo') or []
        if a.add:
            todo.append(a.add)
        if a.done:
            todo = [x for x in todo if a.done not in x]
        job['todo'] = todo
        job['updated_at'] = now()
        d['jobs'] = jobs
        mirror(d, job)
        save(d)
        print('待办：' + ('、'.join(todo) if todo else '（空）'))


if __name__ == '__main__':
    main()
