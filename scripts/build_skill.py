# -*- coding: utf-8 -*-
"""把仓库打成一个可以「保存技能」的 .skill 包

用法:
  python scripts/build_skill.py               # 产出 dist/jobfxxk.skill
  python scripts/build_skill.py --check       # 只检查仓库结构，不打包

.skill 就是一个 zip：根目录放 SKILL.md，scripts/ references/ assets/ 一起打进去。
装的时候不用碰文件系统，桌面端点「保存技能」就行。

**个人数据绝不进包**：config.json、jobs.json、user/、browser-profile/ 全部排除。
"""
import os, sys, zipfile
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
            'scripts/sync_jobs.cjs', 'scripts/browser.cjs',
            'assets/template/简历模板.docx', 'assets/template/简历模板.pdf']
    for n in need:
        if n not in files:
            problems.append('缺关键文件：' + n)
    bad = [f for f in files if f.endswith('config.json') or '/user/' in f
           or f.startswith('data/') or 'browser-profile' in f]
    if bad:
        problems.append('这些文件不该进包：' + '、'.join(bad[:5]))
    return problems


def main():
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
    return 0


if __name__ == '__main__':
    sys.exit(main())
