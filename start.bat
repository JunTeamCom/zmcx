@echo off
rem ============================================================
rem  zmcx - Zhengma code lookup, Windows launcher
rem
rem  Usage:
rem    start.bat                     start the app
rem    start.bat --backend tk        force the Tkinter UI
rem    set ZM_DIR=D:\zmdata          data files live somewhere else
rem
rem  This file is deliberately pure ASCII: cmd.exe mis-parses
rem  UTF-8 batch files. All Chinese messages are printed by
rem  Python itself, which writes to the console as Unicode.
rem
rem  Exit codes: 0 ok, 1 environment not satisfied, 2 files missing
rem ============================================================
setlocal

set "SCRIPT_DIR=%~dp0"

rem 1. Find Python
set "PYTHON="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3"
if not defined PYTHON (
    python -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
    echo ERROR: Python was not found on this machine.
    echo.
    echo Please install Python 3.9 or newer from
    echo   https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

rem 2. Python version
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"
if errorlevel 1 (
    echo ERROR: Python 3.9 or newer is required.
    %PYTHON% -c "import sys; print('Found version: %d.%d' % sys.version_info[:2])"
    pause
    exit /b 1
)

rem 3. Launch (zm_gui.py does the remaining checks and prints Chinese messages)
%PYTHON% "%SCRIPT_DIR%zm_gui.py" %*
if errorlevel 1 (
    echo.
    pause
)
exit /b 0
