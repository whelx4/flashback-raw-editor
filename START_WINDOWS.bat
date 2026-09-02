@echo off
setlocal
cd /d "%~dp0"

title LoFi Logic

py -3.11 -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo.
    echo LoFi Logic needs Python 3.11 in addition to your existing Python.
    echo.
    choice /C YN /N /M "Install Python 3.11 now? [Y/N]: "
    if errorlevel 2 exit /b 1
    echo.
    where winget >nul 2>&1
    if errorlevel 1 (
        echo Windows Package Manager ^(winget^) is unavailable.
        echo Install Python 3.11 from https://www.python.org/downloads/
        echo and run this launcher again.
        echo.
        pause
        exit /b 1
    )
    echo Installing Python 3.11...
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :python_failed
    echo.
    py -3.11 -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo Python was installed, but Windows has not refreshed the launcher yet.
        echo Close this window and double-click START_WINDOWS.bat once more.
        echo.
        pause
        exit /b 0
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating the LoFi Logic environment...
    py -3.11 -m venv .venv
    if errorlevel 1 goto :setup_failed
)

if not exist ".venv\.lofilogic-ready" (
    echo Installing dependencies. This only happens on the first run...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :setup_failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :setup_failed
    type nul > ".venv\.lofilogic-ready"
)

echo Starting LoFi Logic...
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :app_failed
exit /b 0

:setup_failed
echo.
echo Setup failed. Check the messages above for the cause.
echo.
pause
exit /b 1

:python_failed
echo.
echo Python 3.11 could not be installed automatically.
echo Run this command in PowerShell and accept any Windows prompt:
echo   winget install -e --id Python.Python.3.11
echo.
pause
exit /b 1

:app_failed
echo.
echo LoFi Logic closed because of an error. Check the messages above.
echo.
pause
exit /b 1
