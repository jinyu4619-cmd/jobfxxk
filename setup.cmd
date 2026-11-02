@echo off
setlocal enabledelayedexpansion
chcp 936 >nul 2>nul

rem jobfxxk 启动包装：先找到能用的 Python，再把参数原样转给 scripts\setup.py
rem
rem 为什么需要它：Windows 上「python」经常不是真的解释器——
rem 微软商店那个占位程序执行会返回 9009，py 启动器可能没装，
rem python3 也不一定存在。直接按文档敲 python，只会得到一个看不懂的错误。
rem
rem 用法：setup.cmd --check
rem       setup.cmd --apply use_template
rem       setup.cmd --print-python    只打印找到的解释器路径

set "HERE=%~dp0"
set "TARGET=%HERE%scripts\setup.py"
set "PY="
set "PYARG="
set "OUT="

if not exist "%TARGET%" (
  echo [jobfxxk] 找不到 %TARGET%
  echo 这个文件要在技能目录的根上运行。
  exit /b 1
)

rem ---- 1. 直接叫 python ----
for /f "delims=" %%v in ('python --version 2^>^&1') do if not defined OUT set "OUT=%%v"
if defined OUT (
  echo !OUT! | findstr /i "Python 3" >nul 2>nul
  if not errorlevel 1 set "PY=python"
)
set "OUT="

rem ---- 2. py 启动器 ----
if not defined PY (
  for /f "delims=" %%v in ('py -3 --version 2^>^&1') do if not defined OUT set "OUT=%%v"
  if defined OUT (
    echo !OUT! | findstr /i "Python 3" >nul 2>nul
    if not errorlevel 1 (
      set "PY=py"
      set "PYARG=-3"
    )
  )
  set "OUT="
)

rem ---- 3. python3 ----
if not defined PY (
  for /f "delims=" %%v in ('python3 --version 2^>^&1') do if not defined OUT set "OUT=%%v"
  if defined OUT (
    echo !OUT! | findstr /i "Python 3" >nul 2>nul
    if not errorlevel 1 set "PY=python3"
  )
  set "OUT="
)

rem ---- 4. 常见安装位置 ----
if not defined PY (
  for /f "delims=" %%d in ('dir /b /ad "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\%%d\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\%%d\python.exe"
  )
)
if not defined PY (
  for /f "delims=" %%d in ('dir /b /ad "C:\Python3*" 2^>nul') do (
    if not defined PY if exist "C:\%%d\python.exe" set "PY=C:\%%d\python.exe"
  )
)
if not defined PY (
  for /f "delims=" %%d in ('dir /b /ad "%ProgramFiles%\Python3*" 2^>nul') do (
    if not defined PY if exist "%ProgramFiles%\%%d\python.exe" set "PY=%ProgramFiles%\%%d\python.exe"
  )
)
if not defined PY if exist "D:\python.exe" set "PY=D:\python.exe"

rem ---- 5. 还是没找到 ----
if not defined PY (
  echo.
  echo [jobfxxk] 没找到能用的 Python。
  echo.
  echo 需要 Python 3.9 或更高版本。安装时务必勾选 "Add Python to PATH"。
  echo   下载地址： https://www.python.org/downloads/
  echo.
  echo 提醒：微软商店里那个 python 是空壳程序，装了也跑不了。
  echo       如果刚才装过 Python，关掉终端重开再试。
  exit /b 1
)

if /i "%~1"=="--print-python" (
  echo !PY! !PYARG!
  exit /b 0
)

echo [jobfxxk] python: !PY! !PYARG!
"!PY!" !PYARG! "%TARGET%" %*
exit /b %ERRORLEVEL%
