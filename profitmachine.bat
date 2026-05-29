@echo off
setlocal enabledelayedexpansion
title AlgoTrade NSE Platform + Live Algo
color 0A

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   ALGOTRADE — FULL PLATFORM + ALGO ENGINE            ║
echo  ║   Data: NSE Direct API  +  Dhan WebSocket (optional) ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

REM ── Check Python ─────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    where python3 >nul 2>&1
    if errorlevel 1 (
        echo  [ERROR] Python not found. Install from https://python.org
        pause & exit /b 1
    )
    set PYTHON=python3
) else (
    set PYTHON=python
)

REM ── Check Node ───────────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)

REM ── Load .env ────────────────────────────────────────────────
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        set STR=%%A
        if not "!STR:~0,1!"=="#" if not "%%A"=="" set "%%A=%%B"
    )
    echo  [OK] .env loaded
)

REM ════════════════════════════════════════════════════════════
REM  STEP 1 — DHAN TOKEN
REM ════════════════════════════════════════════════════════════
echo.
echo  ─────────────────────────────────────────────────────────
echo   STEP 1 of 3 : Dhan API Token (WebSocket live ticks)
echo  ─────────────────────────────────────────────────────────
echo.

if not "%DHAN_CLIENT_ID%"=="" (
    echo  [DHAN] Token loaded from .env
    echo  [DHAN] Validating...
    %PYTHON% -c "import os,requests; r=requests.get('https://api.dhan.co/v2/funds',headers={'access-token':os.environ['DHAN_ACCESS_TOKEN'],'client-id':os.environ['DHAN_CLIENT_ID']},timeout=5); print('  [DHAN] Token OK' if r.status_code==200 else '  [DHAN] WARNING: expired — refresh at web.dhan.co')" 2>nul || echo  [DHAN] Validation skipped (offline^)
    set DHAN_MODE=DHAN_WS
    goto STEP2
)

echo  No Dhan token in .env
echo.
echo  Options:
echo  [A] Enter token now  (sub-second WebSocket ticks^)
echo  [B] Skip             (NSE Direct API, 2s refresh, free^)
echo.
set /p DHAN_CHOICE=Enter A or B: 

if /i "!DHAN_CHOICE!"=="A" (
    echo.
    set /p DHAN_CLIENT_ID=  Client ID     : 
    set /p DHAN_ACCESS_TOKEN=  Access Token  : 
    echo.
    echo  [TIP] Add these to .env to skip this step next time:
    echo        DHAN_CLIENT_ID=!DHAN_CLIENT_ID!
    echo        DHAN_ACCESS_TOKEN=^<your token^>
    set DHAN_MODE=DHAN_WS
) else (
    echo  [DHAN] Skipped — NSE Direct API only
    set DHAN_MODE=NSE_ONLY
)

:STEP2
REM ════════════════════════════════════════════════════════════
REM  STEP 2 — ALGO SELECTION
REM ════════════════════════════════════════════════════════════
echo.
echo  ─────────────────────────────────────────────────────────
echo   STEP 2 of 3 : Choose Algo
echo  ─────────────────────────────────────────────────────────
echo.
echo  [1] Calendar Spread  (Calendaralgofinal.py^)
echo  [2] All 7 Strategies (multistrategy.py^)
echo  [3] Dashboard only   (no algo^)
echo.
set /p CHOICE=Enter 1, 2 or 3: 

REM ════════════════════════════════════════════════════════════
REM  STEP 3 — INSTALL + LAUNCH
REM ════════════════════════════════════════════════════════════
echo.
echo  ─────────────────────────────────────────────────────────
echo   STEP 3 of 3 : Installing deps and launching
echo  ─────────────────────────────────────────────────────────
echo.

REM ── Python deps ──────────────────────────────────────────────
echo  Installing Python dependencies...
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
if "!DHAN_MODE!"=="DHAN_WS" (
    %PYTHON% -m pip install -q dhanhq 2>nul
)
echo  Python deps done

REM ── Node deps ────────────────────────────────────────────────
cd /d "%~dp0app\frontend"
if not exist "node_modules" (
    echo  Installing frontend packages...
    npm install
    if errorlevel 1 (
        echo  [ERROR] npm install failed. Need Node.js 18+
        pause & exit /b 1
    )
) else (
    npm install --prefer-offline --silent 2>nul
)
cd /d "%~dp0"

REM ── Runtime dirs ─────────────────────────────────────────────
if not exist "%~dp0data"                mkdir "%~dp0data"
if not exist "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
if not exist "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

REM ── Backend ──────────────────────────────────────────────────
start "AlgoTrade API" cmd /k "title AlgoTrade API && cd /d "%~dp0app\backend" && %PYTHON% main.py"
timeout /t 4 /nobreak >nul

REM ── Frontend ─────────────────────────────────────────────────
start "AlgoTrade Dashboard" cmd /k "title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && npm run dev"
timeout /t 4 /nobreak >nul

REM ── Dhan Ticker (if token present) ───────────────────────────
if "!DHAN_MODE!"=="DHAN_WS" (
    start "Dhan Ticker" cmd /k "title Dhan WebSocket Ticker && set DHAN_CLIENT_ID=!DHAN_CLIENT_ID! && set DHAN_ACCESS_TOKEN=!DHAN_ACCESS_TOKEN! && cd /d "%~dp0" && %PYTHON% -c "from algo.dhan_ticker import start_dhan_ticker; import time; start_dhan_ticker([260105,256265]); print('[Dhan] Running. Ctrl+C to stop.'); [time.sleep(60) for _ in iter(int,1)]""
    timeout /t 2 /nobreak >nul
)

REM ── Algo engine ──────────────────────────────────────────────
if "%CHOICE%"=="1" (
    start "Calendar Algo" cmd /k "title Calendar Spread Algo && cd /d "%~dp0" && %PYTHON% algo\Calendaralgofinal.py"
)
if "%CHOICE%"=="2" (
    start "Multi-Strategy Algo" cmd /k "title Multi-Strategy Algo && cd /d "%~dp0" && %PYTHON% algo\multistrategy.py"
)

REM ── Open browser ─────────────────────────────────────────────
timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║  ✅  All components launched                         ║
echo  ║                                                      ║
echo  ║  Dashboard : http://localhost:5173                   ║
echo  ║  API docs  : http://localhost:8000/docs              ║
echo  ║  Login     : demo@algotrade.in / demo123             ║
echo  ║                                                      ║
if "!DHAN_MODE!"=="DHAN_WS" (
echo  ║  Dhan ticks: ENABLED (sub-second^)                   ║
) else (
echo  ║  Dhan ticks: OFF  (add token to .env^)               ║
)
echo  ║  Logs      : data\trade_logs\                        ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
pause
