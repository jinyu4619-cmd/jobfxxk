# -*- coding: utf-8 -*-
"""检查 config.json 是否配好，逐项给出人话提示
用法: python scripts/check_config.py
"""
import json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CONFIG_PATH, DATA_DIR, REPO

CFG = CONFIG_PATH or os.path.join(DATA_DIR, 'config.json')
EXAMPLE = os.path.join(REPO, 'config.example.json')

REQUIRED = [
    ('name', '你的名字', None),
    ('master_docx', '母版简历的 Word 文件', '.docx'),
    ('master_pdf', '母版简历的 PDF 文件', '.pdf'),
    ('anchors', '锚点表', '.json'),
    ('resume_dir', '简历库文件夹', None),
    ('tracker', '投递台账', '.md'),
]

ok = True


def fail(msg, how):
    global ok
    ok = False
    print('  ✗ ' + msg)
    print('    → ' + how)


print('=== 检查 config.json ===\n')

if not os.path.exists(CFG):
    print('  ✗ 没找到 config.json')
    print(f'    → 应该放在：{CFG}')
    print('    → 把仓库里的 config.example.json 复制过去，改个名，再填路径')
    print(f'    → 命令：copy "{EXAMPLE}" "{CFG}"')
    sys.exit(1)

try:
    cfg = json.load(open(CFG, encoding='utf-8'))
except Exception as e:
    print('  ✗ config.json 格式不对：' + str(e)[:80])
    print('    → 检查是不是漏了逗号或引号，可以拿 config.example.json 对照')
    sys.exit(1)

if cfg.get('name', '').strip() in ('', '你的名字'):
    fail('name 还没改', '把 "你的名字" 换成你自己的名字')

for key, desc, ext in REQUIRED:
    val = cfg.get(key)
    if not val:
        fail(f'{key}（{desc}）没填', '在 config.json 里补上这个字段')
        continue
    if key == 'name':
        continue
    p = val.replace('/', os.sep)
    if not os.path.exists(p):
        kind = '文件夹' if ext is None else '文件'
        fail(f'{key}（{desc}）指向的{kind}不存在：{p}',
             '确认路径写对了，分隔符用正斜杠 /')
        continue
    if ext and not p.lower().endswith(ext):
        fail(f'{key} 后缀不对，应该是 {ext}', '检查是不是指到了别的文件')
        continue
    print(f'  ✓ {key} → {p}')

# job_cache 只提示，不强制（首次运行会自动生成）
jc = cfg.get('job_cache')
if jc:
    print(('  ✓ job_cache 已存在' if os.path.exists(jc.replace('/', os.sep))
           else '  · job_cache 还没有，第一次找岗时会自动生成（约 40 秒）'))

print()
if not ok:
    print('有项目没配好，按上面的提示改完再跑一次。')
    sys.exit(1)

# ---------- 环境检查 ----------
print('\n=== 检查运行环境 ===\n')
env_ok = True


def check_env(name, cmd, hint, required=True):
    global env_ok
    import subprocess
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20,
                           encoding='utf-8', errors='replace')
        out = (r.stdout or '').strip().splitlines()
        ver = out[0] if out else ''
        print(f'  ✓ {name}  {ver[:40]}')
        return True
    except Exception:
        if required:
            env_ok = False
            print(f'  ✗ 缺少 {name}')
            print(f'    → {hint}')
        else:
            print(f'  · 没有 {name}（做这步时会用到）')
            print(f'    → {hint}')
        return False


import shutil as _shutil
node = _shutil.which('node') or r'D:\nodejs\node.exe'
check_env('Node.js', [node, '--version'], '装 Node.js 18 以上：https://nodejs.org')

# Playwright 是否装好
try:
    import subprocess
    r = subprocess.run([node, '-e', "require('playwright');console.log('ok')"],
                       capture_output=True, text=True, timeout=30, cwd=REPO)
    if 'ok' in (r.stdout or ''):
        print('  ✓ Playwright 已安装')
    else:
        env_ok = False
        print('  ✗ Playwright 没装')
        print('    → 在 jobfxxk 目录里执行：npm install')
except Exception:
    env_ok = False
    print('  ✗ Playwright 没装')
    print('    → 在 jobfxxk 目录里执行：npm install')

# pypdf
try:
    import pypdf
    print(f'  ✓ pypdf {pypdf.__version__}')
except ImportError:
    env_ok = False
    print('  ✗ 缺少 Python 库 pypdf')
    print('    → 执行：pip install pypdf')

# Word（导出 PDF 要用）
try:
    import subprocess
    r = subprocess.run(['powershell', '-NoProfile', '-Command',
                        '(New-Object -ComObject Word.Application).Version'],
                       capture_output=True, text=True, timeout=30,
                       encoding='utf-8', errors='replace')
    v = (r.stdout or '').strip()
    if v and v[0].isdigit():
        print(f'  ✓ Microsoft Word {v}')
    else:
        print('  · 没检测到 Word（微调简历导出 PDF 时会用到）')
        print('    → 装 Word 或 WPS，否则只能用内置模板')
except Exception:
    print('  · 没检测到 Word（微调简历导出 PDF 时会用到）')

print()
if env_ok:
    print('配置和环境都没问题，可以开始用了。')
    sys.exit(0)
print('环境还差东西，按上面的提示装完再跑一次。')
sys.exit(1)
