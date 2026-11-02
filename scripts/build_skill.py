# -*- coding: utf-8 -*-
"""把仓库打成一个可以「保存技能」的 .skill 包

用法:
  python scripts/build_skill.py                    # 产出 dist/jobfxxk.skill
  python scripts/build_skill.py --check            # 只检查仓库结构，不打包
  python scripts/build_skill.py --verify           # 拿 dist 里的包和仓库对账
  python scripts/build_skill.py --verify 别的包.skill

.skill 就是一个 zip：根目录放 SKILL.md，scripts/ references/ assets/ 一起打进去。
装的时候不用碰文件系统，桌面端点「保存技能」就行。

**个人数据绝不进包**：config.json、jobs.json、user/、browser-profile/ 全部排除。

为什么要有 --verify：包是手工一步打出来的，加了脚本忘记重打，仓库里就会同时
躺着两份代码——今天 pack 缺 4 个脚本，就是这样过了 16 小时没人发现。
对账不是对清单：逐文件比哈希，少了的、多出来的、改过的都点出来。
"""
import hashlib, os, re, sys, zipfile
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

sys.path.insert(0, HERE)
try:
    from paths import DATA_DIR
except Exception:          # 没有配置时也能打包，只是查不了脱敏名单
    DATA_DIR = os.path.join(os.path.expanduser('~'), 'jobfxxk-data')

# 个人信息的机械特征：能靠正则抓的
PII_PATTERNS = [
    ('手机号', re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')),
    ('身份证号', re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')),
    ('邮箱', re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')),
]
# 这几种邮箱不算个人信息
EMAIL_OK = re.compile(r'@(users\.noreply\.github\.com|noreply|example\.com|github\.com)$', re.I)
# 文档里本来就该出现的占位值
FAKE_OK = {'a@b.com', '13800138000', '13000000000', '13900000000'}
# 行里带这些词，说明是在举例子而不是真信息
FAKE_HINT = ('示例', '例如', '比如', '格式', '请填写', '占位', 'placeholder',
             'PLACEHOLDERS', '张三', '某某', 'XXX')


def deny_terms():
    """个人化的词（姓名、学校、公司）放仓库外面，一行一个

    为什么写文件而不是写进代码：把「要脱敏的词」写进公开仓库，本身就是泄露。
    位置：<数据目录>/user/脱敏名单.txt
    """
    p = os.path.join(DATA_DIR, 'user', '脱敏名单.txt')
    if not os.path.exists(p):
        return []
    try:
        return [l.strip() for l in open(p, encoding='utf-8-sig')
                if l.strip() and not l.strip().startswith('#')]
    except OSError:
        return []


def pii_scan(files):
    """打包前扫一遍：这包是给陌生人看的，不能带个人信息"""
    problems, terms = [], deny_terms()
    for f in files:
        if not f.lower().endswith(('.md', '.py', '.cjs', '.json', '.txt')):
            continue
        try:
            body = open(os.path.join(ROOT, f.replace('/', os.sep)),
                        encoding='utf-8-sig', errors='replace').read()
        except OSError:
            continue
        for no, line in enumerate(body.splitlines(), 1):
            if any(h in line for h in FAKE_HINT):
                continue          # 举例用的行，不算泄露
            for label, pat in PII_PATTERNS:
                for m in pat.finditer(line):
                    if m.group(0) in FAKE_OK:
                        continue
                    if label == '邮箱' and EMAIL_OK.search(m.group(0)):
                        continue
                    problems.append('%s:%d 疑似%s：%s' % (f, no, label, m.group(0)[:30]))
            for t in terms:
                if t in line:
                    problems.append('%s:%d 命中脱敏名单「%s」' % (f, no, t))
    return problems[:12]

INCLUDE_DIRS = ['scripts', 'references', 'assets']
INCLUDE_FILES = ['SKILL.md', 'AGENTS.md', 'README.md', 'LICENSE', 'config.example.json', 'package.json']
SKIP_DIRS = {'__pycache__', 'node_modules', '.git', 'data', 'user', 'dist', 'docs',
             'browser-profile', '待排版', '微调版'}
SKIP_FILES = {'config.json', '.DS_Store', 'Thumbs.db'}
SKIP_EXT = {'.pyc', '.log', '.bin'}


def collect():
    files = []
    for name in INCLUDE_FILES:
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            files.append(name)
    for d in INCLUDE_DIRS:
        base = os.path.join(ROOT, d)
        for root, dirs, names in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in names:
                if f in SKIP_FILES or os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                rel = os.path.relpath(os.path.join(root, f), ROOT)
                files.append(rel.replace(os.sep, '/'))
    return sorted(files)


def check(files):
    problems = []
    if 'SKILL.md' not in files:
        problems.append('根目录没有 SKILL.md，Claude 端装不成技能')
    if 'AGENTS.md' not in files:
        problems.append('根目录没有 AGENTS.md，Codex 端没有入口')
    need = ['scripts/setup.py', 'scripts/profile.py', 'scripts/read_resume.py',
            'scripts/compose.py', 'scripts/verify.py', 'scripts/fill_engine.py',
            'scripts/lint_draft.py', 'scripts/gate.py',
            'scripts/directions.py', 'scripts/recommend.py',
            'scripts/sync_jobs.cjs', 'scripts/browser.cjs',
            'assets/template/简历模板.docx', 'assets/template/简历模板.pdf']
    for n in need:
        if n not in files:
            problems.append('缺关键文件：' + n)

    # 文档里点了名的脚本必须真在仓库里——改过名、删过文件，这一条会先叫
    docs = ['SKILL.md', 'AGENTS.md', 'README.md']
    ref_dir = os.path.join(ROOT, 'references')
    if os.path.isdir(ref_dir):
        docs += [os.path.join('references', f) for f in sorted(os.listdir(ref_dir))
                 if f.endswith('.md')]
    try:
        doc = ''.join(open(os.path.join(ROOT, f), encoding='utf-8').read() for f in docs)
    except OSError:
        doc = ''
    for m in sorted(set(re.findall(r'scripts/[\w\-]+\.(?:py|cjs)', doc))):
        if 'xxx' in m:
            continue
        if m not in files:
            problems.append('文档里提到但仓库里没有：' + m)

    # 文档里写的子命令和长参数，脚本里得真有。文档跑在脚本前面，两边会各自漂
    for m in re.finditer(r'(?:python|node)\s+(scripts/[\w\-]+\.(?:py|cjs))([^\n`]*)', doc):
        script, rest = m.group(1), m.group(2)
        if 'xxx' in script or script not in files:
            continue
        path = os.path.join(ROOT, script.replace('/', os.sep))
        if not os.path.exists(path):
            continue
        src = open(path, encoding='utf-8-sig', errors='replace').read()
        toks = set(re.findall(r'(--[A-Za-z][\w\-]*)', src)) | \
            set(re.findall(r"""['"]([A-Za-z][\w\-]{1,24})['"]""", src))
        unknown = []
        for raw in rest.split('#')[0].split():
            t = raw.strip('"\'`')
            if not t:
                continue
            if t.startswith('--'):
                if t.split('=')[0] not in toks:
                    unknown.append(t.split('=')[0])
            elif not t.startswith('-') and re.fullmatch(r'[a-z][a-z\-]{1,20}', t) \
                    and t not in toks:
                unknown.append(t)
        if unknown:
            problems.append(f'文档里的命令参数脚本不认：{script} {"、".join(sorted(set(unknown)))}')

    bad = [f for f in files if f.endswith('config.json') or '/user/' in f
           or f.startswith('data/') or 'browser-profile' in f]
    if bad:
        problems.append('这些文件不该进包：' + '、'.join(bad[:5]))

    # 最后一道：个人信息扫描。宁可打不出包，也不要发一份带真名/手机号的文档出去。
    for p in pii_scan(files):
        problems.append('疑似个人信息：' + p)
    return problems


def sha1_bytes(b):
    return hashlib.sha1(b).hexdigest()


# 文本文件按「统一成 LF」再算哈希。
# 为什么：Windows 上 core.autocrlf 会把工作区的换行改成 CRLF，git checkout 前后
# 内容一模一样，哈希却变了——对账会报一堆假漂移，报几次就没人信这个检查了。
TEXT_EXT = {'.md', '.py', '.cjs', '.json', '.txt', '.yml', '.yaml', '.example'}


def digest(name, data):
    if os.path.splitext(name)[1].lower() in TEXT_EXT:
        data = data.replace(b'\r\n', b'\n')
    return sha1_bytes(data)


def verify(path):
    """拿包里的内容和仓库当前状态逐文件对账"""
    if not os.path.exists(path):
        print(f'找不到包：{path}\n先跑 python scripts/build_skill.py 打一份。')
        return 1
    try:
        with zipfile.ZipFile(path) as z:
            pkg = {n: digest(n, z.read(n)) for n in z.namelist() if not n.endswith('/')}
    except zipfile.BadZipFile:
        print(f'这不是个正常的 .skill（zip）：{path}')
        return 1

    repo = {}
    for rel in collect():
        with open(os.path.join(ROOT, rel.replace('/', os.sep)), 'rb') as f:
            repo[rel] = digest(rel, f.read())

    missing = sorted(set(repo) - set(pkg))      # 仓库有、包里没有
    extra = sorted(set(pkg) - set(repo))        # 包里有、仓库没有
    changed = sorted(k for k in set(repo) & set(pkg) if repo[k] != pkg[k])

    print(f'=== 对账：{os.path.basename(path)} ===\n')
    if not (missing or extra or changed):
        print(f'包和仓库一致：{len(pkg)} 个文件，逐个哈希相同 ✓')
        return 0

    def show(title, items, limit=12):
        print(f'{title}（{len(items)} 个）：')
        for x in items[:limit]:
            print('  · ' + x)
        if len(items) > limit:
            print(f'  … 还有 {len(items) - limit} 个')
        print()

    if missing:
        show('代码比包新，包里没有', missing)
    if changed:
        show('两边同名但内容不同', changed)
    if extra:
        show('包里有、仓库已经删了', extra)

    print('这个包不能发。重打：python scripts/build_skill.py')
    return 1



def main():
    argv = sys.argv
    if '--verify' in argv:
        i = argv.index('--verify')
        nxt = argv[i + 1] if i + 1 < len(argv) else ''
        path = nxt if nxt and not nxt.startswith('--') else os.path.join(ROOT, 'dist', 'jobfxxk.skill')
        return verify(path)

    files = collect()
    problems = check(files)
    print(f'将打包 {len(files)} 个文件：')
    for f in files[:12]:
        print('  ' + f)
    if len(files) > 12:
        print(f'  … 还有 {len(files) - 12} 个')

    if problems:
        print('\n结构有问题，先修：')
        for p in problems:
            print('  ✗ ' + p)
        return 1
    print('\n结构检查通过：SKILL.md 在根、关键脚本齐、没有个人数据。')

    if '--check' in sys.argv:
        return 0

    dist = os.path.join(ROOT, 'dist')
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, 'jobfxxk.skill')
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(os.path.join(ROOT, f.replace('/', os.sep)), f)
    size = os.path.getsize(out) / 1024 / 1024
    print(f'\n已生成：{out}（{size:.1f}MB）')
    print('发布方式：把这个文件挂到 GitHub Release，用户下载后点「保存技能」。')
    return verify(out)          # 打完自己再对一遍账，别发出一份缺文件的包


if __name__ == '__main__':
    sys.exit(main())
