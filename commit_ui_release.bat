@echo off
title AlgoTrade — Commit UI Release
cd /d "%~dp0"
echo.
echo  Committing UI release...
echo.

git add app/frontend/src/App.jsx app/frontend/src/main.jsx app/backend/main.py landing/index.html app/frontend/public/landing.html kite_login.py commit_ui_release.bat

git commit -m "feat(ui): neon glass theme, candlestick charts, signal flash + 3D landing page" -m "- Dashboard redesign: neon glass terminal theme (glass cards, glow accents, staggered entry animations, crystal login screen)" -m "- Candlestick charts with line toggle on all signal cards (custom recharts shape, capped at 80 bars)" -m "- New-signal flash animation for every strategy (S1-S5, Equity, Gold, MCX)" -m "- Chart data fallback chain: NSE -> Dhan intraday -> Yahoo -> simulated (labeled SIM)" -m "- Fixed: strategy filter persisted stringified function in localStorage (hid all signals); self-heals on load" -m "- Fixed: perf regression (backdrop-filter over ticker, unstable React keys remounting cards, duplicate charts)" -m "- New: 3D crystallization landing page (Three.js) with live backend data at /landing.html" -m "- Re-enabled Upstox OAuth flow (/broker/upstox-auth); added kite_login.py daily token helper"

git tag -a v3.1.0-ui -m "UI overhaul: neon glass dashboard theme, candlestick charts, signal flash animations, 3D crystallization landing page"

echo.
git log --oneline -1
git tag --sort=-creatordate | findstr v3.1.0-ui
echo.
set /p PUSH="Push to origin with tags? (y/n): "
if /i "%PUSH%"=="y" git push origin main --tags
echo.
echo  Done.
pause
