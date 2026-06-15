@echo off
title Push — Signal Loop Fix
color 0A
cd /d "%~dp0"
echo.
echo [GIT] Removing stale lock if present...
if exist ".git\index.lock" del /f ".git\index.lock"
echo [GIT] Staging all changes...
git add -A
echo [GIT] Committing...
git commit -m "fix: isolate equity and multistrategy errors in signal loop

Wrap each strategy block in its own try/except so a transient
equity-fetch error cannot crash the entire cycle % 10 block and
starve S2-S7 signals. Also adds full_run section to debug endpoint.

Strategies confirmed live: S2 Iron Condor, S3 Short Straddle, S4 Momentum."
IF ERRORLEVEL 1 (
    echo [GIT] Nothing new to commit — pushing existing commits...
)
echo [GIT] Pushing to GitHub...
git push origin main
echo.
echo ==============================
echo  Push complete!
echo ==============================
echo.
pause
