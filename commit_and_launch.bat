@echo off
cd /d C:\AlgoTrading\kite_source

:: Clear stale git lock if present
if exist .git\index.lock del /f .git\index.lock

:: Stage everything
git add -A

:: Commit with timestamp
for /f "tokens=1-5 delims=/ " %%a in ('echo %DATE% %TIME%') do (
  set DT=%%a-%%b-%%c %%d
)
git commit -m "feat: premium login, onboarding, full settings UI, config API [%DT%]"

:: Push to main
git push origin main

echo.
echo ============================
echo Commit + push done. Launching...
echo ============================
echo.

:: Launch dashboard
call restart_backend.bat

timeout /t 3 /nobreak >nul
start "" http://localhost:5173
