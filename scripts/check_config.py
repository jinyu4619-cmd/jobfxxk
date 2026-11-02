# -*- coding: utf-8 -*-
"""检查配置和环境：逐项给出人话提示

用法: python scripts/check_config.py

注意「必须存在」和「以后会生成」的区别：
  - 母版 docx / 母版 pdf / 简历库目录：没配好就没法开始
  - 锚点表：阶段 3 跑 make_anchors 才生成，现在没有是正常的
  - 台账：第一次投递才生成，只校验目录可写
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CONFIG_PATH, DATA_DIR, REPO
from nodebin import node_bin as _probe_node

CFG_PATH = CONFIG_PATH or os.path.join(DATA_DIR, 'config.json')
EXAMPLE = os.path.join(REPO, 'config.example.json')

cfg_ok = True
env_ok = True


def bad(msg, how):
    global cfg_ok
    cfg_ok = False
    print('  ✗ ' + msg)
    print('    → ' + how)


def p(v, default=None):
    v = (v or '').replace('/', os.sep)
    return v or default


def check_config():
    print('=== 配置文件 ===\n')

    if not os.path.exists(CFG_PATH):
        bad('没找到 config.json',
            f'应该放在：{CFG_PATH}。把 {EXAMPLE} 复制过去，改个名，填上路径')
        return None
    try:
        cfg = json.load(open(CFG_PATH, encoding='utf-8-sig'))
    except Exception as e:
        bad('config.json 格式不对：' + str(e)[:80],
            '检查是不是漏了逗号或引号，可以拿 config.example.json 对照')
        return None

    print(f'  配置文件：{CFG_PATH}')
    print(f'  数据目录：{DATA_DIR}\n')

    if not str(cfg.get('name', '')).strip() or cfg.get('name') == '你的名字':
        bad('name 还没改', '把 "你的名字" 换成你自己的名字')

    for key, desc, ext, kind in [
        ('master_docx', '母版 Word', '.docx', 'file'),
        ('master_pdf', '母版 PDF', '.pdf', 'file'),
        ('resume_dir', '简历库文件夹', None, 'dir'),
    ]:
        path = p(cfg.get(key))
        if not path:
            bad(f'{key}（{desc}）没填', '在 config.json 里补上这个字段')
            continue
        if not os.path.exists(path):
            bad(f'{key}（{desc}）不存在：{path}', '确认路径写对了，分隔符用正斜杠 /')
            continue
        if kind == 'dir' and not os.path.isdir(path):
            bad(f'{key} 应该是个文件夹，现在指向了文件：{path}', '改成文件夹路径')
            continue
        if ext and not path.lower().endswith(ext):
            bad(f'{key} 后缀不对，应该是 {ext}', '检查是不是指到了别的文件')
            continue
        print(f'  ✓ {key} → {path}')

    # 锚点表：允许不存在
    anchors = p(cfg.get('anchors'))
    if not anchors:
        print('  · anchors 没配，锚点表默认放数据目录下（阶段 3 生成）')
    elif os.path.exists(anchors):
        print(f'  ✓ anchors → {anchors}')
    else:
        print(f'  · 锚点表还没生成：{anchors}')
        print('    → 正常，第一次用之前跑一次 python scripts/make_anchors.py')

    # 台账：只校验目录可写，文件可以不存在
    tracker = p(cfg.get('tracker'))
    if not tracker:
        print('  · tracker 没配，台账默认放数据目录下')
    else:
        d = os.path.dirname(tracker) or '.'
        if os.path.isdir(d) and os.access(d, os.W_OK):
            state = '已存在' if os.path.exists(tracker) else '第一次投递时生成'
            print(f'  ✓ tracker 目录可写 → {tracker}（{state}）')
        else:
            bad(f'tracker 的目录不存在或不可写：{d}', '改成一个你能写的目录')

    cache = p(cfg.get('job_cache'))
    if not cache:
        print('  · job_cache 没配，岗位缓存默认放数据目录下')
    elif os.path.exists(cache):
        size = os.path.getsize(cache) / 1024 / 1024
        print(f'  ✓ job_cache → {cache}（{size:.1f}MB）')
    else:
        print('  · 岗位缓存还没有，第一次找岗时自动同步（约一分半）')

    return cfg


def node_bin():
    return _probe_node()


def check_env():
    global env_ok
    print('\n=== 运行环境 ===\n')

    node = node_bin()
    try:
        r = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=20)
        v = (r.stdout or '').strip()
        if not v:
            raise RuntimeError
        print(f'  ✓ Node.js {v}')
        major = int(v.lstrip('v').split('.')[0])
        if major < 18:
            print('    · 版本偏低，建议 18 以上（20 更好）')
    except Exception:
        env_ok = False
        print('  ✗ 没找到 Node.js')
        print('    → 装 20 以上：https://nodejs.org（LTS 版，一路下一步）')

    try:
        probe = "require('./ensure_deps.cjs');require('playwright');console.log('ok')"
        r = subprocess.run([node, '-e', probe], capture_output=True, text=True,
                           timeout=30, cwd=HERE)
        if 'ok' in (r.stdout or ''):
            print('  ✓ Playwright 已安装')
        else:
            env_ok = False
            print('  ✗ Playwright 没装')
            print('    → 跑 python scripts/setup.py --apply install_deps 自动装到数据目录')
    except Exception:
        env_ok = False
        print('  ✗ Playwright 没装')
        print('    → 跑 python scripts/setup.py --apply install_deps 自动装到数据目录')

    try:
        import pypdf
        print(f'  ✓ pypdf {pypdf.__version__}')
    except ImportError:
        env_ok = False
        print('  ✗ 缺少 Python 库 pypdf')
        print('    → 执行：python -m pip install pypdf')

    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command',
                            '(New-Object -ComObject Word.Application).Version'],
                           capture_output=True, text=True, timeout=30,
                           encoding='utf-8', errors='replace')
        v = (r.stdout or '').strip()
        if v and v[0].isdigit():
            print(f'  ✓ Microsoft Word {v}')
        else:
            print('  · 没检测到 Word（导出 PDF 时会用到）')
            print('    → 装 Word 或 WPS，否则只能用内置模板')
    except Exception:
        print('  · 没检测到 Word（导出 PDF 时会用到）')


def main():
    check_config()
    check_env()

    print()
    if cfg_ok and env_ok:
        print('配置和环境都没问题，可以开始用了。')
        return 0
    if not cfg_ok:
        print('配置还有问题，按上面的提示改完再跑一次。')
    if not env_ok:
        print('环境还差东西，按上面的提示装完再跑一次。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
