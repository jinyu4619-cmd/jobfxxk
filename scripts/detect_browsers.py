# -*- coding: utf-8 -*-
"""扫描本机已安装的浏览器，供用户选择

用法: python scripts/detect_browsers.py
输出: JSON，每项含 name / path / engine / can_automate

只认真的 .exe。桌面快捷方式（.lnk）不能直接启动，以前会被当成浏览器列出来，
用户选了之后 Playwright 起来就失败。
"""
import json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

LOCAL = os.environ.get('LOCALAPPDATA', '')
PF = os.environ.get('PROGRAMFILES', r'C:\Program Files')
PF86 = os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')

CANDIDATES = [
    ('Microsoft Edge', [
        os.path.join(PF86, r'Microsoft\Edge\Application\msedge.exe'),
        os.path.join(PF, r'Microsoft\Edge\Application\msedge.exe'),
    ], 'chromium'),
    ('Google Chrome', [
        os.path.join(PF, r'Google\Chrome\Application\chrome.exe'),
        os.path.join(PF86, r'Google\Chrome\Application\chrome.exe'),
        os.path.join(LOCAL, r'Google\Chrome\Application\chrome.exe'),
    ], 'chromium'),
    ('Brave', [
        os.path.join(PF, r'BraveSoftware\Brave-Browser\Application\brave.exe'),
        os.path.join(LOCAL, r'BraveSoftware\Brave-Browser\Application\brave.exe'),
    ], 'chromium'),
    ('Firefox', [
        os.path.join(PF, r'Mozilla Firefox\firefox.exe'),
        os.path.join(PF86, r'Mozilla Firefox\firefox.exe'),
    ], 'gecko'),
    ('360 安全浏览器', [
        os.path.join(LOCAL, r'360Chrome\Chrome\Application\360chrome.exe'),
        os.path.join(os.environ.get('APPDATA', ''), r'360se6\Application\360se.exe'),
    ], 'chromium'),
    ('QQ 浏览器', [
        os.path.join(PF86, r'Tencent\QQBrowser\QQBrowser.exe'),
        os.path.join(PF, r'Tencent\QQBrowser\QQBrowser.exe'),
    ], 'chromium'),
]


def find():
    found = []
    for name, paths, engine in CANDIDATES:
        for p in paths:
            if not p or not p.lower().endswith('.exe'):
                continue
            if os.path.exists(p):
                found.append({
                    'name': name,
                    'path': p,
                    'engine': engine,
                    # 只有 Chromium 系能被 Playwright 驱动，Firefox 不行
                    'can_automate': engine == 'chromium',
                    'recommended': False,
                })
                break
    # 推荐第一个能自动化的（Edge 通常排第一，Windows 自带）
    for b in found:
        if b['can_automate']:
            b['recommended'] = True
            break
    return found


def main():
    if os.name != 'nt':
        print(json.dumps({'found': False, 'browsers': [],
                          'note': '目前只支持 Windows'}, ensure_ascii=False, indent=1))
        return 0
    found = find()
    print(json.dumps({'found': bool(found), 'browsers': found}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
