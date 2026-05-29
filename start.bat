@echo off
setlocal enabledelayedexpansion
title AlgoTrade NSE Platform
color 0A

echo.
echo  ==========================================================
echo   ALGOTRADE NSE F^&O PLATFORM  --  Single Click Start
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
SET "PATH=%PATH%;C:\Program Files\Python311\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python310\Scripts"
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

REM Search common install paths if still not found
IF "!PYTHON!"=="" (
    FOR %%D IN (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Program Files\Python311\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
    ) DO (
        IF "!PYTHON!"=="" IF EXIST %%D SET PYTHON=%%D
    )
)

IF "!PYTHON!"=="" (
    echo.
    echo  [ERROR] Python not found on this system.
    echo.
    echo  Install Python 3.10+ from https://python.org/downloads
    echo  IMPORTANT: Check "Add python.exe to PATH" during install
    echo.
    pause & exit /b 1
)
FOR /F "tokens=2 delims= " %%V IN ('!PYTHON! --version 2^>^&1') DO SET PYVER=%%V
echo  [OK] Python !PYVER! found

REM ── Auto-locate Node / npm ───────────────────────────────────
SET NPM=
SET NODE=
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
        "%PROGRAMFILES%\nodejs\npm.cmd"
    ) DO (
        IF "!NPM!"=="" IF EXIST %%D (
            SET NPM=%%D
            FOR %%X IN (%%D) DO SET "PATH=!PATH!;%%~dpX"
        )
    )
)

IF "!NPM!"=="" (
    echo.
    echo  [ERROR] Node.js / npm not found on this system.
    echo.
    echo  Install Node.js 18+ from https://nodejs.org/en/download
    echo  Choose the LTS version installer for Windows
    echo  After installing, RESTART this bat file
    echo.
    pause & exit /b 1
)
FOR /F %%V IN ('node --version 2^>^&1') DO SET NODEVER=%%V
echo  [OK] Node.js !NODEVER! found

REM ── Load .env ────────────────────────────────────────────────
IF EXIST "%~dp0.env" (
    FOR /F "usebackq tokens=1,* delims==" %%A IN ("%~dp0.env") DO (
        SET "LINE=%%A"
        IF NOT "!LINE:~0,1!"=="#" IF NOT "%%A"=="" SET "%%A=%%B"
    )
    echo  [OK] .env loaded
) ELSE (
    echo  [TIP] Copy .env.example to .env to configure Dhan token
)

REM ── Dhan token status ────────────────────────────────────────
IF "%DHAN_CLIENT_ID%"=="" (
    echo  [DHAN] No token -- using NSE Direct API (free^)
) ELSE (
    echo  [DHAN] Token found -- Dhan WebSocket ticks enabled
)
echo.

REM ── Python dependencies ──────────────────────────────────────
echo  [1/3] Installing Python dependencies...
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
IF NOT "%DHAN_CLIENT_ID%"=="" (
    !PYTHON! -m pip install -q dhanhq 2>nul
    echo  [1/3] dhanhq installed
)
echo  [1/3] Python deps done

REM ── Node / Frontend dependencies ─────────────────────────────
echo  [2/3] Checking frontend dependencies...
cd /d "%~dp0app\frontend"
IF NOT EXIST "node_modules" (
    echo  [2/3] First run -- installing npm packages...
    CALL !NPM! install
    IF ERRORLEVEL 1 (
        echo.
        echo  [ERROR] npm install failed.
        echo  Make sure Node.js 18+ is installed from nodejs.org
        echo.
        pause & exit /b 1
    )
) ELSE (
    CALL !NPM! install --prefer-offline --silent 2>nul
)
echo  [2/3] Frontend deps done
cd /d "%~dp0"

REM ── Create runtime directories ───────────────────────────────
IF NOT EXIST "%~dp0data"                mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
IF NOT EXIST "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

REM ── Start Backend ────────────────────────────────────────────
echo  [3/3] Starting API backend on :8000...
start "AlgoTrade API" cmd /k "SET PATH=!PATH! && title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 4 /nobreak >nul

REM ── Start Frontend ───────────────────────────────────────────
echo  [3/3] Starting React dashboard on :5173...
start "AlgoTrade Dashboard" cmd /k "SET PATH=!PATH! && title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && !NPM! run dev"
timeout /t 5 /nobreak >nul

REM ── Open browser ─────────────────────────────────────────────
start "" "http://localhost:5173"

echo.
echo  ==========================================================
echo   OK  Platform running
echo.
echo   Dashboard : http://localhost:5173
echo   API docs  : http://localhost:8000/docs
echo   Login     : demo@algotrade.in / demo123
echo.
echo   For live algo signals: run profitmachine.bat
echo  ==========================================================
echo.
pause
