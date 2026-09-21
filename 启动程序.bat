@echo off
rem ============================================================
rem  Launcher - Laundry Photo Upload Assistant
rem  This file is ASCII-only on purpose: it avoids the classic
rem  cmd codepage parsing problems that break UTF-8 .bat files.
rem ============================================================
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
set "PYW=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"

if not exist "%PY%" goto fallback

rem -- quick dependency self-check (a window may flash briefly)
"%PY%" -c "import tkinter, pyautogui, pyperclip, pygetwindow" 2>nul
if errorlevel 1 goto fail

if exist "%PYW%" goto startw
start "" "%PY%" main.py
exit /b

:startw
start "" "%PYW%" main.py
exit /b

:fallback
rem -- no known local Python: try the py launcher, keep console visible
py -3 main.py
if errorlevel 1 goto fail
exit /b

:fail
echo.
echo  [!] Failed to start. Common fixes:
echo      1. Missing packages:  py -3 -m pip install -r requirements.txt
echo      2. Python not found:  install from https://www.python.org/downloads/
echo         keep the "tcl/tk" option during installation.
echo.
echo  A startup log (txt file) is written to this folder on errors.
echo.
pause
exit /b
