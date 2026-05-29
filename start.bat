@echo off
title AlgoTrade NSE Platform
color 0A

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   ALGOTRADE NSE F^&O PLATFORM  —  Single Click Start  ║
echo  ║   Data: NSE Direct API  +  Dhan WebSocket (optional) ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

REM ── Check Python 3.9+ ────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    where python3 >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found.
        echo  Install Python 3.10+ from https://python.org
        echo  Make sure to check "Add Python to PATH"
        pause & exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)
for /f "tokens=2 delims= " %%V in ('%PYTHON% --version 2^>^&1') do set PYVER=%%V
echo  [OK] Python %PYVER%

REM ── Check Node 18+ ───────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Node.js not found.
    echo  Install Node.js 18+ from https://nodejs.org
    pause & exit /b 1
)
for /f "tokens=1 delims=v" %%V in ('node --version') do set NODEVER=%%V
echo  [OK] Node %NODEVER%

REM ── Load .env ────────────────────────────────────────────────
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        set STR=%%A
        if not "!STR:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
    )
    echo  [OK] .env loaded
) else (
    echo  [TIP] Copy .env.example to .env to configure Dhan token
)

REM ── Dhan token status ────────────────────────────────────────
if "%DHAN_CLIENT_ID%"=="" (
    echo  [DHAN] No token — using NSE Direct API (free^)
) else (
    echo  [DHAN] Token found — Dhan WebSocket ticks enabled
)
echo.

REM ── Python dependencies ──────────────────────────────────────
echo  [1/3] Installing Python dependencies...
%PYTHON% -m pip install -q --upgrade pip 2>nul
%PYTHON% -m pip install -q ^
    fastapi==0.115.5 ^
    "uvicorn[standard]==0.32.1" ^
    pydantic==2.10.3 ^
    requests==2.32.3 ^
    python-multipart==0.0.20 ^
    httpx==0.28.1 ^
    python-dotenv==1.0.1 ^
    "python-jose[cryptography]==3.3.0" ^
    bcrypt==4.2.1 ^
    "pandas==2.2.3" ^
    "numpy==1.26.4" ^
    "pyarrow==15.0.2" ^
    nsepython 2>nul
if not "%DHAN_CLIENT_ID%"=="" (
    %PYTHON% -m pip install -q dhanhq 2>nul
    echo  [1/3] dhanhq installed
)
echo  [1/3] Python deps done

REM ── Node / Frontend dependencies ─────────────────────────────
echo  [2/3] Checking frontend dependencies...
cd /d "%~dp0app\frontend"
if not exist "node_modules" (
    echo  [2/3] First run — installing npm packages...
    npm install
    if errorlevel 1 (
        echo  [ERROR] npm install failed. Check Node.js version ^(need 18+^)
        pause & exit /b 1
    )
) else (
    REM Check if package.json changed since last install
    npm install --prefer-offline --silent 2>nul
)
echo  [2/3] Frontend deps done
cd /d "%~dp0"

REM ── Create runtime directories ───────────────────────────────
if not exist "%~dp0data"                mkdir "%~dp0data"
if not exist "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
if not exist "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

REM ── Start Backend ────────────────────────────────────────────
echo  [3/3] Starting API backend on :8000...
start "AlgoTrade API" cmd /k "title AlgoTrade API && cd /d "%~dp0app\backend" && %PYTHON% main.py"
timeout /t 4 /nobreak >nul

REM ── Start Frontend ───────────────────────────────────────────
echo  [3/3] Starting React dashboard on :5173...
start "AlgoTrade Dashboard" cmd /k "title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && npm run dev"
timeout /t 5 /nobreak >nul

REM ── Open browser ─────────────────────────────────────────────
start "" "http://localhost:5173"

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║  ✅  Platform running                                ║
echo  ║                                                      ║
echo  ║  Dashboard : http://localhost:5173                   ║
echo  ║  API docs  : http://localhost:8000/docs              ║
echo  ║  Login     : demo@algotrade.in / demo123             ║
echo  ║                                                      ║
echo  ║  For live algo: run profitmachine.bat                ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
pause
