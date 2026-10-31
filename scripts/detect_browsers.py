# -*- coding: utf-8 -*-
"""扫描本机已安装的浏览器，供用户选择
用法: python scripts/detect_browsers.py
输出: JSON 数组，每项含 name / path / engine / cdp
"""
import json, os, sys
sys.stdout.reconfigure(encoding='utf-8')

CANDIDATES = [
    ('Microsoft Edge', [
        r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
        r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
    ], 'chromium'),
    ('Google Chrome', [
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
    ], 'chromium'),
    ('Brave', [
        r'C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe',
        os.path.expandvars(r'%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe'),
    ], 'chromium'),
    ('Firefox', [
        r'C:\Program Files\Mozilla Firefox\firefox.exe',
        r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe',
    ], 'gecko'),
    ('360 安全浏览器', [
        r'C:\Users\Public\Desktop\360安全浏览器.lnk',
        os.path.expandvars(r'%APPDATA%\360se6\Application\360se.exe'),
    ], 'chromium'),
    ('QQ 浏览器', [
        r'C:\Program Files (x86)\Tencent\QQBrowser\QQBrowser.exe',
        r'C:\Program Files\Tencent\QQBrowser\QQBrowser.exe',
    ], 'chromium'),
]

found = []
for name, paths, engine in CANDIDATES:
    for p in paths:
        if os.path.exists(p):
            found.append({
                'name': name,
                'path': p,
                'engine': engine,
                # 只有 Chromium 系能开远程调试端口，Firefox 不行
                'can_automate': engine == 'chromium',
            })
            break

if not found:
    print(json.dumps({'found': False, 'browsers': []}, ensure_ascii=False, indent=1))
    sys.exit(0)

print(json.dumps({'found': True, 'browsers': found}, ensure_ascii=False, indent=1))
