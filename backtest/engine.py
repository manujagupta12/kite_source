"""
backtest/engine.py
==================
Walk-forward backtesting engine.
Simulates real trade execution using Bhavcopy settlement prices.
No lookahead bias — signals on day D are entered on day D+1 open.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import List

import numpy as np
import pandas as pd

from strategies import Signal
from metrics import TradeResult

log = logging.getLogger(__name__)


class BacktestEngine:
    """
    Simulates signal execution against historical Bhavcopy data.

    Rules (matching multistrategy.py behaviour):
      - Signal generated on day D → enter at next day's OPEN (no lookahead)
      - Exit: whichever comes first — TARGET hit, STOP hit, EXPIRY, or new signal flip
      - Position sizing: 1 lot (multiplied in metrics.py by lot_value)
      - No slippage model (Bhavcopy settle prices are end-of-day)
      - Transaction cost: ₹20/trade flat + 0.05% STT on options sell side
    """

    BROKERAGE_PER_TRADE = 20.0    # ₹ per leg
    STT_RATE            = 0.0005  # 0.05% on sell side premium

    def __init__(self, df_options: pd.DataFrame):
        """
        Args:
            df_options: Full Bhavcopy DataFrame (filtered to NIFTY/BANKNIFTY options)
        """
        self.df = df_options.copy()
        self.df["DATE"]      = pd.to_datetime(self.df["DATE"])
        self.df["EXPIRY_DT"] = pd.to_datetime(self.df["EXPIRY_DT"])
        self.df["STRIKE_PR"] = pd.to_numeric(self.df["STRIKE_PR"], errors="coerce")
        self.df["SETTLE_PR"] = pd.to_numeric(self.df["SETTLE_PR"], errors="coerce")
        self.df["OPEN"]      = pd.to_numeric(self.df.get("OPEN", pd.Series()), errors="coerce")

        self.trading_dates = sorted(self.df["DATE"].unique())
        self._date_index   = {d: i for i, d in enumerate(self.trading_dates)}

    # ─── Public API ───────────────────────────────────────────────────────────

    def run(self, signals: List[Signal]) -> List[TradeResult]:
        """Execute all signals and return completed trades."""
        if not signals:
            return []

        signals = sorted(signals, key=lambda s: s.date)
        results = []

        for signal in signals:
            result = self._simulate_trade(signal)
            if result:
                results.append(result)

        log.info("Engine: %d signals → %d completed trades", len(signals), len(results))
        return results

    # ─── Internal ─────────────────────────────────────────────────────────────

    def _simulate_trade(self, signal: Signal) -> TradeResult | None:
        """Simulate a single signal from entry to exit."""

        # Entry: next trading day after signal
        entry_date = self._next_date(signal.date)
        if entry_date is None:
            return None

        entry_price = self._get_price(entry_date, signal)
        if entry_price is None or entry_price <= 0:
            return None

        # Adjust target and stop relative to actual entry (not signal price)
        price_ratio = entry_price / signal.entry_price if signal.entry_price > 0 else 1
        target    = signal.target_price * price_ratio
        stop_loss = signal.stop_loss   * price_ratio

        # Walk forward day by day looking for exit
        date_idx = self._date_index.get(entry_date)
        if date_idx is None:
            return None

        exit_price  = None
        exit_date   = None
        exit_reason = "EXPIRY"

        for future_date in self.trading_dates[date_idx + 1:]:
            day_price = self._get_price(future_date, signal)
            if day_price is None:
                # Option expired / no data — exit at last known price
                exit_price  = entry_price  # conservative: exit at entry
                exit_date   = future_date
                exit_reason = "EXPIRY"
                break

            if signal.direction == "BUY" or signal.direction == "SELL":
                hit_target = day_price >= target if signal.direction == "BUY" else day_price <= target
                hit_stop   = day_price <= stop_loss if signal.direction == "BUY" else day_price >= stop_loss

                if hit_target:
                    exit_price  = target
                    exit_date   = future_date
                    exit_reason = "TARGET"
                    break
                if hit_stop:
                    exit_price  = stop_loss
                    exit_date   = future_date
                    exit_reason = "STOP"
                    break

                # Max hold: 30 days
                if (future_date - entry_date).days > 30:
                    exit_price  = day_price
                    exit_date   = future_date
                    exit_reason = "MAX_HOLD"
                    break

        if exit_price is None or exit_date is None:
            exit_price  = entry_price
            exit_date   = entry_date + pd.Timedelta(days=1)
            exit_reason = "NO_EXIT_DATA"

        # P&L calculation (direction-aware)
        if signal.direction == "BUY":
            raw_pnl = exit_price - entry_price
        else:
            raw_pnl = entry_price - exit_price

        # Subtract transaction costs
        stt     = entry_price * self.STT_RATE if signal.direction == "SELL" else 0
        costs   = self.BROKERAGE_PER_TRADE * 2 + stt   # entry + exit legs
        net_pnl = raw_pnl - costs

        pnl_pct = (net_pnl / entry_price * 100) if entry_price > 0 else 0

        return TradeResult(
            date_in     = entry_date,
            date_out    = exit_date,
            strategy    = signal.strategy,
            symbol      = signal.symbol,
            direction   = signal.direction,
            entry_price = round(entry_price, 2),
            exit_price  = round(exit_price, 2),
            exit_reason = exit_reason,
            pnl         = round(net_pnl, 2),
            pnl_pct     = round(pnl_pct, 2),
            score       = signal.score,
        )

    def _next_date(self, signal_date: pd.Timestamp) -> pd.Timestamp | None:
        """Return next available trading day after signal_date."""
        for d in self.trading_dates:
            if d > signal_date:
                return d
        return None

    def _get_price(self, dt: pd.Timestamp, signal: Signal) -> float | None:
        """
        Get settlement price for the signal's instrument on a given date.
        Uses SETTLE_PR from Bhavcopy (official closing price).
        """
        day_df = self.df[self.df["DATE"] == dt]
        if day_df.empty:
            return None

        # For options strategies: find the specific strike/type
        if "CE" in signal.symbol or "PE" in signal.symbol:
            # Parse symbol like "NIFTY 24000 CE"
            parts = signal.symbol.split()
            if len(parts) >= 3:
                sym    = parts[0]
                strike = float(parts[1])
                opt_t  = parts[2].upper()

                row = day_df[
                    (day_df["SYMBOL"].str.strip().str.upper() == sym) &
                    (day_df["STRIKE_PR"] == strike) &
                    (day_df["OPTION_TYP"].str.upper() == opt_t)
                ]
                if not row.empty:
                    p = pd.to_numeric(row["SETTLE_PR"], errors="coerce").dropna()
                    return float(p.iloc[0]) if not p.empty else None

        # For index/futures: use average settle price across all options of that symbol
        sym_name = signal.symbol.split()[0]
        row = day_df[day_df["SYMBOL"].str.strip().str.upper() == sym_name]
        if row.empty:
            return None

        p = pd.to_numeric(row["SETTLE_PR"], errors="coerce").dropna()
        return float(p.mean()) if not p.empty else None


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward split helper
# ─────────────────────────────────────────────────────────────────────────────

def in_sample_out_sample_split(
    df: pd.DataFrame,
    in_sample_ratio: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split Bhavcopy data into in-sample (training) and out-of-sample (test).
    Default: 70% in-sample, 30% out-of-sample — strict chronological split.
    """
    dates = sorted(df["DATE"].unique())
    split_idx = int(len(dates) * in_sample_ratio)
    split_date = dates[split_idx]

    in_df  = df[df["DATE"] <  split_date].copy()
    out_df = df[df["DATE"] >= split_date].copy()

    log.info(
        "Split: in-sample %s→%s (%d days) | out-of-sample %s→%s (%d days)",
        pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[split_idx - 1]).date(), split_idx,
        pd.Timestamp(split_date).date(), pd.Timestamp(dates[-1]).date(), len(dates) - split_idx,
    )
    return in_df, out_df
