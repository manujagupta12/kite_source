"""
vwap_reversal.py — VWAP Mean Reversion Strategy (NEW)
======================================================
Rationale: Indian indices (NIFTY/BN) exhibit strong intraday VWAP reversion.
Institutional algo orders are VWAP-anchored → price gravitates back to VWAP.

Signal:
  - Price deviates > 1.5 std deviations from VWAP → fade the move
  - RSI confirms oversold (<35) or overbought (>65)
  - Volume must be elevated (participants at the extreme)

Instruments: NIFTY Futures, BANKNIFTY Futures (intraday only)
Timeframe: 5-minute bars
Exit by: 3:15 PM IST (mandatory square-off — intraday only)

Entry conditions:
  LONG:  price < VWAP - 1.5σ AND RSI < 35 AND volume > avg
  SHORT: price > VWAP + 1.5σ AND RSI > 65 AND volume > avg

Exit conditions:
  - Target: price crosses VWAP (mean reversion complete)
  - Stop: price extends further 0.5σ beyond entry (2.0σ from VWAP)
  - Time: 3:15 PM forced exit
  - Max hold: 60 minutes

Performance expectations (based on similar NSE strategies):
  Win rate: 58-65%, PF: 1.8-2.5, Sharpe: 2.0-3.5 (intraday annualized)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from datetime import time as dtime
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime
from risk_manager import RiskManager

logger = logging.getLogger(__name__)

MARKET_CLOSE = dtime(15, 15)
ENTRY_CUTOFF = dtime(14, 30)   # No new entries after 2:30 PM


@dataclass
class VWAPConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    vwap_std_entry: float = 1.5   # Entry at 1.5σ from VWAP
    vwap_std_stop: float = 2.0    # Stop at 2.0σ from VWAP
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_overbought: float = 65.0
    volume_mult: float = 1.3      # Volume must be > 1.3× avg
    volume_avg_periods: int = 20
    max_hold_bars: int = 12       # 12 × 5min = 60 minutes
    timeframe_minutes: int = 5


INSTRUMENT_VWAP_PARAMS = {
    "NIFTY":     VWAPConfig(instrument="NIFTY",     lot_size=50),
    "BANKNIFTY": VWAPConfig(instrument="BANKNIFTY", lot_size=15, vwap_std_entry=1.3),
    "FINNIFTY":  VWAPConfig(instrument="FINNIFTY",  lot_size=40),
}


# ─────────────────────────────────────────────
#  INDICATORS
# ─────────────────────────────────────────────

def compute_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute anchored VWAP + upper/lower bands.
    Resets at start of each trading day.
    df must have DatetimeIndex with 5-min bars.
    """
    df = df.copy()
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["cum_vol"] = df.groupby(df.index.date)["volume"].cumsum()
    df["cum_tpvol"] = df.groupby(df.index.date).apply(
        lambda g: (g["typical_price"] * g["volume"]).cumsum()
    ).values
    df["vwap"] = df["cum_tpvol"] / df["cum_vol"]

    # Rolling std of typical price from VWAP (within day)
    def daily_std(group):
        vwap = group["cum_tpvol"] / group["cum_vol"]
        deviations = group["typical_price"] - vwap
        variance = (deviations ** 2 * group["volume"]).cumsum() / group["cum_vol"]
        return variance.pow(0.5)

    df["vwap_std"] = df.groupby(df.index.date).apply(daily_std).values
    df["vwap_upper"] = df["vwap"] + df["vwap_std"]
    df["vwap_lower"] = df["vwap"] - df["vwap_std"]

    return df


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def compute_vwap_signals(df: pd.DataFrame, cfg: VWAPConfig) -> pd.DataFrame:
    df = compute_vwap(df)
    df["rsi"] = compute_rsi(df["close"], cfg.rsi_period)
    df["vol_avg"] = df["volume"].rolling(cfg.volume_avg_periods).mean()
    df["vol_ok"] = df["volume"] > cfg.volume_mult * df["vol_avg"]

    df["long_signal"] = (
        (df["close"] < df["vwap"] - cfg.vwap_std_entry * df["vwap_std"]) &
        (df["rsi"] < cfg.rsi_oversold) &
        df["vol_ok"]
    )
    df["short_signal"] = (
        (df["close"] > df["vwap"] + cfg.vwap_std_entry * df["vwap_std"]) &
        (df["rsi"] > cfg.rsi_overbought) &
        df["vol_ok"]
    )
    return df


# ─────────────────────────────────────────────
#  BACKTEST ENGINE
# ─────────────────────────────────────────────

class VWAPReversalBacktest:

    def __init__(self, cfg: VWAPConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()
        self.trades: List[dict] = []

    def run(self, df: pd.DataFrame) -> pd.DataFrame:
        """df: 5-min OHLCV with DatetimeIndex."""
        df = compute_vwap_signals(df, self.cfg)

        position = 0
        entry_price = 0.0
        entry_bar = 0
        stop_price = 0.0
        target_vwap = 0.0
        entry_time = None
        lots = 0

        for i in range(self.cfg.volume_avg_periods + self.cfg.rsi_period + 2, len(df)):
            bar = df.iloc[i]
            t = df.index[i].time()
            price = bar["close"]
            vwap = bar["vwap"]
            vwap_std = bar["vwap_std"]
            strategy_name = f"VWAP_Reversal_{self.cfg.instrument}"

            # ── Forced exit at 3:15 ──────────────────────────────
            if t >= MARKET_CLOSE and position != 0:
                pnl = (price - entry_price) * position * lots * self.cfg.lot_size
                self._record_trade(df.index[entry_bar], df.index[i], entry_price, price, position, lots, pnl, "TIME_CLOSE")
                self.rm.close_position(strategy_name, pnl)
                position = 0
                continue

            # ── Manage open position ──────────────────────────────
            if position != 0:
                bars_held = i - entry_bar

                # Target: price returns to VWAP
                hit_target = (position == 1 and price >= vwap) or \
                             (position == -1 and price <= vwap)
                # Stop
                hit_stop = (position == 1 and price <= stop_price) or \
                           (position == -1 and price >= stop_price)
                # Max hold
                hit_time = bars_held >= self.cfg.max_hold_bars

                exit_reason = None
                if hit_target:
                    exit_reason = "TARGET"
                elif hit_stop:
                    exit_reason = "STOP"
                elif hit_time:
                    exit_reason = "MAX_HOLD"

                if exit_reason:
                    pnl = (price - entry_price) * position * lots * self.cfg.lot_size
                    self._record_trade(df.index[entry_bar], df.index[i], entry_price, price, position, lots, pnl, exit_reason)
                    self.rm.close_position(strategy_name, pnl)
                    position = 0

            # ── New entry (no new entries after 2:30 PM) ─────────
            if position == 0 and t < ENTRY_CUTOFF and self.rm.allow_trade(strategy_name):
                if bar["long_signal"]:
                    position = 1
                    entry_price = price
                    stop_price = vwap - self.cfg.vwap_std_stop * vwap_std
                    target_vwap = vwap
                    entry_bar = i
                    entry_time = df.index[i]
                    lots = max(1, self.rm.position_size(strategy_name, self.cfg.instrument,
                                                         price, price - stop_price, self.cfg.lot_size))
                    self.rm.open_position(strategy_name)
                    logger.info(f"VWAP LONG {self.cfg.instrument} @ {price:.2f} stop={stop_price:.2f}")

                elif bar["short_signal"]:
                    position = -1
                    entry_price = price
                    stop_price = vwap + self.cfg.vwap_std_stop * vwap_std
                    target_vwap = vwap
                    entry_bar = i
                    entry_time = df.index[i]
                    lots = max(1, self.rm.position_size(strategy_name, self.cfg.instrument,
                                                         price, stop_price - price, self.cfg.lot_size))
                    self.rm.open_position(strategy_name)
                    logger.info(f"VWAP SHORT {self.cfg.instrument} @ {price:.2f} stop={stop_price:.2f}")

        return pd.DataFrame(self.trades)

    def _record_trade(self, date_in, date_out, entry, exit_, direction, lots, pnl, reason):
        self.trades.append({
            "date_in": date_in,
            "date_out": date_out,
            "strategy": f"VWAP_Reversal_{self.cfg.instrument}",
            "direction": "BUY" if direction == 1 else "SELL",
            "entry_price": entry,
            "exit_price": exit_,
            "exit_reason": reason,
            "lots": lots,
            "pnl": pnl,
            "instrument": self.cfg.instrument,
        })
