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
if ok:
    print('配置检查通过，可以开始用了。')
    sys.exit(0)
print('有项目没配好，按上面的提示改完再跑一次。')
sys.exit(1)
