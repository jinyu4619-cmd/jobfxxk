# -*- coding: utf-8 -*-
"""启动检查：一次只报一个卡点，修完再报下一个

用法:
  python scripts/setup.py --welcome             # 开场白（纯文本，原样念给用户）
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
  no_node → no_resume → no_deps → no_anchors → ready

岗位库不在状态机里：那张表是实时更新的，每次找岗前现拉，
本地缓存只是中间产物，不算「就绪」的前提。
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paths import CONFIG_PATH, DATA_DIR, REPO
from nodebin import node_bin as _probe_node, npm_bin as _probe_npm

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
    return _probe_node()


def _mcp_configured():
    """可选步骤：Playwright MCP 接没接上。只读配置，不跑 npm，不阻塞状态机。

    返回 True / False / None（探测失败，当作不知道）
    """
    try:
        import setup_mcp
        path, _ = setup_mcp.pick_config()
        if not path or not os.path.isfile(path):
            return False
        cfg = setup_mcp.read_cfg(path)
        return setup_mcp.ENTRY in (cfg.get('mcpServers') or {})
    except Exception:
        return None


def p(v):
    return (v or '').replace('/', os.sep)


def has_pypdf():
    import importlib.util
    return importlib.util.find_spec('pypdf') is not None


def word_available():
    """不启动 Word，只查注册表——比 COM 快两个数量级，也不弹窗

    没装 Word 不会卡住前面几个阶段，但到「微调导出 PDF」那一步必然失败。
    等走到那一步才报，用户前面白忙一场，所以在启动时就提醒。
    """
    if os.name != 'nt':
        return ''
    import winreg
    for prog, label in (('Word.Application', 'Microsoft Word'),
                        ('KWPS.Application', 'WPS 文字'),
                        ('Kwps.Application', 'WPS 文字')):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog))
            return label
        except OSError:
            continue
    return ''


def env_warnings():
    """不阻塞、但必须让用户早知道的环境问题"""
    w = []
    if not word_available():
        w.append('没检测到 Word / WPS：后面「导出 PDF」那一步会失败，建议先装一个')
    c = cfg_read()
    if not (c.get('browser_path') or '').strip():
        w.append('还没选定浏览器：第一次投递时会从本机已装的浏览器里挑一个')
    return w


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
    if not playwright_ok():
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


def print_status():
    """一条命令看清现状：装到哪、几条线、每条在哪、下一步做什么

    为什么要有它：恢复到「上次做到哪儿」原本要翻 state.json、git log、记忆文件三处，
    读完还得自己拼。拼出来的还得一致。这件事机器做比人做快，也比人做准。
    """
    st = build_state()
    line = 'jobfxxk 现状'
    print(line)
    print('=' * len(line))
    print()
    mark = lambda ok: '✓' if ok else '✗'
    print('安装：%s' % ('就绪' if st.get('ready') else '未就绪（%s）' % st.get('status', '')))
    for w in st.get('warnings') or []:
        print('      ! ' + w)
    print('数据：%s' % DATA_DIR)

    cache_h = st.get('jobs_cache_hours')
    if cache_h is None:
        print('岗位库：还没同步过')
    else:
        hint = '（超过 12 小时，筛岗前建议先同步）' if cache_h > 12 else ''
        print('岗位库：%s 小时前同步%s' % (int(cache_h), hint))

    anchors = path_or('anchors', os.path.join('user', '锚点表.json'))
    print('简历：母版 %s｜锚点表 %s' % (
        mark(resume_ready()[0]), mark(os.path.exists(anchors))))

    jobs = jobs_summary()
    print()
    if not jobs:
        print('在投的线：还没有。')
    else:
        print('在投的线（%d 条）：' % len(jobs))
        for i, j in enumerate(jobs, 1):
            todo = ('｜待办：' + '、'.join(j['todo'][:3])) if j.get('todo') else ''
            login = {'done': '｜已登录', 'pending': '｜待登录', 'failed': '｜登录失败'}.get(
                j.get('login'), '')
            print('  %d. %s｜%s  →  %s%s%s' % (
                i, j.get('company') or '（未写公司）', j.get('position') or '（未写岗位）',
                j.get('phase') or '（未开始）', login, todo))

    print()
    if not st.get('ready'):
        print('下一步：' + (st.get('ask') or '按状态机提示补齐'))
    elif jobs:
        todo_any = [j for j in jobs if j.get('todo')]
        print('下一步：接着做「%s」这条。' % (todo_any[0].get('company') if todo_any else
                                         jobs[-1].get('company')))
    else:
        print('下一步：可以开始找岗或直接投你指定的公司。')


def build_state():
    """对外入口：状态机结论 + 非阻塞提醒"""
    d = _build_state()
    d['warnings'] = env_warnings()
    return d


def jobs_summary():
    try:
        p = os.path.join(DATA_DIR, 'user', 'state.json')
        if not os.path.exists(p):
            return []
        d = json.load(open(p, encoding='utf-8-sig'))
        jobs = d.get('jobs')
        if isinstance(jobs, list):
            return jobs
        if d.get('company') or d.get('phase'):
            return [{'company': d.get('company', ''), 'position': d.get('position', ''),
                     'phase': d.get('phase', ''), 'todo': d.get('todo') or []}]
    except Exception:
        pass
    return []


# ---------- 开场白 ----------
# 写死在代码里，不交给模型自由发挥：同一个东西，换个模型说话就不一样，
# 用户第一次接触到的印象就会飘。这里给它一段固定的、可逐字念的话。

WELCOME_FIRST = """\
古人曾云，不是不找工作，而是缓找，慢找，有节奏的找。牛棚虽多，奈何待拴小牛过多，就祝我们都能找到适合自己的牛绳。

我来帮你投简历。

先把话说清楚，你再决定要不要继续。

**我会做的**：把你手上的简历整理成一份信息库，之后所有网申表格都从这儿取数；按每个岗位的 JD 微调简历，版式和你原来的一模一样；从岗位库里挑出匹配的岗位；打开招聘官网，把能填的字填好；把每次投递记进进度表。

**需要你做的**：扫码登录招聘网站；点掉那些我没法代填的控件（自定义下拉、日期、单选）；最后按「提交」——这个我永远不替你按。

**我不会做的**：不编造你没做过的经历，不改你简历上任何一个数字，不把你的信息传到任何地方。身份证这类敏感信息默认只加密存在你自己电脑上，你也可以选择全部自己填。

现在开始。第一件事是看你这台机器缺什么，十几秒。"""

WELCOME_BACK = """\
欢迎回来。

上次停在：{phase}{where}。{todo}

接着往下走？"""

WELCOME_READY = """\
已经就绪了，随时可以开始。

这次想投什么岗位？如果没有明确想法，我可以从你的经历里推几个方向给你看。"""


def load_state():
    p = os.path.join(DATA_DIR, 'user', 'state.json')
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding='utf-8-sig'))
    except Exception:
        return {}


def welcome_text():
    st = load_state()
    if not os.path.exists(CFG_PATH):
        return WELCOME_FIRST
    phase = (st.get('phase') or '').strip()
    if phase:
        where = ''
        if st.get('company') or st.get('position'):
            where = '，{}｜{}'.format(st.get('company') or '', st.get('position') or '')
        todo = ''
        if st.get('todo'):
            todo = '还差：' + '；'.join(st['todo'][:3]) + '。'
        return WELCOME_BACK.format(phase=phase, where=where, todo=todo)
    return WELCOME_READY


def _build_state():
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
    # 岗位库不进「就绪」门槛：那张表是实时更新的，每次找岗前现拉，
    # 本地缓存只是中间产物，不该被当成必须存在的资产。
    cache_h = jobs_fresh_hours()

    if not ok:
        return dict(status='no_resume', ready=False, blocking=True,
                    ask='把你的简历给我，最省事的是 Word 版（.docx）——我拿它改字，PDF 我可以自己导。'
                        '只有 PDF 也行，但要你先在 Word 里打开它、另存为 .docx 再给我；'
                        '不然排版我改不动。两样都没有的话，我用内置模板起步。',
                    actions=[dict(id='use_template', label='用内置模板起步'),
                             dict(id='set_resume', label='我有 Word 简历（PDF 有就给，没有我导）',
                                  args='--docx 路径 [--pdf 路径] [--name 姓名]')],
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

    suggestions = []
    if _mcp_configured() is False:
        suggestions.append(dict(
            id='apply_mcp',
            label='配上 Playwright MCP',
            why='不配也能用，但遇到自定义下拉、虚拟滚动列表这类控件，'
                '只能靠坐标点，容易点空、一个字段卡几分钟。配了之后走无障碍树，'
                '按元素引用点。可随时补。',
            cmd='python scripts/setup_mcp.py --apply',
        ))

    return dict(status='ready', ready=True, blocking=False,
                ask='可以开始了。你想投什么岗位？',
                actions=[], done=done, missing=[],
                suggestions=suggestions,
                data_dir=DATA_DIR, config_path=CFG_PATH,
                jobs_cache_hours=(round(cache_h, 1) if cache_h is not None else None),
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


def word_export_pdf(docx, pdf):
    """用 Word 把 docx 导成 pdf。返回 (ok, 说明)"""
    ps = '''
$ErrorActionPreference = "Stop"
$w = $null; $d = $null
try {
  $w = New-Object -ComObject Word.Application
  $w.Visible = $false; $w.DisplayAlerts = 0
  $d = $w.Documents.Open("%s", $false, $true)
  $d.ExportAsFixedFormat("%s", 17)
  Write-Output ("PAGES=" + $d.ComputeStatistics(2))
}
catch { Write-Output ("ERR=" + $_.Exception.Message) }
finally {
  if ($d -ne $null) { try { $d.Close(0) } catch {} }
  if ($w -ne $null) { try { $w.Quit() } catch {} }
}
''' % (docx.replace('"', '`"'), pdf.replace('"', '`"'))
    f = os.path.join(DATA_DIR, 'tmp', '_export.ps1')
    os.makedirs(os.path.dirname(f), exist_ok=True)
    open(f, 'w', encoding='utf-8-sig').write(ps)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', f],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, 'Word 超过 120 秒没响应（可能是弹了对话框，或者文档太大）'
    out = ((r.stdout or '') + (r.stderr or '')).strip()
    if 'ERR=' in out:
        return False, out[out.index('ERR='):][:200]
    if not os.path.exists(pdf):
        return False, '没生成 PDF'
    return True, out


def act_set_resume(args):
    docx = p(args.get('docx'))
    pdf = p(args.get('pdf'))
    name = args.get('name')

    if not docx:
        sys.exit('要 --docx 指定你的 Word 简历。只有 PDF 的话，先在 Word 里打开它、另存为 .docx，再来。')

    if not os.path.exists(docx):
        sys.exit(f'找不到这个文件：{docx}')
    if not docx.lower().endswith('.docx'):
        sys.exit(f'要 .docx 文件，你给的是：{docx}')

    # 没有 pdf 就用 Word 现导一份——版式基准和上传件都靠它
    if not pdf:
        stem = os.path.splitext(os.path.basename(docx))[0]
        pdf = os.path.join(os.path.dirname(os.path.dirname(docx)), 'pdf', stem + '.pdf')
        os.makedirs(os.path.dirname(pdf), exist_ok=True)
        print(f'没给 PDF，我用 Word 从 docx 导一份：{pdf}')
        ok, msg = word_export_pdf(docx, pdf)
        if not ok:
            sys.exit('自动导出 PDF 失败：' + msg + '\n可以自己在 Word 里「另存为 PDF」，再把路径给我。')
        print('  导出完成 ' + msg.strip())

    if not os.path.exists(pdf):
        sys.exit(f'找不到这个 PDF：{pdf}')

    name = name or os.path.splitext(os.path.basename(docx))[0]
    resume_dir = os.path.dirname(docx)
    if os.path.basename(resume_dir).lower() in ('docx', 'word', '文档'):
        resume_dir = os.path.dirname(resume_dir)
    _write_base_cfg(name, docx, pdf, resume_dir)
    print(f'已记下简历：\n  {docx}\n  {pdf}')
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
    shutil.copy(TEMPLATE_DOCX, docx)
    shutil.copy(TEMPLATE_PDF, pdf)
    # 这两份天生是一对，把修改时间对齐，免得被「Word 比 PDF 新」的检查误判
    import time
    now = time.time()
    for p in (docx, pdf):
        os.utime(p, (now, now))
    _write_base_cfg(name, docx, pdf, resume_dir)
    print(f'已用内置模板起步：{docx}')
    print('这个模板是固定骨架（3 段实习 / 2 个项目 / 1 段校园），')
    print('装不下你的经历时，用 Word 打开它自己加删条目，或者换成你自己的 docx+pdf。')


def playwright_ok():
    """和运行期同一套解析规则：仓库里的 node_modules 或数据目录的 deps 都算"""
    node = node_bin()
    if not node:
        return False
    probe = "require('./ensure_deps.cjs');require('playwright');console.log('ok')"
    try:
        r = subprocess.run([node, '-e', probe], capture_output=True, text=True,
                           timeout=30, cwd=HERE)
        return 'ok' in (r.stdout or '')
    except Exception:
        return False


def act_install_deps(_):
    # Playwright 装到数据目录，不往技能目录里写——托管目录可能是只读的，升级也会被替换
    if not playwright_ok():
        deps = os.path.join(DATA_DIR, 'deps')
        os.makedirs(deps, exist_ok=True)
        print(f'正在装 Playwright 到 {deps} …')
        npm = _probe_npm()
        if not npm:
            sys.exit('找不到 npm。Node.js 可能装得不完整，重装一次 nodejs.org 上的 LTS 版再来。')
        # 只装库，不下载浏览器：我们用 connectOverCDP 连用户自己装的 Edge/Chrome，
        # 自带那套 Chromium + Firefox + WebKit 有 200MB 上下，装完一次都不会用。
        # 实测这一步原来是安装耗时的大头（约两分钟，绝大多数花在下载浏览器）。
        env = dict(os.environ)
        env['PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD'] = '1'
        r = subprocess.run([npm, 'install', 'playwright', '--prefix', deps],
                           capture_output=True, text=True, shell=(os.name == 'nt'),
                           timeout=900, env=env)
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


def cache_untranslated():
    """同步时如果拿不到字段元数据，城市/学历会留成 optXXX 内部 ID，这时才需要单独翻译"""
    import re
    cache = path_or('job_cache', os.path.join('data', 'jobs.json'))
    if not os.path.exists(cache):
        return False
    try:
        d = json.load(open(cache, encoding='utf-8-sig'))
    except Exception:
        return True
    for r in (d.get('rows') or [])[:200]:
        for v in r.values():
            if isinstance(v, str) and re.search(r'\bopt[A-Za-z0-9]{4,}', v):
                return True
    return False


def act_sync_jobs(_):
    node = node_bin()
    print('同步岗位库，大约一分半…')
    r = subprocess.run([node, os.path.join(HERE, 'sync_jobs.cjs'), '--only=jobs'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=ROOT)
    tail = ((r.stdout or '') + (r.stderr or '')).strip().splitlines()
    print('\n'.join(tail[-6:]))
    if r.returncode != 0:
        sys.exit('岗位库同步失败')

    # 同步时通常已经把选项翻译好了（元数据就在同一次会话里），
    # 只有没抓到元数据、留了 optXXX 的时候才需要再跑一遍翻译。
    if not cache_untranslated():
        print('选项已经是中文，跳过翻译')
        return
    print('缓存里还有未翻译的选项，补一次翻译…')
    r2 = subprocess.run([sys.executable, os.path.join(HERE, 'translate_jobs.py')],
                        capture_output=True, text=True, encoding='utf-8', errors='replace')
    out2 = (r2.stdout or '').strip().splitlines()
    print('\n'.join(out2[-4:]))
    if r2.returncode != 0:
        sys.exit('选项翻译失败：' + ((r2.stderr or r2.stdout or '')[-200:]))


def act_apply_mcp(_):
    """可选：把 Playwright MCP 接进 Claude 桌面版（不装也能用）"""
    r = subprocess.run([sys.executable, os.path.join(HERE, 'setup_mcp.py'), '--apply'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print((r.stdout or '').rstrip())
    if r.returncode != 0:
        print((r.stderr or '').rstrip())
        sys.exit('配 Playwright MCP 失败')


ACTIONS = {
    'init_data_dir': act_init_data_dir,
    'set_resume': act_set_resume,
    'use_template': act_use_template,
    'install_deps': act_install_deps,
    'make_anchors': act_make_anchors,
    'sync_jobs': act_sync_jobs,
    'apply_mcp': act_apply_mcp,
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
    # 版本太低的话，后面所有脚本都会以各种奇怪的方式失败，不如在这里直接说清楚
    if sys.version_info < (3, 9):
        print('需要 Python 3.9 或更高，现在这台是 %s。' % sys.version.split()[0])
        print('到 python.org 下新版，安装时记得勾上「Add Python to PATH」。')
        return 2

    argv = sys.argv[1:]
    if '--status' in argv:
        print_status()
        return 0
    if '--welcome' in argv:
        print(welcome_text())
        return 0
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
