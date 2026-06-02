"""
backtest/engine.py  v3
======================
Walk-forward backtesting engine.
Handles standard directional trades AND multi-leg options strategies
(strangles, iron condors, calendar spreads) with expiry-aware exits.

Key rules:
  - Signal on day D → enter at day D+1 (no lookahead bias)
  - Options sellers (ATM_Strangle, IronCondor, CalendarSpread):
      hold to expiry; only exit early if premium hits max_loss_ratio × entry
  - Options buyers (PCR, EMA, OI): exit at fixed target/stop
  - Transaction cost: ₹20/leg + 0.05% STT on sell-side premium
"""

from __future__ import annotations

import logging
from typing import List, Optional

import numpy as np
import pandas as pd

from strategies import Signal
from metrics import TradeResult

log = logging.getLogger(__name__)


class BacktestEngine:
    BROKERAGE_PER_LEG = 20.0    # ₹ per leg per lot
    STT_SELL_RATE     = 0.0005  # 0.05% on premium for sell-side

    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        for col in ["DATE", "EXPIRY_DT"]:
            if col in self.df.columns:
                self.df[col] = pd.to_datetime(self.df[col], errors="coerce")
        for col in ["STRIKE_PR", "SETTLE_PR", "OPEN_INT"]:
            if col in self.df.columns:
                self.df[col] = pd.to_numeric(self.df[col], errors="coerce")

        self.trading_dates = sorted(self.df["DATE"].dropna().unique())
        self._date_idx     = {d: i for i, d in enumerate(self.trading_dates)}

    # ── Public ────────────────────────────────────────────────────────────────

    def run(self, signals: List[Signal]) -> List[TradeResult]:
        if not signals:
            return []
        results = [r for s in sorted(signals, key=lambda x: x.date)
                   if (r := self._simulate(s)) is not None]
        log.info("Engine: %d signals → %d trades", len(signals), len(results))
        return results

    # ── Core simulation ───────────────────────────────────────────────────────

    def _simulate(self, sig: Signal) -> Optional[TradeResult]:
        entry_date = self._next_date(sig.date)
        if entry_date is None:
            return None

        entry_price = self._price(entry_date, sig)
        if entry_price is None or entry_price <= 0:
            return None

        # Rescale target/stop to actual entry (not signal estimate)
        ratio     = entry_price / sig.entry_price if sig.entry_price > 0 else 1.0
        target    = sig.target_price * ratio
        stop_loss = sig.stop_loss   * ratio

        # For option sellers: max_loss_ratio overrides the stop level
        max_loss_ratio = getattr(sig, "max_loss_ratio", None)

        idx = self._date_idx.get(entry_date)
        if idx is None:
            return None

        exit_price  = None
        exit_date   = None
        exit_reason = "EXPIRY"

        for fdate in self.trading_dates[idx + 1:]:
            day_price = self._price(fdate, sig)

            # ── Expiry-based exit (strangles / iron condors / calendars) ──────
            if sig.expiry is not None and fdate >= sig.expiry:
                # At/past expiry: options settle near 0 → seller collects full credit
                settle      = day_price if day_price is not None else 0.0
                exit_price  = settle
                exit_date   = fdate
                exit_reason = "EXPIRY_SETTLE"
                break

            if day_price is None:
                # Data gap — treat as flat exit
                exit_price  = entry_price
                exit_date   = fdate
                exit_reason = "EXPIRY"
                break

            # ── Option sellers: only early-exit at max_loss_ratio ─────────────
            if max_loss_ratio is not None:
                if day_price >= entry_price * max_loss_ratio:
                    exit_price  = day_price
                    exit_date   = fdate
                    exit_reason = "STOP"
                    break
                # Otherwise: hold to expiry — no target exit for sellers
                continue

            # ── Directional buyers/sellers: target / stop ─────────────────────
            if sig.direction == "BUY":
                if day_price >= target:
                    exit_price, exit_date, exit_reason = day_price, fdate, "TARGET"
                    break
                if day_price <= stop_loss:
                    exit_price, exit_date, exit_reason = day_price, fdate, "STOP"
                    break
            else:  # SELL
                if day_price <= target:
                    exit_price, exit_date, exit_reason = day_price, fdate, "TARGET"
                    break
                if day_price >= stop_loss:
                    exit_price, exit_date, exit_reason = day_price, fdate, "STOP"
                    break

        # If never exited: exit at last available price
        if exit_price is None:
            last_date = self.trading_dates[-1]
            exit_price = self._price(last_date, sig) or entry_price
            exit_date  = last_date
            exit_reason = "EXPIRY"

        # ── P&L calculation ───────────────────────────────────────────────────
        n_legs = getattr(sig, "n_legs", 1)
        transaction_cost = self.BROKERAGE_PER_LEG * n_legs * 2  # entry + exit

        if sig.direction == "SELL":
            # Seller collects entry premium, pays exit premium
            pnl_raw  = entry_price - exit_price
            stt      = self.STT_SELL_RATE * entry_price  # STT on sell at entry
        else:
            pnl_raw  = exit_price - entry_price
            stt      = self.STT_SELL_RATE * exit_price   # STT on sell at exit

        pnl     = pnl_raw - (transaction_cost / max(sig.lot_size, 1)) - stt
        pnl_pct = (pnl / entry_price * 100) if entry_price > 0 else 0.0

        return TradeResult(
            date_in     = pd.Timestamp(entry_date),
            date_out    = pd.Timestamp(exit_date),
            strategy    = sig.strategy,
            symbol      = sig.symbol,
            direction   = sig.direction,
            entry_price = round(entry_price, 2),
            exit_price  = round(exit_price,  2),
            exit_reason = exit_reason,
            pnl         = round(pnl, 2),
            pnl_pct     = round(pnl_pct, 2),
            score       = sig.score,
        )

    # ── Price lookup ──────────────────────────────────────────────────────────

    def _price(self, dt: pd.Timestamp, sig: Signal) -> Optional[float]:
        day = self.df[self.df["DATE"] == dt]
        if day.empty:
            return None

        sym = sig.symbol.split()[0].strip().upper()

        # Iron Condor: "NIFTY IC sell_ce/buy_ce/sell_pe/buy_pe"
        if " IC " in sig.symbol:
            return self._price_condor(day, sym, sig.symbol)

        # Strangle: "NIFTY 24000 CE+PE"
        if "CE+PE" in sig.symbol:
            parts = sig.symbol.split()
            if len(parts) >= 3:
                try:
                    strike = float(parts[1])
                except ValueError:
                    return None
                sub = day[(day["SYMBOL"].str.strip().str.upper() == sym) &
                          (day["STRIKE_PR"] == strike)]
                ce_p = pd.to_numeric(
                    sub[sub["OPTION_TYP"].str.upper() == "CE"]["SETTLE_PR"],
                    errors="coerce").dropna()
                pe_p = pd.to_numeric(
                    sub[sub["OPTION_TYP"].str.upper() == "PE"]["SETTLE_PR"],
                    errors="coerce").dropna()
                if not ce_p.empty and not pe_p.empty:
                    return float(ce_p.iloc[0]) + float(pe_p.iloc[0])
            return None

        # Specific option: "NIFTY 24000 CE"
        if "CE" in sig.symbol or "PE" in sig.symbol:
            parts = sig.symbol.split()
            if len(parts) >= 3:
                try:
                    strike = float(parts[1])
                except ValueError:
                    return None
                opt_t = parts[2].upper()
                row   = day[(day["SYMBOL"].str.strip().str.upper() == sym) &
                            (day["STRIKE_PR"] == strike) &
                            (day["OPTION_TYP"].str.upper() == opt_t)]
                if not row.empty:
                    p = pd.to_numeric(row["SETTLE_PR"], errors="coerce").dropna()
                    return float(p.iloc[0]) if not p.empty else None
            return None

        # Index (EMA / OI): use ATM strike settle price (CE+PE average)
        sub = day[day["SYMBOL"].str.strip().str.upper() == sym]
        if sub.empty:
            return None
        oi = sub.groupby("STRIKE_PR")["OPEN_INT"].sum()
        if oi.empty:
            return None
        atm     = float(oi.idxmax())
        atm_sub = sub[sub["STRIKE_PR"] == atm]
        p       = pd.to_numeric(atm_sub["SETTLE_PR"], errors="coerce").dropna()
        return float(p.mean()) if not p.empty else None

    def _price_condor(self, day: pd.DataFrame, sym: str, symbol: str) -> Optional[float]:
        """
        Iron condor price lookup.
        Symbol: "NIFTY IC sell_ce/buy_ce/sell_pe/buy_pe"
        Returns current net debit (negative of current P&L for seller).
        """
        try:
            parts     = symbol.split("IC")[1].strip().split("/")
            sell_ce_k = float(parts[0])
            buy_ce_k  = float(parts[1])
            sell_pe_k = float(parts[2])
            buy_pe_k  = float(parts[3])
        except (IndexError, ValueError):
            return None

        def get_p(strike: float, opt_type: str) -> Optional[float]:
            sub = day[(day["SYMBOL"].str.strip().str.upper() == sym) &
                      (day["STRIKE_PR"] == strike) &
                      (day["OPTION_TYP"].str.upper() == opt_type)]
            p = pd.to_numeric(sub["SETTLE_PR"], errors="coerce").dropna()
            return float(p.iloc[0]) if not p.empty else None

        sc = get_p(sell_ce_k, "CE")
        bc = get_p(buy_ce_k,  "CE")
        sp = get_p(sell_pe_k, "PE")
        bp = get_p(buy_pe_k,  "PE")

        if any(x is None for x in [sc, bc, sp, bp]):
            return None
        return (sc - bc) + (sp - bp)  # current net credit value (decreases as options decay)

    def _next_date(self, dt: pd.Timestamp) -> Optional[pd.Timestamp]:
        for d in self.trading_dates:
            if d > dt:
                return d
        return None


# ── Walk-forward split ─────────────────────────────────────────────────────────

def in_sample_out_sample_split(
    df: pd.DataFrame,
    in_sample_ratio: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Split Bhavcopy data 70% in-sample / 30% out-of-sample.
    Strict chronological — no data leakage.
    """
    dates     = sorted(df["DATE"].dropna().unique())
    split_idx = int(len(dates) * in_sample_ratio)
    split_dt  = dates[split_idx]
    in_df     = df[df["DATE"] <  split_dt].copy()
    out_df    = df[df["DATE"] >= split_dt].copy()
    log.info(
        "Split: in=%s→%s (%d days) | out=%s→%s (%d days)",
        pd.Timestamp(dates[0]).date(),
        pd.Timestamp(dates[split_idx - 1]).date(), split_idx,
        pd.Timestamp(split_dt).date(),
        pd.Timestamp(dates[-1]).date(), len(dates) - split_idx,
    )
    return in_df, out_df
