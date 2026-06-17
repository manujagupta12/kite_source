@echo off
:: ══════════════════════════════════════════════════════════════════════════
:: AlgoTrade — Install Auto-Start (run ONCE as Administrator)
::
:: What this does:
::   • Registers AlgoTrade backend to start automatically when you log in
::   • You never need to manually start the server again
::   • Also installs a crash-watchdog that restarts it if it dies
::
:: How to run:
::   Right-click this file → Run as Administrator
:: ══════════════════════════════════════════════════════════════════════════

cd /d "%~dp0"
set TASK_NAME=AlgoTrade_Backend
set SCRIPT_DIR=%~dp0
set RESTART_BAT=%SCRIPT_DIR%restart_backend.bat

echo.
echo ╔══════════════════════════════════════════════╗
echo ║   AlgoTrade Auto-Start Installer            ║
echo ╚══════════════════════════════════════════════╝
echo.

:: Remove existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Register: trigger on user logon, run restart_backend.bat
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "cmd /c \"%RESTART_BAT%\"" ^
  /sc onlogon ^
  /delay 0000:30 ^
  /rl highest ^
  /f

if %errorlevel% equ 0 (
    echo ✓ Task registered: AlgoTrade backend will now start automatically at login.
) else (
    echo ✗ Failed to register task. Make sure you are running as Administrator.
    pause
    exit /b 1
)

:: Also register a watchdog that checks every 5 minutes
set WATCHDOG_TASK=AlgoTrade_Watchdog
schtasks /delete /tn "%WATCHDOG_TASK%" /f >nul 2>&1
schtasks /create ^
  /tn "%WATCHDOG_TASK%" ^
  /tr "cmd /c \"%SCRIPT_DIR%watchdog.bat\"" ^
  /sc minute ^
  /mo 5 ^
  /rl highest ^
  /f

if %errorlevel% equ 0 (
    echo ✓ Watchdog registered: backend restarts automatically if it crashes.
) else (
    echo ⚠ Watchdog not registered (non-critical).
)

echo.
echo ══════════════════════════════════════════════════
echo   Done. The backend will now:
echo   • Start automatically when you log in
echo   • Restart itself if it crashes (every 5 min check)
echo   • You only need to open the browser: http://localhost:5173
echo ══════════════════════════════════════════════════
echo.
pause
