@echo off
title Git Push — Upstox Integration
color 0A
cd /d "%~dp0"
echo.
echo [GIT] Status...
git status --short
echo.
echo [GIT] Staging all tracked changes...
git add -A
echo [GIT] Committing...
git commit -m "feat: wire Upstox access token — dashboard live with real-time NSE data"
IF ERRORLEVEL 1 (
    echo [GIT] Nothing new to commit — pushing existing commits...
)
echo [GIT] Pushing to GitHub...
git push origin main
echo.
echo ==============================
echo  Push complete!
echo  Repo: github.com/manujagupta12/kite_source
echo ==============================
echo.
pause
