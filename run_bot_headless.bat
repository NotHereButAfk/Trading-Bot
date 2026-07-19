@echo off
REM Run the bot 24/7 WITHOUT the GUI (for a machine you leave running
REM unattended, or to start automatically on boot via Task Scheduler).
REM Auto-restarts on crash. For unattended use, set confirm_signals: false
REM in config.yaml so signals execute without needing a button press.
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

set BOT_SUPERVISED=1

:restart
echo [%date% %time%] Starting HTX Futures Bot (headless)...
".venv\Scripts\python.exe" run.py --no-gui %*
set CODE=!errorlevel!

if "!CODE!"=="0" goto :end
if "!CODE!"=="3" (
    echo [%date% %time%] Applying new settings - restarting now...
    goto :restart
)
if "!CODE!"=="2" (
    echo [%date% %time%] Configuration error - NOT restarting. Fix config.yaml.
    pause
    goto :end
)

echo [%date% %time%] Exited unexpectedly (code !CODE!). Restarting in 10 seconds...
timeout /t 10 /nobreak >nul
goto :restart

:end
endlocal
