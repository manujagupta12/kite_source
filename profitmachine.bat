@echo off
setlocal enabledelayedexpansion
title AlgoTrade NSE Platform + Live Algo
color 0A

echo.
echo  ==========================================================
echo   ALGOTRADE -- FULL PLATFORM + ALGO ENGINE
echo   Data: NSE Direct API  +  Dhan WebSocket (optional)
echo  ==========================================================
echo.

REM ================================================================
REM  FIX WINDOWS PATH: add common Node.js + Python install locations
REM ================================================================
SET "PATH=%PATH%;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs"
SET "PATH=%PATH%;%APPDATA%\npm;%APPDATA%\nvm\current"
SET "PATH=%PATH%;C:\Program Files\Python313;C:\Program Files\Python312"
SET "PATH=%PATH%;C:\Program Files\Python311;C:\Program Files\Python310"
SET "PATH=%PATH%;C:\Program Files\Python39;C:\Program Files\Python38"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python310"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python310\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python313\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python312\Scripts"
SET "PATH=%PATH%;C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python313"
SET "PATH=%PATH%;C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312"
SET "PATH=%PATH%;C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311"

REM ── Auto-locate Python ───────────────────────────────────────
SET PYTHON=
FOR %%P IN (python python3 py) DO (
    IF "!PYTHON!"=="" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET PYTHON=%%P
    )
)
IF "!PYTHON!"=="" (
    FOR %%D IN (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) DO (
        IF "!PYTHON!"=="" IF EXIST %%D SET PYTHON=%%D
    )
)
IF "!PYTHON!"=="" (
    echo  [ERROR] Python not found. Install from https://python.org
    echo  Check "Add python.exe to PATH" during install
    pause & exit /b 1
)
FOR /F "tokens=2 delims= " %%V IN ('!PYTHON! --version 2^>^&1') DO SET PYVER=%%V
echo  [OK] Python !PYVER!

REM ── Auto-locate npm ──────────────────────────────────────────
SET NPM=
FOR %%P IN (npm.cmd npm) DO (
    IF "!NPM!"=="" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET NPM=%%P
    )
)
IF "!NPM!"=="" (
    FOR %%D IN (
        "C:\Program Files\nodejs\npm.cmd"
        "C:\Program Files (x86)\nodejs\npm.cmd"
        "%APPDATA%\nvm\current\npm.cmd"
    ) DO (
        IF "!NPM!"=="" IF EXIST %%D (
            SET NPM=%%D
            FOR %%X IN (%%D) DO SET "PATH=!PATH!;%%~dpX"
        )
    )
)
IF "!NPM!"=="" (
    echo  [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)
FOR /F %%V IN ('node --version 2^>^&1') DO SET NODEVER=%%V
echo  [OK] Node.js !NODEVER!

REM ── Load .env ────────────────────────────────────────────────
IF EXIST "%~dp0.env" (
    FOR /F "usebackq tokens=1,* delims==" %%A IN ("%~dp0.env") DO (
        SET "LINE=%%A"
        IF NOT "!LINE:~0,1!"=="#" IF NOT "%%A"=="" SET "%%A=%%B"
    )
    echo  [OK] .env loaded
)

REM ================================================================
REM  STEP 1 -- DHAN TOKEN
REM ================================================================
echo.
echo  ----------------------------------------------------------
echo   STEP 1 of 3 : Dhan API Token (WebSocket live ticks^)
echo  ----------------------------------------------------------
echo.

IF NOT "%DHAN_CLIENT_ID%"=="" (
    echo  [DHAN] Token loaded from .env
    !PYTHON! -c "import os,requests; r=requests.get('https://api.dhan.co/v2/funds',headers={'access-token':os.environ.get('DHAN_ACCESS_TOKEN',''),'client-id':os.environ.get('DHAN_CLIENT_ID','')},timeout=5); print('  [DHAN] Token OK') if r.status_code==200 else print('  [DHAN] WARNING: may be expired')" 2>nul || echo  [DHAN] Validation skipped (offline^)
    SET DHAN_MODE=DHAN_WS
    GOTO STEP2
)

echo  No Dhan token in .env
echo.
echo  [A] Enter token now  (sub-second WebSocket ticks^)
echo  [B] Skip             (NSE Direct API, 2s refresh, free^)
echo.
SET /P DHAN_CHOICE=Enter A or B: 

IF /I "!DHAN_CHOICE!"=="A" (
    echo.
    SET /P DHAN_CLIENT_ID=  Client ID     : 
    SET /P DHAN_ACCESS_TOKEN=  Access Token  : 
    echo.
    echo  [TIP] Save to .env to skip this step next time
    SET DHAN_MODE=DHAN_WS
) ELSE (
    echo  [DHAN] Skipped -- NSE Direct API only
    SET DHAN_MODE=NSE_ONLY
)

:STEP2
REM ================================================================
REM  STEP 2 -- ALGO SELECTION
REM ================================================================
echo.
echo  ----------------------------------------------------------
echo   STEP 2 of 3 : Choose Algo
echo  ----------------------------------------------------------
echo.
echo  [1] Calendar Spread  (Calendaralgofinal.py^)
echo  [2] All 7 Strategies (multistrategy.py^)
echo  [3] Dashboard only   (no algo^)
echo.
SET /P CHOICE=Enter 1, 2 or 3: 

REM ================================================================
REM  STEP 3 -- INSTALL + LAUNCH
REM ================================================================
echo.
echo  ----------------------------------------------------------
echo   STEP 3 of 3 : Installing deps and launching
echo  ----------------------------------------------------------
echo.

echo  Installing Python dependencies...
!PYTHON! -m pip install -q --upgrade pip 2>nul
!PYTHON! -m pip install -q ^
    "fastapi==0.115.5" ^
    "uvicorn[standard]==0.32.1" ^
    "pydantic==2.10.3" ^
    "requests==2.32.3" ^
    "python-multipart==0.0.20" ^
    "httpx==0.28.1" ^
    "python-dotenv==1.0.1" ^
    "python-jose[cryptography]==3.3.0" ^
    "bcrypt==4.2.1" ^
    "pandas==2.2.3" ^
    "numpy==1.26.4" ^
    "pyarrow==15.0.2" ^
    "nsepython"
IF "!DHAN_MODE!"=="DHAN_WS" (
    !PYTHON! -m pip install -q dhanhq 2>nul
)
echo  Python deps done

cd /d "%~dp0app\frontend"
IF NOT EXIST "node_modules" (
    echo  Installing frontend packages (first run^)...
    CALL !NPM! install
    IF ERRORLEVEL 1 (
        echo  [ERROR] npm install failed. Need Node.js 18+
        pause & exit /b 1
    )
) ELSE (
    CALL !NPM! install --prefer-offline --silent 2>nul
)
cd /d "%~dp0"

IF NOT EXIST "%~dp0data"                mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
IF NOT EXIST "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

REM ── Backend ──────────────────────────────────────────────────
start "AlgoTrade API" cmd /k "SET PATH=!PATH! && title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 4 /nobreak >nul

REM ── Frontend ─────────────────────────────────────────────────
start "AlgoTrade Dashboard" cmd /k "SET PATH=!PATH! && title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && !NPM! run dev"
timeout /t 4 /nobreak >nul

REM ── Dhan Ticker ──────────────────────────────────────────────
IF "!DHAN_MODE!"=="DHAN_WS" (
    start "Dhan Ticker" cmd /k "SET PATH=!PATH! && SET DHAN_CLIENT_ID=!DHAN_CLIENT_ID! && SET DHAN_ACCESS_TOKEN=!DHAN_ACCESS_TOKEN! && title Dhan WebSocket Ticker && cd /d "%~dp0" && !PYTHON! -c "from algo.dhan_ticker import start_dhan_ticker; import time; start_dhan_ticker([260105,256265]); print('[Dhan] Running'); [time.sleep(60) for _ in iter(int,1)]""
    timeout /t 2 /nobreak >nul
)

REM ── Algo engine ──────────────────────────────────────────────
IF "%CHOICE%"=="1" (
    start "Calendar Algo" cmd /k "SET PATH=!PATH! && title Calendar Spread Algo && cd /d "%~dp0" && !PYTHON! algo\Calendaralgofinal.py"
)
IF "%CHOICE%"=="2" (
    start "Multi-Strategy Algo" cmd /k "SET PATH=!PATH! && title Multi-Strategy Algo && cd /d "%~dp0" && !PYTHON! algo\multistrategy.py"
)

timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo  ==========================================================
echo   OK  All components launched
echo.
echo   Dashboard : http://localhost:5173
echo   API docs  : http://localhost:8000/docs
echo   Login     : demo@algotrade.in / demo123
IF "!DHAN_MODE!"=="DHAN_WS" (
echo   Dhan ticks: ENABLED
) ELSE (
echo   Dhan ticks: OFF  ^(add token to .env to enable^)
)
echo   Logs      : data\trade_logs\
echo  ==========================================================
echo.
pause
