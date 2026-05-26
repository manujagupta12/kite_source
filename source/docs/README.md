# AlgoTrade — NIFTY/BANKNIFTY Algorithmic Trading System

> Multi-strategy calendar spread algo with live regime detection,
> P&L tracking, backtesting, and a subscription trading platform.

---

## Repository Structure

```
source/
│
├── algo/                          # Core trading scripts
│   ├── calendar.py                # Calendar spread strategy
│   ├── multistrategy.py           # 7-strategy system
│   ├── regime_engine.py           # Market regime detector (8 regimes)
│   └── trade_logger.py            # P&L tracking + trade input
│
├── backtest/                      # Backtesting engine
│   ├── backtest_engine.py         # Core backtesting framework
│   ├── data_loader.py             # NSE historical data loader
│   ├── strategy_runner.py         # Runs all strategies on historical data
│   └── reports/                   # Output reports (auto-generated)
│
├── app/                           # Web platform (Phase 3)
│   ├── backend/                   # FastAPI backend
│   ├── frontend/                  # React dashboard
│   └── docker-compose.yml
│
├── docs/
│   ├── SETUP.md                   # Installation guide
│   ├── STRATEGIES.md              # Strategy documentation
│   ├── BACKTEST_RESULTS.md        # Backtest results (auto-generated)
│   └── PRODUCT_ROADMAP.md         # Full product plan
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## Quick Start

### 1. Clone the repo
```bash
git clone https://github.com/manujagupta12/source.git
cd source
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your data path
Edit `algo/calendar.py` or `algo/multistrategy.py`:
```python
FILEPATH = r"C:\AlgoTrading\data\multitrade_feed.xls"
```

### 4. Run calendar spread algo
```bash
python algo/calendar.py
```

### 5. Run multi-strategy system
```bash
python algo/multistrategy.py
```

### 6. Log trades interactively
While running, press **Ctrl+C** → type `enter` / `close` / `summary`

---

## Strategies Implemented

| ID | Strategy | Market Condition | Risk |
|----|----------|-----------------|------|
| S1 | Calendar Spread | Sideways / Low VIX | 🟢 LOW |
| S2 | Iron Condor | Sideways / Range-bound | 🟢 LOW |
| S3 | Short Straddle | High IV / Sideways | 🟡 MEDIUM |
| S4 | Momentum Breakout | Trending | 🟡 MEDIUM |
| S5 | Delta Hedge Strangle | VIX Spikes | 🟠 HIGH |
| S6 | Expiry 0DTE | Expiry Week | 🟠 HIGH |
| S7 | Ratio Spread | Mild Directional | 🟡 MEDIUM |

---

## Market Regimes Detected

| Regime | Condition | Action |
|--------|-----------|--------|
| R1 DEAD MARKET | No movement, IV crush | Reduce to 25% size |
| R2 SIDEWAYS LOW | VIX < 13 | Full size, calendar |
| R3 SIDEWAYS HIGH IV | VIX 13-16 | Sell premium |
| R4 TRENDING BULL | Spot rising + CE volume | Momentum CE |
| R5 TRENDING BEAR | Spot falling + PE volume | Momentum PE |
| R6 HIGH VOL | VIX 19-22 | 50% size, buy vol |
| R7 EXPIRY | DTE ≤ 3 | 0DTE straddle |
| R8 EXTREME PANIC | VIX > 22 | STOP — no new trades |

---

## Roadmap

- [x] Phase 1 — Live algo scripts
- [x] Phase 1 — Regime detection engine
- [x] Phase 1 — Trade logger + P&L tracking
- [ ] Phase 2 — Backtesting engine (5-year NSE data)
- [ ] Phase 3 — Web dashboard + app
- [ ] Phase 3 — Paper trading simulator
- [ ] Phase 4 — Subscription platform

---

## License

Private — All rights reserved. © 2025 manujagupta12
