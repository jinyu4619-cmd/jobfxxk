# -*- coding: utf-8 -*-
"""把 docx 导成 pdf。Word 优先，WPS 兜底，就这一条通道。

设计只有四条：

1. **一条通道，不做后端选择。** 装了 Word 用 Word，只装了 WPS 就用 WPS；
   没有 ``--pdf-backend``，没有第二引擎。能导出就是能导出，不能就说清差什么。
2. **限时，且超时能说清卡在哪一步。** 状态写在临时文件里，主进程超时后读它，
   得到的是「卡在 exporting PDF with Word.Application」这种话，不是一句「失败」。
3. **原子替换。** 先导出到同目录的临时文件名，确认是有效 PDF 再 ``os.replace``
   覆盖目标；任何一步失败都不动已有文件，不会留下半截 PDF。
4. **导出后自检。** ``%PDF-`` 头、``%%EOF`` 尾、大小、页数，几项都在才算成功，
   不拿「进程返回 0」当成功。

**版式复验不在这里做**，归 ``verify.py``。它比的是「母版 PDF」和「产物 PDF」——
因为导出只有这一条通道，两份必然同源，几何坐标天然可比，不需要处理跨引擎偏移。
"""
import os
import shutil
import subprocess
import sys
import time
import uuid

from paths import DATA_DIR

POWERSHELL = shutil.which('powershell.exe') or shutil.which('powershell')
TIMEOUT_SECONDS = 45

# Word 和 WPS 都能导出 PDF，但 COM 的 ProgID 不同。装了 WPS 没装 Office 的机器
# 非常常见，只认 Word.Application 会让这些机器在导出这一步直接停住。
# 按 Word → WPS 顺序试，谁先起来用谁，并把用的是哪个记进状态文件。
_SCRIPT = r'''param([string]$Source, [string]$Candidate, [string]$StatusFile)
$ErrorActionPreference = 'Stop'
$w = $null
$d = $null
$stage = 'starting'
function Set-Stage([string]$Value) {
  $script:stage = $Value
  [System.IO.File]::WriteAllText($StatusFile, $Value, [System.Text.Encoding]::UTF8)
}
try {
  $progIds = @('Word.Application', 'KWPS.Application', 'Kwps.Application')
  $made = $false
  $progId = ''
  foreach ($p in $progIds) {
    Set-Stage ('creating ' + $p)
    try {
      $w = New-Object -ComObject $p
      $progId = $p
      $made = $true
      break
    } catch {
      $w = $null
    }
  }
  if (-not $made -or $w -eq $null) {
    throw '没找到 Word.Application 或 KWPS.Application，Word / WPS 都没装或 COM 未注册'
  }
  $w.Visible = $false
  $w.DisplayAlerts = 0
  Set-Stage ('opening document with ' + $progId)
  $d = $w.Documents.Open($Source, $false, $true, $false)
  Set-Stage ('exporting PDF with ' + $progId)
  $d.ExportAsFixedFormat($Candidate, 17)
  Set-Stage 'reading page count'
  $pages = $d.ComputeStatistics(2)
  Set-Stage ('done via ' + $progId + ' pages=' + $pages)
  exit 0
} catch {
  [System.IO.File]::WriteAllText($StatusFile, ('error at ' + $stage + ': ' + $_.Exception.Message), [System.Text.Encoding]::UTF8)
  exit 1
} finally {
  if ($d -ne $null) { try { $d.Close(0) } catch {} }
  if ($w -ne $null) { try { $w.Quit() } catch {} }
  if ($d -ne $null) { try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($d) } catch {} }
  if ($w -ne $null) { try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($w) } catch {} }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}'''


def valid_pdf(path):
    """是不是一份完整的 PDF：头、尾、大小都对。缺一样就当没生成。"""
    try:
        size = os.path.getsize(path)
        if size < 512:
            return False
        with open(path, 'rb') as f:
            if f.read(5) != b'%PDF-':
                return False
            f.seek(max(0, size - 4096))
            return b'%%EOF' in f.read()
    except OSError:
        return False


def _read_stage(path):
    try:
        with open(path, encoding='utf-8-sig') as f:
            return f.read().strip()
    except OSError:
        return ''


def export_pdf(source, destination, timeout=None):
    """把 source.docx 导成 destination.pdf。返回 (ok, 说明)。

    成功时说明里带 PAGES=n 和耗时；失败时说清卡在哪一步，并且不改动已有文件。
    """
    if os.name != 'nt':
        return False, '当前只支持 Windows：导出依赖 Word / WPS 的 COM 接口。'
    source = os.path.abspath(source)
    destination = os.path.abspath(destination)
    if not os.path.isfile(source):
        return False, '源 DOCX 不存在：' + source
    if not POWERSHELL:
        return False, '找不到 Windows PowerShell，无法调用 Word / WPS 的 COM 接口。'
    timeout = timeout or TIMEOUT_SECONDS

    out_dir = os.path.dirname(destination)
    os.makedirs(out_dir, exist_ok=True)
    tmp_dir = os.path.join(DATA_DIR, 'tmp')
    os.makedirs(tmp_dir, exist_ok=True)
    token = uuid.uuid4().hex
    ps_file = os.path.join(tmp_dir, f'_pdf_export_{token}.ps1')
    status_file = os.path.join(tmp_dir, f'_pdf_export_{token}.status')
    # 先导到同目录的隐藏临时名，验证通过才替换目标
    candidate = os.path.join(out_dir, f'.jobfxxk-{token}.pdf')

    with open(ps_file, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write(_SCRIPT)
    started = time.monotonic()
    proc = None
    try:
        proc = subprocess.Popen(
            [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-STA',
             '-ExecutionPolicy', 'Bypass', '-File', ps_file,
             source, candidate, status_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace')
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 只终止本次启动的 PowerShell 辅助进程，绝不按进程名批量结束 Word——
            # 用户可能开着没保存的文档。
            stage = _read_stage(status_file) or 'unknown'
            proc.kill()
            proc.communicate()
            if valid_pdf(candidate):
                os.replace(candidate, destination)
                return True, (f'PAGES=unknown\nPDF 已生成；Word/WPS 在清理阶段超时'
                              f'（{stage}）。可能仍有后台进程。')
            return False, f'超过 {timeout} 秒没导出来，卡在：{stage}。'

        stage = _read_stage(status_file)
        if proc.returncode != 0:
            return False, (stage or (output or '').strip() or 'Word / WPS 返回失败。')[:500]
        if not valid_pdf(candidate):
            return False, f'进程成功但 PDF 不完整（阶段：{stage or "未知"}）。'
        os.replace(candidate, destination)          # 只有走到这里才覆盖旧文件
        pages = stage.rsplit('pages=', 1)[-1] if 'pages=' in stage else 'unknown'
        return True, f'PAGES={pages}\n用时 {time.monotonic() - started:.1f} 秒。'
    except OSError as e:
        return False, f'启动导出进程失败：{e}'
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.communicate()
        for p in (ps_file, status_file, candidate):
            try:
                os.remove(p)
            except OSError:
                pass


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if len(args) != 2:
        sys.exit('用法：python scripts/pdf_export.py <源DOCX> <目标PDF>')
    ok, message = export_pdf(args[0], args[1])
    print(message)
    sys.exit(0 if ok else 1)
