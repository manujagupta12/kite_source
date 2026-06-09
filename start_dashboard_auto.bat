@echo off
setlocal enabledelayedexpansion
title AlgoTrade Dashboard — Auto Start
color 0A

echo.
echo  ============================================================
echo   ALGOTRADE DASHBOARD — AUTO LAUNCH
echo   Backend  : http://localhost:8000
echo   Dashboard: http://localhost:5173
echo  ============================================================
echo.

REM ── PATH setup ───────────────────────────────────────────────
SET "PATH=%PATH%;C:\Program Files\nodejs;C:\Program Files (x86)\nodejs"
SET "PATH=%PATH%;%APPDATA%\npm"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python314"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python312"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python311"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python314\Scripts"
SET "PATH=%PATH%;%LOCALAPPDATA%\Programs\Python\Python313\Scripts"
SET "PATH=%PATH%;C:\Program Files\Python314;C:\Program Files\Python313"
SET "PATH=%PATH%;C:\Program Files\Python314\Scripts;C:\Program Files\Python313\Scripts"

REM ── Find Python ───────────────────────────────────────────────
SET PYTHON=python
FOR %%P IN (python python3 py) DO (
    IF "!PYTHON!"=="python" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET PYTHON=%%P
    )
)

REM ── Find npm ──────────────────────────────────────────────────
SET NPM=npm
FOR %%P IN (npm.cmd npm) DO (
    IF "!NPM!"=="npm" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET NPM=%%P
    )
)

REM ── Load .env ─────────────────────────────────────────────────
IF EXIST "%~dp0.env" (
    FOR /F "usebackq tokens=1,* delims==" %%A IN ("%~dp0.env") DO (
        SET "LINE=%%A"
        IF NOT "!LINE:~0,1!"=="#" IF NOT "%%A"=="" SET "%%A=%%B"
    )
    echo  [OK] .env loaded
)

REM ── Set margin ────────────────────────────────────────────────
SET AVAILABLE_MARGIN=5000000
IF EXIST "%~dp0data\margin_today.txt" (
    SET /P AVAILABLE_MARGIN=<"%~dp0data\margin_today.txt"
)
echo  [OK] Margin: Rs.!AVAILABLE_MARGIN!

REM ── Install Python deps if needed ─────────────────────────────
!PYTHON! -c "import fastapi" >nul 2>&1
IF ERRORLEVEL 1 (
    echo  [pip] Installing backend packages...
    !PYTHON! -m pip install -q -r "%~dp0app\backend\requirements.txt"
)

REM ── Install frontend deps if needed ───────────────────────────
cd /d "%~dp0app\frontend"
IF NOT EXIST "node_modules" (
    echo  [npm] Installing frontend packages...
    CALL !NPM! install --silent
)
IF EXIST "node_modules\.vite" rmdir /s /q "node_modules\.vite" >nul 2>&1

cd /d "%~dp0"

REM ── Create required dirs ──────────────────────────────────────
IF NOT EXIST "%~dp0data"                mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs"     mkdir "%~dp0data\trade_logs"
IF NOT EXIST "%~dp0data\bhavcopy_cache" mkdir "%~dp0data\bhavcopy_cache"

echo.
echo  [1/3] Starting Backend API (FastAPI)...
start "AlgoTrade Backend" cmd /k "SET PATH=!PATH! && SET AVAILABLE_MARGIN=!AVAILABLE_MARGIN! && title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 4 /nobreak >nul

echo  [2/3] Starting Frontend (Vite)...
start "AlgoTrade Frontend" cmd /k "SET PATH=!PATH! && title AlgoTrade Dashboard && cd /d "%~dp0app\frontend" && !NPM! run dev -- --force"
timeout /t 5 /nobreak >nul

echo  [3/3] Opening dashboard in browser...
start "" "http://localhost:5173"

echo.
echo  ============================================================
echo   DONE — Platform is running
echo.
echo   Dashboard : http://localhost:5173
echo   API Docs  : http://localhost:8000/docs
echo   Login     : demo@algotrade.in / demo123
echo   Margin    : Rs.!AVAILABLE_MARGIN!
echo.
echo   To run algo: open another terminal and run
echo     python algo\multistrategy.py
echo  ============================================================
echo.
pause
