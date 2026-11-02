# -*- coding: utf-8 -*-
"""跨平台定位 node / npm —— 不写死任何本机路径

为什么单独抽一个文件：
别人机器上 node 可能装在 Program Files、可能用 nvm、可能是解压某个盘的
nodejs 目录（手动安装的常见放法）。写死一个路径等于对多数人失效。

用法:
    from nodebin import node_bin, npm_bin
    node_bin()      # -> 'C:\\Program Files\\nodejs\\node.exe' 或 None
    npm_bin()       # -> 'C:\\Program Files\\nodejs\\npm.cmd'  或 None

探测顺序（先命中先返回）：
    1. PATH
    2. 环境变量 NODE_HOME / NODEJS_HOME / NODE_BIN
    3. 各平台常见安装位
    4. Windows 下各盘根目录的 nodejs 子目录：
         解压安装常放在某个盘根目录，装完手动加 PATH，脚本进程未必继承到
"""
import os
import shutil
import string
import sys


def _exe_names(exe):
    """带扩展名的候选名：Windows 下 node 可能是 node.exe，npm 是 npm.cmd"""
    names = [exe]
    if os.name == 'nt':
        for ext in ('.exe', '.cmd', '.bat'):
            if not exe.lower().endswith(ext):
                names.append(exe + ext)
    return names


def _env(name, default=''):
    v = os.environ.get(name)
    return v if v else default


def _roots():
    """各平台常见安装根目录"""
    roots = []
    if os.name == 'nt':
        for key, sub in (
            ('ProgramFiles', 'nodejs'),
            ('ProgramFiles(x86)', 'nodejs'),
            ('LOCALAPPDATA', os.path.join('Programs', 'nodejs')),
            ('APPDATA', 'npm'),
        ):
            base = _env(key)
            if base:
                roots.append(os.path.join(base, sub))
        # 各盘根目录的 nodejs：手动解压安装的常见放法
        for d in string.ascii_uppercase:
            drive = d + ':' + os.sep
            try:
                if os.path.exists(drive):
                    roots.append(os.path.join(drive, 'nodejs'))
            except OSError:
                pass
    else:
        roots.extend(['/usr/local/bin', '/usr/bin', '/opt/homebrew/bin', '/opt/local/bin'])
        nvm = os.path.expanduser('~/.nvm/versions/node')
        if os.path.isdir(nvm):
            try:
                for v in sorted(os.listdir(nvm), reverse=True):
                    roots.append(os.path.join(nvm, v, 'bin'))
            except OSError:
                pass
        fnm = os.path.expanduser('~/.local/share/fnm/node-versions')
        if os.path.isdir(fnm):
            try:
                for v in sorted(os.listdir(fnm), reverse=True):
                    roots.append(os.path.join(fnm, v, 'installation', 'bin'))
            except OSError:
                pass
    return roots


def find(exe, hinted=None):
    """找可执行文件，返回绝对路径或 None

    hinted: 用户显式指定的路径，优先级最高
    """
    if hinted:
        for cand in [hinted] + [hinted + e for e in ('.exe', '.cmd')]:
            if os.path.isfile(cand):
                return cand
    names = _exe_names(exe)

    # 1) PATH
    for n in names:
        p = shutil.which(n)
        if p:
            return p

    # 2) 环境变量
    for env in ('NODE_HOME', 'NODEJS_HOME', 'NODE_BIN'):
        root = os.environ.get(env)
        if not root:
            continue
        for n in names:
            p = os.path.join(root, n)
            if os.path.isfile(p):
                return p

    # 3) 常见安装位
    for root in _roots():
        if not root:
            continue
        for n in names:
            p = os.path.join(root, n)
            try:
                if os.path.isfile(p):
                    return p
            except OSError:
                continue

    return None


def node_bin(hinted=None):
    return find('node', hinted)


def npm_bin(hinted=None):
    return find('npm', hinted)


def probe_line():
    """给 --check 输出用的一行摘要，不暴露多余环境信息"""
    n = node_bin()
    return {'node': n, 'npm': npm_bin(), 'ok': bool(n)}


if __name__ == '__main__':
    import json
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(probe_line(), ensure_ascii=False, indent=1))
