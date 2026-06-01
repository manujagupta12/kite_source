@echo off
setlocal enabledelayedexpansion
title AlgoTrade NSE Platform
color 0A

echo.
echo  ==========================================================
echo   ALGOTRADE NSE F^&O PLATFORM  --  Single Click Start
echo  ==========================================================
echo.

SET "PATH=%PATH%;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs"
SET "PATH=%PATH%;%APPDATA%\npm;%APPDATA%\nvm\current"
SET "PATH=%PATH%;C:\Program Files\Python314;C:\Program Files\Python313"
SET "PATH=%PATH%;C:\Program Files\Python312;C:\Program Files\Python311"
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
        "C:\Program Files\Python314\python.exe"
        "C:\Program Files\Python313\python.exe"
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
) ELSE (
    echo  [TIP] Copy .env.example to .env to configure Dhan token
)

REM ── Python deps: skip if already installed ────────────────────
!PYTHON! -c "import fastapi" >nul 2>&1
IF ERRORLEVEL 1 (
    echo  Installing Python packages ^(first run - one time only^)...
    !PYTHON! -m pip install -q -r "%~dp0app\backend\requirements.txt"
) ELSE (
    echo  [OK] Python deps ready
)
!PYTHON! -c "import nsepython" >nul 2>&1
IF ERRORLEVEL 1 (!PYTHON! -m pip install -q nsepython)

REM ── Node deps: skip if node_modules exists ────────────────────
cd /d "%~dp0app\frontend"
IF NOT EXIST "node_modules" (
    echo  Installing frontend packages ^(first run - one time only^)...
    CALL !NPM! install --silent
) ELSE (
    echo  [OK] Frontend deps ready
)
cd /d "%~dp0"

REM ── Dirs ──────────────────────────────────────────────────────
IF NOT EXIST "%~dp0data"            mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs" mkdir "%~dp0data\trade_logs"

echo.
echo  Starting...

start "AlgoTrade API" cmd /k "SET PATH=!PATH! && title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 3 /nobreak >nul
start "AlgoTrade Dashboard" cmd /k "SET PATH=!PATH! && title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && !NPM! run dev"
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo  ==========================================================
echo   DONE
echo   Dashboard : http://localhost:5173
echo   Login     : demo@algotrade.in / demo123
echo  ==========================================================
echo.
pause
