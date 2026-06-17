@echo off
:: AlgoTrade Watchdog — checks if backend is alive, restarts if not
:: Runs every 5 minutes via Task Scheduler (installed by install_autostart.bat)

:: Check if port 8000 is listening
netstat -ano | findstr ":8000 " | findstr "LISTENING" >nul 2>&1
if %errorlevel% equ 0 (
    :: Backend is running — nothing to do
    exit /b 0
)

:: Backend is down — restart it
echo [%date% %time%] AlgoTrade backend not responding — restarting... >> "%~dp0data\watchdog.log"
call "%~dp0restart_backend.bat"
echo [%date% %time%] Backend restarted. >> "%~dp0data\watchdog.log"
