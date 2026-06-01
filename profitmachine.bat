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

REM ── PATH setup ───────────────────────────────────────────────
SET "PATH=%PATH%;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs"
SET "PATH=%PATH%;%APPDATA%\npm;%APPDATA%\nvm\current"
SET "PATH=%PATH%;C:\Program Files\Python314;C:\Program Files\Python313"
SET "PATH=%PATH%;C:\Program Files\Python312;C:\Program Files\Python311;C:\Program Files\Python310"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python314"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python314\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python314\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python313\Scripts"

REM ── Find Python ───────────────────────────────────────────────
SET PYTHON=
FOR %%P IN (python python3 py) DO (
    IF "!PYTHON!"=="" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET PYTHON=%%P
    )
)
IF "!PYTHON!"=="" (
    FOR %%D IN (
        "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Program Files\Python314\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Python314\python.exe"
        "C:\Python313\python.exe"
    ) DO (
        IF "!PYTHON!"=="" IF EXIST %%D SET PYTHON=%%D
    )
)
IF "!PYTHON!"=="" (
    echo  [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)
FOR /F "tokens=2 delims= " %%V IN ('!PYTHON! --version 2^>^&1') DO SET PYVER=%%V
echo  [OK] Python !PYVER!

REM ── Find npm ──────────────────────────────────────────────────
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

REM ── Load .env ─────────────────────────────────────────────────
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
echo   STEP 1 of 3 : Dhan API Token
echo  ----------------------------------------------------------

IF NOT "%DHAN_CLIENT_ID%"=="" (
    echo  [DHAN] Token loaded from .env - WebSocket ticks enabled
    SET DHAN_MODE=DHAN_WS
    GOTO STEP2
)

echo.
echo  [A] Enter token now  (sub-second WebSocket ticks)
echo  [B] Skip             (NSE Direct API, free)
echo.
SET /P DHAN_CHOICE=Enter A or B: 

IF /I "!DHAN_CHOICE!"=="A" (
    echo.
    SET /P DHAN_CLIENT_ID=  Client ID     : 
    SET /P DHAN_ACCESS_TOKEN=  Access Token  : 
    echo.
    REM Save to .env for next time
    echo DHAN_CLIENT_ID=!DHAN_CLIENT_ID!>> "%~dp0.env"
    echo DHAN_ACCESS_TOKEN=!DHAN_ACCESS_TOKEN!>> "%~dp0.env"
    echo  [OK] Token saved to .env - won't ask again next time
    SET DHAN_MODE=DHAN_WS
) ELSE (
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
echo  [1] Calendar Spread  (Calendaralgofinal.py)
echo  [2] All 7 Strategies (multistrategy.py)
echo  [3] Dashboard only   (no algo)
echo.
SET /P CHOICE=Enter 1, 2 or 3: 

REM ================================================================
REM  STEP 3 -- DEPS CHECK (fast - skips if already installed)
REM ================================================================
echo.
echo  ----------------------------------------------------------
echo   STEP 3 of 3 : Checking dependencies
echo  ----------------------------------------------------------
echo.

REM ── Python deps: check fastapi + nsepython ──────────────────
!PYTHON! -c "import fastapi" >nul 2>&1
IF ERRORLEVEL 1 (
    echo  [pip] First run - installing Python packages ^(one time only^)...
    !PYTHON! -m pip install -q -r "%~dp0app\backend\requirements.txt"
    echo  [pip] Done
) ELSE (
    echo  [pip] Python deps ready
)
REM ── nsepython separate check (NSE option chain data) ─────────
!PYTHON! -c "import nsepython" >nul 2>&1
IF ERRORLEVEL 1 (
    echo  [pip] Installing nsepython...
    !PYTHON! -m pip install -q nsepython
)

REM ── dhanhq: only install if Dhan mode and not already present ─
IF "!DHAN_MODE!"=="DHAN_WS" (
    !PYTHON! -c "import dhanhq" >nul 2>&1
    IF ERRORLEVEL 1 (
        echo  [pip] Installing dhanhq...
        !PYTHON! -m pip install -q dhanhq
    )
)

REM ── Node deps: only install if node_modules missing ───────────
cd /d "%~dp0app\frontend"
IF NOT EXIST "node_modules" (
    echo  [npm] First run - installing frontend packages ^(one time only^)...
    CALL !NPM! install --silent
    IF ERRORLEVEL 1 (
        echo  [ERROR] npm install failed.
        pause & exit /b 1
    )
    echo  [npm] Done
) ELSE (
    echo  [npm] node_modules exists - skipping
)
cd /d "%~dp0"

REM ── Dirs ──────────────────────────────────────────────────────
IF NOT EXIST "%~dp0data"                mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
IF NOT EXIST "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

echo.
echo  Launching...
echo.

REM ── Backend ───────────────────────────────────────────────────
start "AlgoTrade API" cmd /k "SET PATH=!PATH! && title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 3 /nobreak >nul

REM ── Frontend ──────────────────────────────────────────────────
start "AlgoTrade Dashboard" cmd /k "SET PATH=!PATH! && title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && !NPM! run dev"
timeout /t 4 /nobreak >nul

REM ── Dhan Ticker ───────────────────────────────────────────────
IF "!DHAN_MODE!"=="DHAN_WS" (
    start "Dhan Ticker" cmd /k "SET PATH=!PATH! && SET DHAN_CLIENT_ID=!DHAN_CLIENT_ID! && SET DHAN_ACCESS_TOKEN=!DHAN_ACCESS_TOKEN! && title Dhan WebSocket Ticker && cd /d "%~dp0" && !PYTHON! -c "from algo.dhan_ticker import start_dhan_ticker; import time; start_dhan_ticker([260105,256265]); print('[Dhan] Running'); [time.sleep(60) for _ in iter(int,1)]""
)

REM ── Algo ──────────────────────────────────────────────────────
IF "%CHOICE%"=="1" (
    start "Calendar Algo" cmd /k "SET PATH=!PATH! && title Calendar Spread Algo && cd /d "%~dp0" && !PYTHON! algo\Calendaralgofinal.py"
)
IF "%CHOICE%"=="2" (
    start "Multi-Strategy" cmd /k "SET PATH=!PATH! && title Multi-Strategy Algo && cd /d "%~dp0" && !PYTHON! algo\multistrategy.py"
)

timeout /t 2 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo  ==========================================================
echo   DONE - Platform is running
echo.
echo   Dashboard : http://localhost:5173
echo   API       : http://localhost:8000/docs
echo   Login     : demo@algotrade.in / demo123
IF "!DHAN_MODE!"=="DHAN_WS" (
echo   Dhan WS   : ENABLED
) ELSE (
echo   Dhan WS   : OFF  (add token to .env to enable)
)
echo  ==========================================================
echo.
pause
