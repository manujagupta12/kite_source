@echo off
title Git Push
cd /d "%~dp0"
echo [GIT] Removing stale lock if present...
if exist ".git\index.lock" del /f /q ".git\index.lock"
echo [GIT] Pushing to GitHub...
git push origin main
echo.
echo Push done.
pause
