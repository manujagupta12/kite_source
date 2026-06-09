@echo off
title Restart AlgoTrade Backend
echo Stopping any existing backend on port 8000...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo Killing PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo Installing/updating Python packages...
python -m pip install -q yfinance python-dotenv --break-system-packages 2>nul || python -m pip install -q yfinance python-dotenv

echo Starting AlgoTrade Backend...
cd /d "%~dp0app\backend"
start "AlgoTrade API" cmd /k "python main.py"
echo Backend started.
timeout /t 3 /nobreak >nul
