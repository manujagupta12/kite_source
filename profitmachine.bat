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
echo   STEP 2 of 4 : Choose Algo
echo  ----------------------------------------------------------
echo.
echo  [1] Calendar Spread  (Calendaralgofinal.py)
echo  [2] All 7 Strategies (multistrategy.py)
echo  [3] Dashboard only   (no algo)
echo.
SET /P CHOICE=Enter 1, 2 or 3:

REM ================================================================
REM  STEP 3 -- MARGIN SETUP
REM ================================================================
echo.
echo  ----------------------------------------------------------
echo   STEP 3 of 4 : Available Margin for Today
echo  ----------------------------------------------------------
echo.
echo  This sets your position sizing budget.
echo  Algo will never exceed this — lots scale dynamically.
echo  Formats accepted:  500000  /  5L  /  5,00,000
echo.

REM Try to load saved margin from last session
SET SAVED_MARGIN=
IF EXIST "%~dp0data\margin_today.txt" (
    SET /P SAVED_MARGIN=<"%~dp0data\margin_today.txt"
)

IF NOT "!SAVED_MARGIN!"=="" (
    echo  [SAVED] Last margin: Rs.!SAVED_MARGIN!
    echo.
    SET /P MARGIN_CONFIRM=  Use this again? [Y/N, default Y]:
    IF /I "!MARGIN_CONFIRM!"=="N" (
        SET SAVED_MARGIN=
    )
)

IF "!SAVED_MARGIN!"=="" (
    SET /P AVAILABLE_MARGIN=  Enter margin (Rs.):
) ELSE (
    SET AVAILABLE_MARGIN=!SAVED_MARGIN!
)

REM Save margin for next session
IF NOT "!AVAILABLE_MARGIN!"=="" (
    echo !AVAILABLE_MARGIN!>"%~dp0data\margin_today.txt"
    echo  [OK] Margin set: Rs.!AVAILABLE_MARGIN!
) ELSE (
    SET AVAILABLE_MARGIN=500000
    echo  [DEFAULT] No margin entered - using Rs.500,000
)

REM Set as env var so backend + algos can read it
SET AVAILABLE_MARGIN=!AVAILABLE_MARGIN!

REM ================================================================
REM  STEP 4 -- DEPS CHECK (fast - skips if already installed)
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

REM ── Clear Vite cache so latest code is always served ────────
IF EXIST "node_modules\.vite"  rmdir /s /q "node_modules\.vite"  >nul 2>&1
IF EXIST ".vite"               rmdir /s /q ".vite"               >nul 2>&1
echo  [OK] Vite cache cleared - dashboard will show latest code
cd /d "%~dp0"

REM ── Dirs ──────────────────────────────────────────────────────
IF NOT EXIST "%~dp0data"                mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
IF NOT EXIST "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

echo.
echo  Launching...
echo.

REM ── Backend ───────────────────────────────────────────────────
start "AlgoTrade API" cmd /k "SET PATH=!PATH! && SET AVAILABLE_MARGIN=!AVAILABLE_MARGIN! && SET DHAN_CLIENT_ID=!DHAN_CLIENT_ID! && SET DHAN_ACCESS_TOKEN=!DHAN_ACCESS_TOKEN! && title AlgoTrade API v2.0 && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 3 /nobreak >nul

REM ── Frontend ─────�