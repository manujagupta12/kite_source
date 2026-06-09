# kite_source — AlgoTrade Platform

## What This Is
NSE F&O signal dashboard. React+Vite frontend → FastAPI backend → NSE Direct API + Dhan WebSocket.
Generates live trading signals for BANKNIFTY/NIFTY Calendar, Iron Condor, PCR, Equity and Gold.

## Stack
- Frontend: `app/frontend/` — React + Vite, port 5173
- Backend: `app/backend/main.py` — FastAPI, port 8000
- Algo: `algo/` — signal generators, NSE fetcher, Gold strategy
- Backtest: `backtest/` — walk-forward engine, 5-strategy results

## Start Commands
```
restart_backend.bat        — kill old process, start fresh backend
start.bat                  — start both backend + frontend
launch_with_commit.bat     — git commit + start both
```

## Hard Rules
1. All changes must be ADDITIVE — trade logging, paper trading, subscription gating must never break
2. No mock/fake data in production signals — `_validate_signal()` enforces this
3. Persist signals to `data/signals_YYYY-MM-DD.json` on every write (done)
4. `_DATA_DIR = Path("/data")` on Windows resolves to C:\data — mkdir is idempotent

## Architecture Notes

### Signal Flow
```
signal_loop (asyncio, 3s tick during market hours)
  ├── _nse_signal()          → S1 Calendar (NSE option chain)
  ├── _run_all_strategies()  → S2–S7 (NSE data via data_provider)
  ├── _pcr_signal_live()     → PCR contrarian (every 90s)
  ├── Gold signals           → Delta Exchange XAUUSD (every 5 min, 24/7)
  └── generate_equity_signals() → top-30 NIFTY50 (every 30s)
```

### NSE Data Pipeline
- `algo/nse_fetcher.py` — scrapes NSE option chain directly (session cookie refresh every 120s)
- Circuit breaker: 3 fails → 5-min backoff → auto-recover
- `_NSE_OK` flag is now dynamically synced with fetcher state (live recovery, not just startup)

### Signal Persistence
- Signals written to `data/signals_YYYY-MM-DD.json` every 30s during market hours
- Loaded back on startup — dashboard shows today's history even after restart

### Key Endpoints
- `GET /health` — quick status
- `GET /signals/audit` — per-strategy health, feed status, fail counts (use at 9:15 AM)
- `GET /signals/accuracy` — real outcome tracker (zero defaults, builds from your records)
- `POST /signals/record-outcome` — record signal result for accuracy tracking
- `GET /signals/gold` — live Gold signals from Delta Exchange
- `GET /gold/ticker` — live XAUUSD price

## Known Issues / Active Work
- Delta Exchange OHLCV API returns 400 (resolution parameter format) — fix in `delta_connector.py` sent, needs backend restart
- `_DATA_DIR = "/data"` on Windows = C:\data — works but non-standard; consider setting DATA_DIR in .env
- OI_Buildup strategy: NSE 2024+ Bhavcopy dropped CHG_IN_OI column — strategy cannot generate signals with current data format

## Backtest Results Summary (2024–2025 NSE Bhavcopy, 1,300 trades)

Full report: `backtest/results/backtest_report.md`

| Strategy | OOS Result | Live Trading |
| --- | --- | --- |
| PCR_MeanReversion | ❌ Fails OOS (BANKNIFTY PF=0.85) | Do NOT trade |
| ATM_Strangle | ❌ Loses consistently all windows | Do NOT trade |
| IronCondor | ⚠ MODERATE 3/5 windows | Paper trade only |
| EMA_Crossover | ⚠ Strong IS, anomalous OOS numbers | Small size, manual oversight |
| Calendar_Spread | ❌ Fails with EOD data (data limitation) | Paper trade, verify live |
| OI_Buildup | ❓ Not testable (missing CHG_IN_OI) | Cannot assess |
| Equity_Momentum | ❓ Not testable (OHLCV not downloaded) | Cannot assess |
| Gold XAUUSD | ❓ Not testable (no offline data) | Cannot assess |

**Honest summary:** None of the 5 tested strategies meet the STRONG+consistent bar for live trading with confidence. EMA_Crossover shows the most genuine signal. No strategy should be traded live at full size without 90 days of paper trade validation.

## Credentials / Secrets
- Rotate `DHAN_ACCESS_TOKEN` in `.env` — was exposed in git (check git history)
- `SECRET_KEY` in main.py: change before production deployment
- `.env` is gitignored

## Git
- Repo: github.com/manujagupta12/kite_source (private, branch: main)
- Commit frequently via `launch_with_commit.bat`
