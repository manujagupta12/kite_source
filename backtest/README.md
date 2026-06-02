# kite_source — Backtest Module

Real NSE F&O Bhavcopy backtesting. No mocks. No random data.

## Setup

```cmd
cd C:\AlgoTrading\kite_source\backtest
pip install -r requirements.txt
mkdir results
```

## Run

```cmd
# Full year 2024 backtest
python run_backtest.py --from 2024-01-01 --to 2024-12-31

# Shorter test (faster, for validation)
python run_backtest.py --from 2024-06-01 --to 2024-12-31

# Custom lot size (BankNifty = 15)
python run_backtest.py --from 2024-01-01 --to 2024-12-31 --lot 15
```

## What it does

1. **Downloads** real NSE F&O Bhavcopy CSV files from `nsearchives.nseindia.com`
2. **Caches** them in `data/bhavcopy/` so re-runs are instant
3. **Splits** 70% in-sample / 30% out-of-sample (strict chronological — no lookahead)
4. **Runs** 5 strategies reconstructed from `algo/multistrategy.py` and `algo/pcr_strategy.py`
5. **Saves** results to `results/`

## Outputs

| File | Description |
|------|-------------|
| `results/backtest_trades.csv` | Every trade: entry/exit date, price, P&L, exit reason |
| `results/backtest_results.csv` | Per-strategy summary metrics |
| `results/walkforward_results.csv` | Walk-forward window metrics |
| `results/backtest_report.md` | Full human-readable report |
| `results/backtest.log` | Execution log |

## Strategies tested

| Strategy | File origin | Type |
|----------|-------------|------|
| PCR Mean-Reversion | `algo/pcr_strategy.py` | Options directional |
| ATM Strangle | `algo/multistrategy.py` | Short volatility |
| EMA Crossover | `algo/multistrategy.py` | Futures trend |
| OI Build-up | `algo/nse_connector.py` | OI-based directional |
| Calendar Spread | `algo/Calendaralgofinal.py` | Inter-expiry spread |

## Key metrics explained

- **Win Rate** — % of trades that were profitable
- **Profit Factor** — Gross profit / Gross loss (>1.5 = good, >2.0 = excellent)
- **Max Drawdown** — Worst peak-to-trough loss (lower = better)
- **Sharpe Ratio** — Risk-adjusted return (>1.0 = acceptable, >2.0 = strong)
- **Signal Credibility** — STRONG / MODERATE / WEAK / UNRELIABLE

## Share results

After running, share `results/backtest_report.md` and `results/backtest_results.csv`.
The README will be updated with actual numbers.
