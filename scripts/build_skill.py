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
    try:
        doc = ''.join(open(os.path.join(ROOT, f), encoding='utf-8').read()
                      for f in ('SKILL.md', 'AGENTS.md', 'README.md'))
    except OSError:
        doc = ''
    for m in sorted(set(re.findall(r'scripts/[\w\-]+\.(?:py|cjs)', doc))):
        if 'xxx' in m:
            continue
        if m not in files:
            problems.append('文档里提到但仓库里没有：' + m)

    bad = [f for f in files if f.endswith('config.json') or '/user/' in f
           or f.startswith('data/') or 'browser-profile' in f]
    if bad:
        problems.append('这些文件不该进包：' + '、'.join(bad[:5]))
    return problems


def sha1_bytes(b):
    return hashlib.sha1(b).hexdigest()


def verify(path):
    """拿包里的内容和仓库当前状态逐文件对账"""
    if not os.path.exists(path):
        print(f'找不到包：{path}\n先跑 python scripts/build_skill.py 打一份。')
        return 1
    try:
        with zipfile.ZipFile(path) as z:
            pkg = {n: sha1_bytes(z.read(n)) for n in z.namelist() if not n.endswith('/')}
    except zipfile.BadZipFile:
        print(f'这不是个正常的 .skill（zip）：{path}')
        return 1

    repo = {}
    for rel in collect():
        with open(os.path.join(ROOT, rel.replace('/', os.sep)), 'rb') as f:
            repo[rel] = sha1_bytes(f.read())

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
