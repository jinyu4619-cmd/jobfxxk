# -*- coding: utf-8 -*-
"""启动检查：一次只报一个卡点，修完再报下一个

用法:
  python scripts/setup.py --check
  python scripts/setup.py --apply <动作> [参数]

--check 输出 JSON，给 agent 读，不要自己解读环境：
  {
    "status": "no_resume",
    "ready": false,
    "ask": "……可以直接念给用户的一句话",
    "actions": [{"id": "use_template", "label": "用内置模板"}],
    "done": ["node", "data_dir"],
    "missing": ["resume"]
  }

状态按顺序检查，一次只返回最靠前的一个：
  no_node → no_resume → no_deps → no_anchors → no_jobs → ready
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CONFIG_PATH, DATA_DIR, REPO

TEMPLATE_DOCX = os.path.join(REPO, 'assets', 'template', '简历模板.docx')
TEMPLATE_PDF = os.path.join(REPO, 'assets', 'template', '简历模板.pdf')

CFG_PATH = CONFIG_PATH or os.path.join(DATA_DIR, 'config.json')


# ---------- 小工具 ----------

def cfg_read():
    if not os.path.exists(CFG_PATH):
        return {}
    try:
        return json.load(open(CFG_PATH, encoding='utf-8-sig'))
    except Exception:
        return {}


def cfg_write(d):
    os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
    json.dump(d, open(CFG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)


def out(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=1))


def node_bin():
    return shutil.which('node') or (r'D:\nodejs\node.exe' if os.name == 'nt' else None)


def p(v):
    return (v or '').replace('/', os.sep)


def has_pypdf():
    import importlib.util
    return importlib.util.find_spec('pypdf') is not None


def path_or(default_key, default_rel):
    c = cfg_read()
    return p(c.get(default_key)) or os.path.join(DATA_DIR, default_rel)


# ---------- 各项检查 ----------

def resume_ready():
    c = cfg_read()
    docx, pdf = p(c.get('master_docx')), p(c.get('master_pdf'))
    if not docx or not pdf:
        return False, '还没配简历'
    if not os.path.exists(docx):
        return False, f'母版 docx 找不到：{docx}'
    if not os.path.exists(pdf):
        return False, f'母版 pdf 找不到：{pdf}'
    return True, ''


def deps_missing():
    miss = []
    if not has_pypdf():
        miss.append('pypdf')
    node = node_bin()
    if not node:
        return miss + ['playwright']
    try:
        r = subprocess.run([node, '-e', "require('playwright');console.log('ok')"],
                           capture_output=True, text=True, timeout=30, cwd=REPO)
        if 'ok' not in (r.stdout or ''):
            miss.append('playwright')
    except Exception:
        miss.append('playwright')
    return miss


def jobs_fresh_hours():
    cache = path_or('job_cache', os.path.join('data', 'jobs.json'))
    if not os.path.exists(cache):
        return None
    import time
    return (time.time() - os.path.getmtime(cache)) / 3600


def profile_missing_count():
    """档案还缺几项必填。档案由对话逐条确认，不阻塞启动，但要报出来。"""
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, 'profile.py'), 'missing'],
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
        return json.loads(r.stdout).get('required_count', 0)
    except Exception:
        return None


def build_state():
    done, missing = [], []

    node = node_bin()
    node_ok = False
    if node:
        try:
            r = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=15)
            node_ok = bool((r.stdout or '').strip())
        except Exception:
            node_ok = False
    (done if node_ok else missing).append('node')

    if not node_ok:
        return dict(status='no_node', ready=False, blocking=True,
                    ask='这台机器没找到 Node.js，自动填表要用它。到 nodejs.org 下 LTS 版，一路下一步，装完跟我说一声。',
                    actions=[], done=done, missing=missing,
                    data_dir=DATA_DIR, config_path=CFG_PATH)

    data_ok = os.path.isdir(DATA_DIR)
    (done if data_ok else missing).append('data_dir')

    ok, why = resume_ready()
    (done if ok else missing).append('resume')
    anchors = path_or('anchors', os.path.join('user', '锚点表.json'))
    (done if os.path.exists(anchors) else missing).append('anchors')
    cache_h = jobs_fresh_hours()
    (done if cache_h is not None else missing).append('jobs')

    if not ok:
        return dict(status='no_resume', ready=False, blocking=True,
                    ask='把你的简历给我：Word 版和 PDF 版各一份，两样都要——'
                        'docx 用来改字，pdf 用来量版式。只有一份的话，我会用内置模板起步，'
                        '你后面再换成自己的。',
                    actions=[dict(id='use_template', label='用内置模板起步'),
                             dict(id='set_resume', label='我有 docx 和 pdf',
                                  args='--docx 路径 --pdf 路径 [--name 姓名]')],
                    done=done, missing=missing, reason=why,
                    data_dir=DATA_DIR, config_path=CFG_PATH)

    miss = deps_missing()
    if miss:
        size = '约 10MB' if miss == ['playwright'] else '约 10MB 加一个小库'
        return dict(status='no_deps', ready=False, blocking=True,
                    ask=f'还差自动填表要用的组件（{"、".join(miss)}），{size}，要现在装吗？',
                    actions=[dict(id='install_deps', label='装')],
                    done=done, missing=missing,
                    data_dir=DATA_DIR, config_path=CFG_PATH)

    if not os.path.exists(anchors):
        return dict(status='no_anchors', ready=False, blocking=False,
                    ask='量一下你简历的行宽，好知道每个位置能放多少字，十几秒。',
                    actions=[dict(id='make_anchors', label='开始标定')],
                    done=done, missing=missing,
                    data_dir=DATA_DIR, config_path=CFG_PATH)

    if cache_h is None:
        return dict(status='no_jobs', ready=False, blocking=False,
                    ask='第一次用要同步岗位库，大概一分半，别关窗口。',
                    actions=[dict(id='sync_jobs', label='同步岗位库')],
                    done=done, missing=missing,
                    data_dir=DATA_DIR, config_path=CFG_PATH)

    return dict(status='ready', ready=True, blocking=False,
                ask='可以开始了。你想投什么岗位？',
                actions=[], done=done, missing=[],
                data_dir=DATA_DIR, config_path=CFG_PATH,
                jobs_cache_hours=round(cache_h, 1),
                profile_missing_required=profile_missing_count())


# ---------- 各项动作 ----------

def act_init_data_dir(_):
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f'数据目录：{DATA_DIR}')


def _write_base_cfg(name, docx, pdf, resume_dir):
    cfg = cfg_read()
    cfg.update({
        'name': name,
        'master_docx': docx.replace(os.sep, '/'),
        'master_pdf': pdf.replace(os.sep, '/'),
        'resume_dir': resume_dir.replace(os.sep, '/'),
        'anchors': (cfg.get('anchors') or os.path.join(DATA_DIR, 'user', '锚点表.json')).replace(os.sep, '/'),
        'job_cache': (cfg.get('job_cache') or os.path.join(DATA_DIR, 'data', 'jobs.json')).replace(os.sep, '/'),
        'tracker': (cfg.get('tracker') or os.path.join(DATA_DIR, '投递台账.md')).replace(os.sep, '/'),
        'browser_name': cfg.get('browser_name', ''),
        'browser_path': cfg.get('browser_path', ''),
    })
    cfg_write(cfg)
    return cfg


def act_set_resume(args):
    docx = p(args.get('docx'))
    pdf = p(args.get('pdf'))
    if not docx or not pdf:
        sys.exit('要给 --docx 和 --pdf 两个路径。只有一份的话，用 --apply use_template。')
    for path, ext, label in [(docx, '.docx', 'Word 版'), (pdf, '.pdf', 'PDF 版')]:
        if not os.path.exists(path):
            sys.exit(f'{label}找不到：{path}')
        if not path.lower().endswith(ext):
            sys.exit(f'{label}应该是 {ext} 文件，你给的是：{path}')
    name = args.get('name') or os.path.splitext(os.path.basename(docx))[0]
    resume_dir = os.path.dirname(docx)
    # 常见布局是 简历库/docx/ 与 简历库/pdf/，简历库应该是它们上一级
    if os.path.basename(resume_dir).lower() in ('docx', 'word', '文档'):
        resume_dir = os.path.dirname(resume_dir)
    _write_base_cfg(name, docx, pdf, resume_dir)
    print(f'已记下简历：{docx}')
    print(f'            {pdf}')
    print(f'简历库：{resume_dir}')


def act_use_template(args):
    if not (os.path.exists(TEMPLATE_DOCX) and os.path.exists(TEMPLATE_PDF)):
        sys.exit('内置模板缺失，仓库不完整。重新下载一份。')
    name = args.get('name') or '我'
    resume_dir = os.path.join(DATA_DIR, '简历库')
    docx_dir = os.path.join(resume_dir, 'docx')
    pdf_dir = os.path.join(resume_dir, 'pdf')
    os.makedirs(docx_dir, exist_ok=True)
    os.makedirs(pdf_dir, exist_ok=True)
    stem = f'{name} - 简历模板'
    docx = os.path.join(docx_dir, stem + '.docx')
    pdf = os.path.join(pdf_dir, stem + '.pdf')
    shutil.copy2(TEMPLATE_DOCX, docx)
    shutil.copy2(TEMPLATE_PDF, pdf)
    _write_base_cfg(name, docx, pdf, resume_dir)
    print(f'已用内置模板起步：{docx}')
    print('这个模板是固定骨架（3 段实习 / 2 个项目 / 1 段校园），')
    print('装不下你的经历时，用 Word 打开它自己加删条目，或者换成你自己的 docx+pdf。')


def act_install_deps(_):
    node = node_bin()
    # Playwright
    try:
        r = subprocess.run([node, '-e', "require('playwright');console.log('ok')"],
                           capture_output=True, text=True, timeout=30, cwd=REPO)
        has_pw = 'ok' in (r.stdout or '')
    except Exception:
        has_pw = False
    if not has_pw:
        print('正在装 Playwright（npm install）…')
        r = subprocess.run(['npm', 'install'], cwd=REPO, capture_output=True, text=True,
                           shell=(os.name == 'nt'), timeout=600)
        if r.returncode != 0:
            sys.exit('npm install 失败：' + ((r.stderr or r.stdout or '')[-300:]))
        print('  完成')
    # pypdf
    if not has_pypdf():
        print('正在装 pypdf…')
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', 'pypdf'],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            sys.exit('pip install pypdf 失败：' + ((r.stderr or r.stdout or '')[-300:]))
        print('  完成')
    print('依赖装好了。')


def act_make_anchors(_):
    r = subprocess.run([sys.executable, os.path.join(HERE, 'make_anchors.py')],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print((r.stdout or '').rstrip())
    if r.returncode != 0:
        print((r.stderr or '').rstrip())
        sys.exit('标定失败')


def act_sync_jobs(_):
    node = node_bin()
    print('同步岗位库，大约一分半…')
    r = subprocess.run([node, os.path.join(HERE, 'sync_jobs.cjs'), '--only=jobs'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=ROOT)
    tail = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
    print('\n'.join(tail[-6:]))
    if r.returncode != 0:
        sys.exit('岗位库同步失败')


ACTIONS = {
    'init_data_dir': act_init_data_dir,
    'set_resume': act_set_resume,
    'use_template': act_use_template,
    'install_deps': act_install_deps,
    'make_anchors': act_make_anchors,
    'sync_jobs': act_sync_jobs,
}


def parse_args(argv):
    """把 --docx X --name Y 解析成字典"""
    d = {}
    i = 0
    while i < len(argv):
        k = argv[i]
        if k.startswith('--'):
            v = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith('--') else ''
            d[k[2:]] = v
            i += 2 if v else 1
        else:
            i += 1
    return d


def main():
    argv = sys.argv[1:]
    if not argv or '--check' in argv:
        out(build_state())
        return 0
    if '--apply' in argv:
        i = argv.index('--apply')
        if i + 1 >= len(argv):
            sys.exit('用法：--apply <动作> [--参数 值]')
        name = argv[i + 1]
        fn = ACTIONS.get(name)
        if not fn:
            sys.exit(f'不认识的动作：{name}\n可用：{"、".join(ACTIONS)}')
        fn(parse_args(argv[i + 2:]))
        print()
        out(build_state())
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main())
