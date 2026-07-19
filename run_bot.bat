@echo off
REM Launch the HTX Futures Bot with the GUI control panel, and keep it running
REM 24/7: if it ever crashes it is automatically restarted after a short pause.
REM Any arguments you pass are forwarded to run.py (e.g. run_bot.bat --no-gui).
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Please run setup.bat first.
    pause
    exit /b 1
)

set BOT_SUPERVISED=1

:restart
echo.
echo [%date% %time%] Starting HTX Futures Bot...
".venv\Scripts\python.exe" run.py %*
set CODE=!errorlevel!

if "!CODE!"=="0" (
    echo [%date% %time%] Bot stopped cleanly.
    goto :end
)
if "!CODE!"=="3" (
    echo [%date% %time%] Applying new settings - restarting now...
    goto :restart
)
if "!CODE!"=="2" (
    echo [%date% %time%] Configuration error - NOT restarting.
    echo Fix config.yaml, then run this again.
    pause
    goto :end
)

echo [%date% %time%] Bot exited unexpectedly (code !CODE!). Restarting in 10 seconds...
echo Press Ctrl+C now to stop for good.
timeout /t 10 /nobreak >nul
goto :restart

:end
endlocal
