# -*- coding: utf-8 -*-
"""读写进度状态，断了能接上

用法:
  python scripts/state.py show
  python scripts/state.py set --phase "阶段 8 · 填表中" --company 某公司 \
      --position 用户运营管培生 --todo "性别未选,到岗时间未选"
  python scripts/state.py todo --add "上传后弹窗要手动关"
  python scripts/state.py clear

状态文件在数据目录的 user/state.json，和仓库分开，不进 Git。
"""
import argparse, json, os, sys
from datetime import datetime
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from paths import DATA_DIR

PATH = os.path.join(DATA_DIR, 'user', 'state.json')


def load():
    if not os.path.exists(PATH):
        return {}
    try:
        return json.load(open(PATH, encoding='utf-8-sig'))
    except Exception:
        return {}


def save(d):
    d['updated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    json.dump(d, open(PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', nargs='?', default='show',
                    choices=['show', 'set', 'todo', 'clear'])
    ap.add_argument('--phase')
    ap.add_argument('--company')
    ap.add_argument('--position')
    ap.add_argument('--resume', dest='resume_version')
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

    if a.cmd == 'set':
        for k in ('phase', 'company', 'position', 'resume_version'):
            v = getattr(a, k)
            if v:
                d[k] = v
        if a.todo is not None:
            d['todo'] = [x.strip() for x in a.todo.split(',') if x.strip()]
        save(d)
        print(f'状态已更新：{d.get("phase", "")} {d.get("company", "")}'.strip())
        if d.get('todo'):
            print('待办：' + '、'.join(d['todo']))
        return

    if a.cmd == 'todo':
        todo = d.get('todo', [])
        if a.add:
            todo.append(a.add)
        if a.done:
            todo = [x for x in todo if a.done not in x]
        d['todo'] = todo
        save(d)
        print('待办：' + ('、'.join(todo) if todo else '（空）'))


if __name__ == '__main__':
    main()
