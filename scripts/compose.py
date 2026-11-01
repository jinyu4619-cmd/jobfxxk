# -*- coding: utf-8 -*-
"""把用户经历灌进内置模板，产出一份「等着排版」的 docx

和 fill_engine 的分工：
  fill_engine —— 已有简历上做等长替换，导出 PDF，复验版式
  compose      —— 把经历填进模板，**不导出 PDF**，排版交给用户自己做

用法:
  python scripts/compose.py --draft 填充稿.json [--out 名字.docx]

填充稿格式:
  {
    "replace": [
      {"old": "模板里的原文片段", "new": "改写成相近长度的新文本", "why": "第 1 段实习"}
    ],
    "extra": [
      {"text": "装不下的经历，会放成一个文本框", "why": "第 4 段实习"}
    ]
  }

规则:
  - replace 的每条都要落在锚点窗口里，超窗的直接拒绝，不写
  - extra 不参与窗口校验，但会在文档末尾生成一个文本框，由用户自己拖到位
  - 产出落在 <resume_dir>/待排版/
"""
import json, os, shutil, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fill_engine as fe


def build_ps(docx, pairs, extras):
    ps = ['''
$ErrorActionPreference = "Stop"
$w = $null; $d = $null
try {
  $w = New-Object -ComObject Word.Application
  $w.Visible = $false
  $w.DisplayAlerts = 0
  $d = $w.Documents.Open("%s")
''' % fe.ps_escape(docx)]

    for old, new in pairs:
        ps.append('''
  foreach ($story in $d.StoryRanges) {
    $r = $story
    do {
      $f = $r.Find
      $f.ClearFormatting(); $f.Replacement.ClearFormatting()
      $f.Forward = $true; $f.Wrap = 0; $f.Format = $false
      $null = $f.Execute("%s", $false, $false, $false, $false, $false, $true, 0, $false, "%s", 2)
      $r = $r.NextStoryRange
    } while ($r -ne $null)
  }
''' % (fe.ps_escape(old), fe.ps_escape(new)))

    # 装不下的内容：每个一块文本框，浮在页面左下，等用户自己拖
    if extras:
        ps.append('''
  $top = 60
  foreach ($text in @(%s)) {
    $shp = $d.Shapes.AddTextbox(1, 30, $top, 500, 90)
    $shp.WrapFormat.Type = 3
    $tf = $shp.TextFrame
    $tf.WordWrap = $true
    $tf.MarginLeft = 0; $tf.MarginRight = 0; $tf.MarginTop = 0; $tf.MarginBottom = 0
    $tr = $tf.TextRange
    $tr.Text = $text
    $tr.Font.Size = 9
    $tr.Font.Name = "微软雅黑"
    $top = $top + 100
  }
''' % ', '.join('"%s"' % fe.ps_escape(e['text']) for e in extras))

    ps.append('''
  $d.Save()
  Write-Output ("SHAPES=" + $d.Shapes.Count)
}
catch {
  Write-Output ("ERROR=" + $_.Exception.Message)
}
finally {
  if ($d -ne $null) { try { $d.Close(0) } catch {} }
  if ($w -ne $null) { try { $w.Quit() } catch {} }
}
''')
    return '\n'.join(ps)


def main():
    argv = sys.argv[1:]
    if '--draft' not in argv:
        sys.exit(__doc__)
    draft_path = argv[argv.index('--draft') + 1]
    if not os.path.exists(draft_path):
        sys.exit(f'找不到填充稿：{draft_path}')
    draft = json.load(open(draft_path, encoding='utf-8-sig'))

    master = fe.need('master_docx', '母版 Word')
    anchors = fe.load_anchors()

    print(f'=== 窗口校验（每行 {anchors.get("capacity", "?")} 字）===\n')
    ok_list, bad_list = [], []
    for e in draft.get('replace', []):
        ok, n, lo, hi, s = fe.check(anchors, e['old'], e['new'])
        tag = '✓' if ok else ('✗' if ok is False else '?')
        rng = f'{lo}–{hi}' if lo is not None else '未匹配'
        print(f'  {tag} {n:>3} 字  窗口 {rng:<8}  {e["old"][:20]}…')
        (ok_list if ok else bad_list).append(e)

    print(f'\n可写入 {len(ok_list)} 条，超窗或未匹配 {len(bad_list)} 条')
    for e in bad_list:
        ok, n, lo, hi, s = fe.check(anchors, e['old'], e['new'])
        print(f'  · {e["old"][:24]}… → {n} 字' +
              (f'，窗口 {lo}–{hi}' if lo is not None else '，模板里没有这段'))

    extras = draft.get('extra', [])
    if not ok_list and not extras:
        print('\n没有可写入的内容，退出。')
        return

    out_dir = os.path.join(fe.RESUME_DIR or fe.DATA_DIR, '待排版')
    os.makedirs(out_dir, exist_ok=True)
    name = fe.CFG.get('name') or '简历'
    out_name = argv[argv.index('--out') + 1] if '--out' in argv else f'{name} - 待排版.docx'
    out = os.path.join(out_dir, out_name)
    shutil.copy2(master, out)

    raws = fe.docx_raw_texts(out)
    pairs, unresolved = [], []
    for e in ok_list:
        real = fe.resolve_old(raws, e['old'])
        if real is None:
            unresolved.append(e)
            real = e['old']
        pairs.append((real, e['new']))
    if unresolved:
        print('\n以下片段在模板里找不到对应文字：')
        for e in unresolved:
            print('  · ' + e['old'][:30])

    print('\n=== 写入 Word ===')
    os.makedirs(fe.TMP, exist_ok=True)
    ps_file = os.path.join(fe.TMP, '_compose.ps1')
    open(ps_file, 'w', encoding='utf-8-sig').write(build_ps(out, pairs, extras))
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ps_file],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        sys.exit('Word 超过 180 秒没响应。可能有对话框挡着。')
    log = ((r.stdout or '') + (r.stderr or '')).strip()
    for line in log.splitlines():
        if line.strip():
            print('  ' + line.strip())
    if 'ERROR=' in log:
        sys.exit('写入失败：' + log[log.index('ERROR='):][:200])

    print(f'\n产出：{out}')
    print(f'其中 {len(extras)} 段装不下，放成了文本框，需要你自己拖到位。')
    print('\n接下来是**用户自己排版**：')
    print('  1. 用 Word 打开上面这个文件，把内容调成你想要的样子')
    print('  2. 排完告诉我，我先检查硬伤（残留示例文字、超页、空段落）')
    print('  3. 检查通过我再导出 PDF，然后按新版面重新标定')


if __name__ == '__main__':
    main()
