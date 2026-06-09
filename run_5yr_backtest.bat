@echo off
title AlgoTrade — 5-Year Backtest (2021-2025)
color 0B

echo.
echo  ============================================================
echo   kite_source — 5-YEAR BACKTEST
echo   Period : 2021-01-01 to 2025-12-31
echo   Margin : Rs.50,00,000
echo   Strategies: All (EMA, IronCondor, PCR, Straddle, Calendar,
echo               CreditSpread, VWAP, MomentumBreakout)
echo  ============================================================
echo.

REM ── Find Python ──────────────────────────────────────────────
SET PYTHON=
FOR %%P IN (python python3 py) DO (
    IF "!PYTHON!"=="" (
        WHERE %%P >nul 2>&1
        IF NOT ERRORLEVEL 1 SET PYTHON=%%P
    )
)
IF "!PYTHON!"=="" SET PYTHON=python

REM ── Install backtest requirements ───────────────────────────
echo  [1/3] Checking backtest requirements...
%PYTHON% -m pip install -q pandas numpy scipy tqdm requests --break-system-packages 2>nul || %PYTHON% -m pip install -q pandas numpy scipy tqdm requests
echo  [OK] Dependencies ready

REM ── Run backtest ─────────────────────────────────────────────
echo.
echo  [2/3] Starting 5-year backtest — this will take 10-30 mins
echo  [INFO] Results will be saved to backtest\results\
echo.
cd /d "%~dp0backtest"
%PYTHON% run_backtest.py --from 2021-01-01 --to 2025-12-31 --margin 5000000 --windows 5

echo.
echo  [3/3] Backtest complete!
echo  Results: %~dp0backtest\results\
echo.
echo  Files generated:
dir /b results\
echo.
pause
