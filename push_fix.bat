@echo off
title Push — PCR Signal Fix
color 0A
cd /d "%~dp0"
echo.
echo [GIT] Removing stale lock if present...
if exist ".git\index.lock" del /f ".git\index.lock"
echo [GIT] Staging all changes...
git add -A
echo [GIT] Committing...
git commit -m "fix(pcr): include WATCH zone signals + /debug/pcr endpoint + timeout

- pcr_strategy.py: lower score threshold 50->40 so WATCH zones
  (PCR 0.60-0.85 and 1.15-1.30, score=45) generate signals.
  Previously only extremes PCR<0.60 or >1.30 fired — WATCH zones
  silently dropped. Now fires SHORT/LONG WATCH signals in between.
- pcr_strategy.py: WATCH directions changed from WATCH_SHORT/WATCH_LONG
  to SHORT/LONG with WATCH tag so dashboard renders them correctly.
- main.py: add /debug/pcr endpoint — shows raw PCR values + zone for
  NIFTY, BANKNIFTY, FINNIFTY even when no signal fires.
- main.py: add asyncio.wait_for(timeout=30s) on PCR executor call in
  signal_loop — previously no timeout, could hang 25s+ on NSE block.
- main.py: _pcr_signal_from_oc() now includes WATCH zones as well."
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
