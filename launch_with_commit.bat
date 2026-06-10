@echo off
setlocal enabledelayedexpansion
title AlgoTrade — Commit + Start
color 0A

echo.
echo  ================================================================
echo   ALGOTRADE  —  Commit changes and start dashboard
echo  ================================================================
echo.

cd /d "%~dp0"

REM ── Check git ──────────────────────────────────────────────────
WHERE git >nul 2>&1
IF ERRORLEVEL 1 (
    echo  [WARN] git not found on PATH — skipping commit
    goto :START
)

echo  [GIT] Staging all changes...
git add . 2>&1

echo  [GIT] Committing...
git commit -m "fix: signal_loop crash on empty signals (IndexError at cycle 30), add supervised auto-restart for engine loops, fix NameError in Yahoo indices fallback, make DEMO_MODE functional" 2>&1

IF ERRORLEVEL 1 (
    echo  [GIT] Nothing to commit or commit failed — continuing
) ELSE (
    echo  [GIT] Committed successfully — pushing to GitHub...
    git push origin main 2>&1
    IF ERRORLEVEL 1 (
        echo  [GIT] Push failed — check remote/credentials
    ) ELSE (
        echo  [GIT] Pushed to GitHub successfully
    )
)

:START
echo.
echo  Starting AlgoTrade...
echo.

REM ── Kill stale backend (port 8000) and frontend (port 5173) ───
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo  [CLEANUP] Killing stale backend PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    echo  [CLEANUP] Killing stale frontend PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

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

REM ── Find Python ─────────────────────────────────────────────
SET PYTHON=
FOR %%P IN (python python3 py) DO (
    IF "!PYTHON!"=="" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET PYTHON=%%P
    )
)
FOR %%D IN (
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
) DO (
    IF "!PYTHON!"=="" IF EXIST %%D SET PYTHON=%%D
)
IF "!PYTHON!"=="" (echo [ERROR] Python not found & pause & exit /b 1)
FOR /F "tokens=2 delims= " %%V IN ('!PYTHON! --version 2^>^&1') DO SET PYVER=%%V
echo  [OK] Python !PYVER!

REM ── Find npm ─────────────────────────────────────────────────
SET NPM=
FOR %%P IN (npm.cmd npm) DO (
    IF "!NPM!"=="" (WHERE %%P >nul 2>&1 && IF NOT ERRORLEVEL 1 SET NPM=%%P)
)
IF "!NPM!"=="" (echo [ERROR] Node.js not found & pause & exit /b 1)

REM ── Load .env ────────────────────────────────────────────────
IF EXIST "%~dp0.env" (
    FOR /F "usebackq tokens=1,* delims==" %%A IN ("%~dp0.env") DO (
        SET "LINE=%%A"
        IF NOT "!LINE:~0,1!"=="#" IF NOT "%%A"=="" SET "%%A=%%B"
    )
    echo  [OK] .env loaded
)

REM ── Python deps ───────────────────────────────────────────────
!PYTHON! -c "import fastapi" >nul 2>&1
IF ERRORLEVEL 1 (
    echo  Installing Python packages...
    !PYTHON! -m pip install -q -r "%~dp0app\backend\requirements.txt"
)
!PYTHON! -c "import nsepython" >nul 2>&1
IF ERRORLEVEL 1 (!PYTHON! -m pip install -q nsepython)

REM ── Node deps ─────────────────────────────────────────────────
IF NOT EXIST "%~dp0app\frontend\node_modules" (
    echo  Installing frontend packages...
    cd /d "%~dp0app\frontend" && CALL !NPM! install --silent
    cd /d "%~dp0"
)

REM ── Dirs ──────────────────────────────────────────────────────
IF NOT EXIST "%~dp0data"            mkdir "%~dp0data"
IF NOT EXIST "%~dp0data\trade_logs" mkdir "%~dp0data\trade_logs"

echo.
echo  Launching backend and frontend...
echo.

start "AlgoTrade API" cmd /k "title AlgoTrade API && cd /d "%~dp0app\backend" && !PYTHON! main.py"
timeout /t 3 /nobreak >nul
start "AlgoTrade Frontend" cmd /k "title AlgoTrade Frontend && cd /d "%~dp0app\frontend" && !NPM! run dev"
timeout /t 4 /nobreak >nul
start "" "http://localhost:5173"

echo.
echo  ================================================================
echo   RUNNING
echo   Dashboard  : http://localhost:5173
echo   API        : http://localhost:8000
echo   System Health tab shows which signals are actually working
echo   Gold signals run 24/7 (check right now even though NSE closed)
echo  ================================================================
echo.
pause
