@echo off
REM One-time setup for the HTX Futures Bot on Windows.
REM Double-click this file once after downloading the project.
setlocal
cd /d "%~dp0"

echo ==================================================
echo    HTX Futures Bot - Windows setup
echo ==================================================
echo.

REM --- check Python is installed and on PATH ---
where python >nul 2>nul
if errorlevel 1 (
    echo [X] Python was not found.
    echo     Install Python 3.10 or newer from:
    echo         https://www.python.org/downloads/
    echo     During install, TICK "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo [X] Could not create the virtual environment.
    pause
    exit /b 1
)

echo [2/4] Upgrading pip...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul

echo [3/4] Installing dependencies (this can take a minute)...
pip install -r requirements.txt
if errorlevel 1 (
    echo [X] Failed to install dependencies.
    pause
    exit /b 1
)

echo [4/4] Preparing your config file...
if not exist config.yaml (
    copy config.example.yaml config.yaml >nul
    echo     Created config.yaml from the template.
) else (
    echo     config.yaml already exists - left unchanged.
)

echo.
echo ==================================================
echo    Setup complete.
echo.
echo    NEXT STEPS:
echo      1. Open config.yaml in Notepad and set it up.
echo         (Keep paper_trading: true until you trust it.)
echo      2. Double-click run_bot.bat to start the bot.
echo ==================================================
echo.
pause
