# -*- coding: utf-8 -*-
"""可选步骤：把 Playwright MCP 接进 Claude 桌面版

为什么值得配：
    不配的话，agent 只能靠截图算坐标去点页面。遇到自定义下拉、虚拟滚动列表
    （比如 51job 简历页那种 288px 高、overflow:hidden、选项画在可视区外的
    省市级联），坐标点上去等于点空，一个字段能卡几分钟。
    配上之后 agent 走无障碍树，拿的是元素引用，点的是元素本身。

用法:
    python scripts/setup_mcp.py --check             # 看要不要配、配没配上
    python scripts/setup_mcp.py --apply             # 装包 + 写配置（自动备份）
    python scripts/setup_mcp.py --undo              # 从配置里移除这一条
    python scripts/setup_mcp.py --apply --config <路径> --node <路径> --port 9222

--check 输出 JSON：
    {
      "status": "not_installed" | "no_config" | "installed_not_configured"
                | "configured" | "no_node",
      "ready": bool,
      "config_path": "……",
      "ask": "……可以直接念给用户的一句话",
      "actions": [{"id": "apply_mcp", "label": "配上"}]
    }

安全约定：
    - 改配置前一律先备份成 <原文件>.bak-<时间戳>
    - 只增删 mcpServers.playwright 这一条，其他条目一个字节不动
    - 写完回读校验，原有条目丢了就报错并把备份路径打出来
    - 不写死任何本机路径，全部运行时探测
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from nodebin import node_bin, npm_bin  # noqa: E402

ENTRY = 'playwright'
DEFAULT_PORT = 9222


def out(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# ---------- 找 Claude 桌面版配置 ----------

def config_candidates():
    """各平台配置文件候选路径，按优先级排列"""
    c = []
    if os.name == 'nt':
        appdata = os.environ.get('APPDATA') or ''
        local = os.environ.get('LOCALAPPDATA') or ''
        if appdata:
            c.append(os.path.join(appdata, 'Claude', 'claude_desktop_config.json'))
            c.append(os.path.join(appdata, 'Claude-3p', 'claude_desktop_config.json'))
        if local:
            c.append(os.path.join(local, 'Claude', 'claude_desktop_config.json'))
            c.append(os.path.join(local, 'Claude-3p', 'claude_desktop_config.json'))
            # 部分安装包把配置放在带随机后缀的目录下
            for p in glob.glob(os.path.join(local, 'Claude-Data', '*', 'claude_desktop_config.json')):
                c.append(p)
    elif sys.platform == 'darwin':
        home = os.path.expanduser('~')
        c.append(os.path.join(home, 'Library', 'Application Support', 'Claude',
                              'claude_desktop_config.json'))
    else:
        home = os.path.expanduser('~')
        c.append(os.path.join(home, '.config', 'Claude', 'claude_desktop_config.json'))
        c.append(os.path.join(home, '.config', 'claude-desktop',
                              'claude_desktop_config.json'))
    return c


def score(path):
    """配置文件的可信度：已存在 + 里面有 mcpServers + 条目越多越可信"""
    if not os.path.isfile(path):
        return -1
    try:
        d = json.load(open(path, encoding='utf-8-sig'))
    except Exception:
        return 0
    servers = d.get('mcpServers') or {}
    return 1 + len(servers)


def pick_config(explicit=None):
    """返回 (路径, 全部候选及打分)。explicit 优先。"""
    cands = config_candidates()
    scored = [(p, score(p)) for p in cands]
    if explicit:
        return explicit, scored
    best = None
    best_s = -1
    for p, s in scored:
        if s > best_s:
            best, best_s = p, s
    return (best if best_s > 0 else (cands[0] if cands else None)), scored


# ---------- 找 @playwright/mcp 的 cli.js ----------

def npm_global_root(npm):
    try:
        r = subprocess.run([npm, 'root', '-g'], capture_output=True, text=True,
                           timeout=60, encoding='utf-8', errors='replace')
        if r.returncode == 0:
            return r.stdout.strip().splitlines()[-1].strip()
    except Exception:
        pass
    return None


def find_mcp_cli(npm):
    root = npm_global_root(npm)
    if not root:
        return None
    p = os.path.join(root, '@playwright', 'mcp', 'cli.js')
    return p if os.path.isfile(p) else None


# ---------- 配置读写 ----------

def read_cfg(path):
    if not path or not os.path.isfile(path):
        return {}
    raw = open(path, encoding='utf-8-sig').read().strip()
    if not raw:
        return {}
    return json.loads(raw)


def backup(path):
    stamp = time.strftime('%Y-%m-%dT%H-%M-%S')
    bak = path + '.bak-' + stamp
    shutil.copy2(path, bak)
    return bak


def build_entry(node, cli, port):
    return {
        'command': node,
        'args': [
            cli,
            '--cdp-endpoint', 'http://127.0.0.1:%d' % port,
            '--cdp-timeout', '15000',
            '--caps', 'vision',
        ],
    }


def write_cfg(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')


# ---------- 三个动作 ----------

def do_check(args):
    node = node_bin(args.get('node'))
    npm = npm_bin(args.get('node_npm'))
    path, scored = pick_config(args.get('config'))
    cli = find_mcp_cli(npm) if npm else None

    payload = {
        'config_path': path,
        'config_candidates': [{'path': p, 'score': s} for p, s in scored],
        'node': node,
        'npm': npm,
        'mcp_cli': cli,
    }

    if not node:
        payload.update(status='no_node', ready=False,
                       ask='这台机器没找到 Node.js，配不了 Playwright MCP。到 nodejs.org 下 LTS 版，一路下一步，装完回来说一声。',
                       actions=[{'id': 'install_node', 'label': '去 nodejs.org 下 LTS'}])
        return out(payload)

    cfg = {}
    if path and os.path.isfile(path):
        try:
            cfg = read_cfg(path)
        except Exception as e:
            payload.update(status='bad_config', ready=False,
                           ask='%s 这个配置文件读不了（%s），不自动改它，你手动看一眼。' % (path, e),
                           actions=[])
            return out(payload)

    has_entry = ENTRY in (cfg.get('mcpServers') or {})
    installed = bool(cli)

    if has_entry and installed:
        payload.update(status='configured', ready=True,
                       ask='Playwright MCP 已经配好了。改配置后要重启一次 Claude 桌面版才生效。',
                       actions=[])
    elif not installed:
        payload.update(status='not_installed', ready=False,
                       ask='没装 @playwright/mcp。要我装上、顺手把配置也写好吗？改完你重启一次桌面版就生效。',
                       actions=[{'id': 'apply_mcp', 'label': '装上并配置'}])
    elif not path or not os.path.isfile(path):
        payload.update(status='no_config', ready=False,
                       ask='没找到 Claude 桌面版的配置文件（%s）。' % (path or '未知'),
                       actions=[{'id': 'apply_mcp', 'label': '帮我建一个'}])
    else:
        payload.update(status='installed_not_configured', ready=False,
                       ask='@playwright/mcp 装好了，但还没接进 Claude 桌面版。要我写进配置吗？只加一条，其他条目不动。',
                       actions=[{'id': 'apply_mcp', 'label': '写进配置'}])
    return out(payload)


def do_apply(args):
    node = node_bin(args.get('node'))
    npm = npm_bin(args.get('node_npm'))
    if not node:
        return out({'ok': False, 'error': 'no_node',
                    'ask': '没找到 Node.js，先装 nodejs.org 的 LTS 版再来。'})
    if not npm:
        return out({'ok': False, 'error': 'no_npm',
                    'ask': 'Node.js 装了但找不到 npm，可能安装不完整，重装一次 Node.js。'})

    port = int(args.get('port') or DEFAULT_PORT)
    path, _ = pick_config(args.get('config'))
    if not path:
        return out({'ok': False, 'error': 'no_config_path',
                    'ask': '这台机器没见过 Claude 桌面版。先装并登录一次 Claude 桌面版，再回来配。'})

    steps = []

    # 1) 装 npm 包
    cli = find_mcp_cli(npm)
    if not cli:
        try:
            r = subprocess.run([npm, 'install', '-g', '@playwright/mcp',
                                '--no-fund', '--no-audit'],
                               capture_output=True, text=True, timeout=600,
                               encoding='utf-8', errors='replace')
            steps.append({'step': 'npm_install', 'returncode': r.returncode,
                          'tail': (r.stdout or '')[-400:] + (r.stderr or '')[-300:]})
        except Exception as e:
            steps.append({'step': 'npm_install', 'error': str(e)})
        cli = find_mcp_cli(npm)
    if not cli:
        return out({'ok': False, 'error': 'mcp_not_found_after_install',
                    'ask': 'npm 装了但没找到 @playwright/mcp 的 cli.js，可能是全局目录权限问题。手动跑一次 npm install -g @playwright/mcp 看报什么错。',
                    'steps': steps})
    steps.append({'step': 'mcp_cli', 'path': cli})

    # 2) 读 + 备份
    cfg = {}
    bak = None
    if os.path.isfile(path):
        cfg = read_cfg(path)
        bak = backup(path)
        steps.append({'step': 'backup', 'path': bak})
    else:
        steps.append({'step': 'backup', 'path': None, 'note': '原文件不存在，将新建'})

    before_servers = sorted((cfg.get('mcpServers') or {}).keys())
    before_top = sorted(cfg.keys())

    # 3) 合并，只动 playwright 这一条
    cfg.setdefault('mcpServers', {})
    old = cfg['mcpServers'].get(ENTRY)
    cfg['mcpServers'][ENTRY] = build_entry(node, cli, port)
    write_cfg(path, cfg)

    # 4) 回读校验
    after = read_cfg(path)
    after_servers = sorted((after.get('mcpServers') or {}).keys())
    after_top = sorted(after.keys())
    lost = [k for k in before_servers if k not in after_servers]
    lost_top = [k for k in before_top if k not in after_top]
    entry_ok = ENTRY in after_servers and \
        (after['mcpServers'][ENTRY].get('args') or [])[:1] == [cli]

    ok = entry_ok and not lost and not lost_top
    payload = {
        'ok': ok,
        'config_path': path,
        'backup': bak,
        'entry': after['mcpServers'].get(ENTRY),
        'replaced_existing': bool(old),
        'servers_before': before_servers,
        'servers_after': after_servers,
        'lost': lost + lost_top,
        'steps': steps,
        'ask': ('配好了。**完全退出 Claude 桌面版再打开**（托盘图标右键退出，不是只关窗口），'
                '新会话里才有 browser_* 工具。' if ok else
                '写入后回读对不上，已保留备份：%s' % bak),
    }
    return out(payload)


def do_undo(args):
    path, _ = pick_config(args.get('config'))
    if not path or not os.path.isfile(path):
        return out({'ok': False, 'error': 'no_config', 'config_path': path})
    cfg = read_cfg(path)
    servers = cfg.get('mcpServers') or {}
    if ENTRY not in servers:
        return out({'ok': True, 'note': '本来就没有这一条，不用动', 'config_path': path})
    bak = backup(path)
    removed = servers.pop(ENTRY)
    write_cfg(path, cfg)
    after = read_cfg(path)
    return out({
        'ok': ENTRY not in (after.get('mcpServers') or {}),
        'config_path': path,
        'backup': bak,
        'removed': removed,
        'servers_left': sorted((after.get('mcpServers') or {}).keys()),
        'ask': '已经从配置里移除，重启桌面版生效。备份在 %s' % bak,
    })


# ---------- 入口 ----------

def parse(argv):
    args = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith('--'):
            key = a[2:].replace('-', '_')
            if '=' in key:
                key, val = key.split('=', 1)
                args[key] = val
            elif i + 1 < len(argv) and not argv[i + 1].startswith('--'):
                args[key] = argv[i + 1]
                i += 1
            else:
                args[key] = True
        i += 1
    return args


def main():
    argv = sys.argv[1:]
    args = parse(argv)
    if not argv or args.get('help'):
        print(__doc__)
        return 0
    if args.get('check'):
        return do_check(args) or 0
    if args.get('apply'):
        return do_apply(args) or 0
    if args.get('undo'):
        return do_undo(args) or 0
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
