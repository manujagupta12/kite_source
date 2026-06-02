"""
backtest/engine.py  v3
======================
Walk-forward backtesting engine.
Handles standard directional trades AND multi-leg options strategies
(strangles, iron condors, calendar spreads) with expiry-aware exits.

Key rules:
  - Signal on day D → enter at day D+1 (no lookahead)
  - For options sellers (ATM_Strangle, IronCondor): hold to expiry, collect theta
    Only exit early if premium hits max_loss_ratio × entry
  - For options buyers (PCR): exit at fixed target/stop
  - Transaction costs: ₹20/leg + STT
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
    BROKERAGE_PER_LEG = 20.0   # ₹ per leg per lot
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

    def _simulate(self, sig: Signal):
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
        # Held to expiry unless premium hits max_loss_ratio × entry
        max_loss_ratio = getattr(sig, "max_loss_ratio", None)

        idx = self._date_idx.get(entry_date)
        if idx is None:
            return None

        exit_price  = None
        exit_date   = None
        exit_reason = "EXPIRY"

        for fdate in self.trading_dates[idx + 1:]:
            day_price = self._price(fdate, sig)

            # ── Expiry-based exit (strangles / iron condors / calendars) ──
            if sig.expiry is not None and fdate >= sig.expiry:
                # At/past expiry: options settle near 0 → seller collects full credit
                # Use actual settlement if available, else assume near-0
                settle = day_price if day_price is not None else 0.0
                exit_price  = settle
                exit_date   = fdate
                exit_reason = "EXPIRY_SETTLE"
                break

            if day_price is None:
                # Data gap — treat as expiry at entry (flat)
                exit_price  = entry_price
                exit_date   = fdate
                exit_reason = "EXPIRY"
                break

            # ── For options sellers: only early-exit at max_loss_ratio ──────
            if max_loss_ratio is not None:
                if day_price >= entry_price * max_loss_ratio:
                    exit_price  = day_price
                    exit_date   = fdate
                    exit_reason = "STOP"
                    break
                # No target — hold to expiry
                continue

            # ── Standard directional exit ────────────────────────────────────
            if sig.direction == "BUY":
                if day_price >= target:
                    exit_price, exit_date, exit_reason = target, fdate, "TARGET"
                    break
                if day_price <= stop_loss:
                    exit_price, exit_date, exit_reason = stop_loss, fdate, "STOP"
                    break
            else:  # SELL
                if day_price <= target:
                    exit_price, exit_date, exit_reason = target, fdate, "TARGET"
                    break
                if day_price >= stop_loss:
                    exit_price, exit_date, exit_reason = stop_loss, fdate, "STOP"
                    break

            # Max hold: 30 days for non-expiry strategies
            if sig.expiry is None and (fdate - entry_date).days > 30:
                exit_price, exit_date, exit_reason = day_price, fdate, "MAX_HOLD"
                break

        if exit_price is None:
            exit_price  = entry_price
            exit_date   = entry_date + pd.Timedelta(days=1)
            exit_reason = "NO_EXIT_DATA"

        # ── P&L ──────────────────────────────────────────────────────────────
        # For SELL strangles: premium collected at entry, paid back at exit
        # P&L = entry_premium - exit_premium (profit when exit < entry)
        if sig.direction == "SELL":
            raw_pnl = entry_price - exit_price
        else:
            raw_pnl = exit_price - entry_price

        n_legs = getattr(sig, "n_legs", 2)  # legs: 2 for strangle, 4 for condor
        costs  = self.BROKERAGE_PER_LEG * n_legs
        if sig.direction == "SELL":
            costs += entry_price * self.STT_SELL_RATE

        net_pnl = raw_pnl - costs
        pnl_pct = net_pnl / entry_price * 100 if entry_price > 0 else 0

        return TradeResult(
            date_in     = entry_date,
            date_out    = exit_date if exit_date else entry_date,
            strategy    = sig.strategy,
            symbol      = sig.symbol,
            direction   = sig.direction,
            entry_price = round(entry_price, 2),
            exit_price  = round(exit_price, 2),
            exit_reason = exit_reason,
            pnl         = round(net_pnl, 2),
            pnl_pct     = round(pnl_pct, 2),
            score       = sig.score,
        )

    # ── Price lookup ──────────────────────────────────────────────────────────

    def _price(self, dt: pd.Timestamp, sig: Signal):
        """
        Price lookup dispatcher:
          'NIFTY 24000 CE+PE'  → CE_settle + PE_settle at that strike (strangle)
          'NIFTY 24000 CE'     → specific option settle
          'NIFTY 24000 CE / 24500 CE'  → iron condor net premium
          'NIFTY'              → ATM-strike settle (EMA/OI signals)
        """
        day = self.df[self.df["DATE"] == dt]
        if day.empty:
            return None

        sym = sig.symbol.split()[0].strip().upper()

        # ── Iron condor: "NIFTY IC 24500CE-24000CE+23500PE-23000PE" ──────────
        if " IC " in sig.symbol or sig.symbol.count("/") >= 3:
            return self._price_condor(day, sym, sig.symbol)

        # ── Strangle: "NIFTY 24000 CE+PE" ────────────────────────────────────
        if "CE+PE" in sig.symbol:
            parts = sig.symbol.split()
            if len(parts) < 3:
                return None
            try:
                strike = float(parts[1])
            except ValueError:
                return None
            sub = day[(day["SYMBOL"].str.strip().str.upper() == sym) &
                      (day["STRIKE_PR"] == strike)]
            ce = pd.to_numeric(sub[sub["OPTION_TYP"].str.upper() == "CE"]["SETTLE_PR"], errors="coerce").dropna()
            pe = pd.to_numeric(sub[sub["OPTION_TYP"].str.upper() == "PE"]["SETTLE_PR"], errors="coerce").dropna()
            if ce.empty or pe.empty:
                return None
            return float(ce.iloc[0]) + float(pe.iloc[0])

        # ── Specific option: "NIFTY 24000 CE" ────────────────────────────────
        if "CE" in sig.symbol or "PE" in sig.symbol:
            parts = sig.symbol.split()
            if len(parts) < 3:
                return None
            try:
                strike = float(parts[1])
            except ValueError:
                return None
            opt_t = parts[2].upper()
            sub = day[(day["SYMBOL"].str.strip().str.upper() == sym) &
                      (day["STRIKE_PR"] == strike) &
                      (day["OPTION_TYP"].str.upper() == opt_t)]
            p = pd.to_numeric(sub["SETTLE_PR"], errors="coerce").dropna()
            return float(p.iloc[0]) if not p.empty else None

        # ── Index (EMA / OI): ATM strike only ────────────────────────────────
        sub = day[day["SYMBOL"].str.strip().str.upper() == sym]
        if sub.empty:
            return None
        oi = sub.groupby("STRIKE_PR")["OPEN_INT"].sum()
        if oi.empty:
            return None
        atm = float(oi.idxmax())
        atm_sub = sub[sub["STRIKE_PR"] == atm]
        p = pd.to_numeric(atm_sub["SETTLE_PR"], errors="coerce").dropna()
        return float(p.mean()) if not p.empty else None

    def _price_condor(self, day: pd.DataFrame, sym: str, symbol: str):
        """
        Iron condor price lookup.
        Symbol format: "NIFTY IC sell_ce/buy_ce/sell_pe/buy_pe"
        e.g. "NIFTY IC 24500/25000/23500/23000"
        Returns net premium = (sell_ce + sell_pe) - (buy_ce + buy_pe)
        """
        try:
            parts = symbol.split("IC")[1].strip().split("/")
            sell_ce_k = float(parts[0])
            buy_ce_k  = float(parts[1])
            sell_pe_k = float(parts[2])
            buy_pe_k  = float(parts[3])
        except (IndexError, ValueError):
            return None

        def get_p(strike, opt_type):
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
        return (sc - bc) + (sp - bp)  # net credit received

    def _next_date(self, dt: pd.Timestamp):
        for d in self.trading_dates:
            if d > dt:
                return d
        return None


# ── Walk-forward split ────────────────────────────────────────────────────────

def in_sample_out_sample_split(df, in_sample_ratio=0.70):
    dates     = sorted(df["DATE"].dropna().unique())
    split_idx = int(len(dates) * in_sample_ratio)
    split_dt  = dates[split_idx]
    in_df     = df[df["DATE"] <  split_dt].copy()
    out_df    = df[df["DATE"] >= split_dt].copy()
    log.info("Split: in=%s→%s (%d days) | out=%s→%s (%d days)",
        pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[split_idx-1]).date(), split_idx,
        pd.Timestamp(split_dt).date(), pd.Timestamp(dates[-1]).date(), len(dates)-split_idx)
    return in_df, out_df
            return None
        return (sc - bc) + (sp - bp)

    def _next_date(self, dt: pd.Timestamp):
        for d in self.trading_dates:
            if d > dt:
                return d
        return None


# ── Walk-forward split ────────────────────────────────────────────────────────

def in_sample_out_sample_split(df, in_sample_ratio=0.70):
    dates     = sorted(df["DATE"].dropna().unique())
    split_idx = int(len(dates) * in_sample_ratio)
    split_dt  = dates[split_idx]
    in_df     = df[df["DATE"] <  split_dt].copy()
    out_df    = df[df["DATE"] >= split_dt].copy()
    log.info("Split: in=%s->%s (%d days) | out=%s->%s (%d days)",
        pd.Timestamp(dates[0]).date(), pd.Timestamp(dates[split_idx-1]).date(), split_idx,
        pd.Timestamp(split_dt).date(), pd.Timestamp(dates[-1]).date(), len(dates)-split_idx)
    return in_df, out_df
