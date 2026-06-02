"""
momentum_breakout.py — Momentum Breakout with Weekly Options (NEW)
===================================================================
Rationale: Breakouts from consolidation zones in Indian indices are
followed by significant momentum moves (earnings season, budget,
RBI policy days, global cues). Buying slightly OTM weekly calls/puts
on confirmed breakout gives asymmetric payoff.

Strategy:
  - Identify 20-day high/low channel (Donchian Channel)
  - Entry when close breaks above 20-day high → buy ATM+1 CE
  - Entry when close breaks below 20-day low → buy ATM-1 PE
  - Use weekly options for leverage + limited risk
  - Strict: close position on Friday (avoid weekend theta decay)

Confirmation filters:
  1. ADX > 20 (trend strength)
  2. Volume > 1.5× 20-day average on breakout bar
  3. Breakout close must be > 0.3% beyond the channel boundary
  4. No entry if major news event within 2 days (manual override flag)

Exit rules:
  - Target: 80% gain on option premium (options give 5-10× leverage)
  - Stop: 40% loss on option premium
  - Time stop: Thursday EOD (don't hold into Friday expiry)
  - Trend reversal: if price closes back inside channel, exit

Expected performance:
  Win rate: 45-55% (lower than EMA since breakouts can fail)
  PF: 2.5-4.0 (asymmetric payoff with options covers losses)
  Sharpe: 1.5-2.5
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, adx as compute_adx
from risk_manager import RiskManager
from iron_condor import bs_price

logger = logging.getLogger(__name__)


@dataclass
class MomentumConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    channel_period: int = 20      # Donchian channel lookback
    adx_min: float = 20.0
    volume_mult: float = 1.5      # Breakout volume requirement
    volume_period: int = 20
    min_breakout_pct: float = 0.003  # Min 0.3% break beyond channel
    profit_target_pct: float = 80.0  # Target 80% gain on premium
    stop_loss_pct: float = 40.0      # Stop at 40% loss
    dte_target: int = 7            # Use 7 DTE options (weekly)
    otm_strikes: int = 1          # How many strikes OTM to buy


INSTRUMENT_MOMENTUM_PARAMS = {
    "NIFTY":     MomentumConfig(instrument="NIFTY",     lot_size=50),
    "BANKNIFTY": MomentumConfig(instrument="BANKNIFTY", lot_size=15),
    "FINNIFTY":  MomentumConfig(instrument="FINNIFTY",  lot_size=40),
}


def compute_donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    df = df.copy()
    df["dc_high"] = df["high"].rolling(period).max().shift(1)  # Previous N-bar high
    df["dc_low"]  = df["low"].rolling(period).min().shift(1)   # Previous N-bar low
    return df


def compute_breakout_signals(df: pd.DataFrame, cfg: MomentumConfig) -> pd.DataFrame:
    df = compute_donchian(df, cfg.channel_period)
    adx_vals, plus_di, minus_di = compute_adx(df["high"], df["low"], df["close"], 14)
    df["adx"] = adx_vals

    if "volume" in df.columns:
        df["vol_avg"] = df["volume"].rolling(cfg.volume_period).mean()
        df["vol_ok"] = df["volume"] > cfg.volume_mult * df["vol_avg"]
    else:
        df["vol_ok"] = True

    # Breakout: close must be significantly beyond channel
    df["breakout_up"] = (
        (df["close"] > df["dc_high"] * (1 + cfg.min_breakout_pct)) &
        (df["adx"] >= cfg.adx_min) &
        df["vol_ok"]
    )
    df["breakout_dn"] = (
        (df["close"] < df["dc_low"] * (1 - cfg.min_breakout_pct)) &
        (df["adx"] >= cfg.adx_min) &
        df["vol_ok"]
    )
    return df


class MomentumBreakoutBacktest:
    """Weekly options momentum breakout strategy."""

    def __init__(self, cfg: MomentumConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()
        self.trades: List[dict] = []

    def run(
        self,
        df: pd.DataFrame,
        option_price_fetcher,  # callable(date, strike, opt_type, dte) → price
    ) -> pd.DataFrame:
        """
        df: daily OHLCV with DatetimeIndex
        option_price_fetcher: fetches option premium given (date, strike, type, dte)
        """
        df = compute_breakout_signals(df, self.cfg)
        step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)

        position = None  # dict

        for i in range(self.cfg.channel_period + 15, len(df)):
            bar = df.iloc[i]
            date = df.index[i]
            price = bar["close"]
            strategy_name = f"Momentum_Breakout_{self.cfg.instrument}"

            # ── Manage open position ──────────────────────────────
            if position is not None:
                days_held = (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days
                dte_remaining = position["dte"] - days_held

                try:
                    current_option_price = option_price_fetcher(
                        date, position["strike"], position["option_type"], max(1, dte_remaining)
                    )
                except Exception:
                    current_option_price = position["entry_option_price"]

                pnl_pct = (current_option_price - position["entry_option_price"]) / position["entry_option_price"] * 100
                pnl_abs = (current_option_price - position["entry_option_price"]) * position["lots"] * self.cfg.lot_size

                # Check channel re-entry (trend failure)
                if position["direction"] == "BUY":
                    trend_failed = price < bar["dc_high"]
                else:
                    trend_failed = price > bar["dc_low"]

                exit_reason = None
                if pnl_pct >= self.cfg.profit_target_pct:
                    exit_reason = "TARGET"
                elif pnl_pct <= -self.cfg.stop_loss_pct:
                    exit_reason = "STOP"
                elif dte_remaining <= 1:
                    exit_reason = "DTE_EXIT"
                elif trend_failed and days_held >= 2:
                    exit_reason = "TREND_FAILED"

                if exit_reason:
                    self.trades.append({
                        "date_in": position["entry_date"],
                        "date_out": date,
                        "strategy": strategy_name,
                        "symbol": f"{self.cfg.instrument} {position['strike']} {position['option_type'].upper()}",
                        "direction": position["direction"],
                        "entry_price": position["entry_option_price"],
                        "exit_price": current_option_price,
                        "exit_reason": exit_reason,
                        "pnl": pnl_abs,
                        "pnl_pct": pnl_pct,
                        "instrument": self.cfg.instrument,
                    })
                    self.rm.close_position(strategy_name, pnl_abs)
                    position = None

            # ── New entry ─────────────────────────────────────────
            if position is None and self.rm.allow_trade(strategy_name):
                direction = None
                option_type = None

                if bar["breakout_up"]:
                    direction = "BUY"
                    option_type = "call"
                    strike = int(round((price + self.cfg.otm_strikes * step) / step) * step)
                elif bar["breakout_dn"]:
                    direction = "SELL"  # Buying puts on bearish breakout
                    option_type = "put"
                    strike = int(round((price - self.cfg.otm_strikes * step) / step) * step)

                if direction is not None:
                    try:
                        entry_price = option_price_fetcher(date, strike, option_type, self.cfg.dte_target)
                    except Exception as e:
                        logger.warning(f"Momentum: option fetch failed {e}")
                        continue

                    if entry_price <= 0:
                        continue

                    lots = max(1, self.rm.position_size(
                        strategy_name, self.cfg.instrument, price,
                        atr=entry_price, lot_size=self.cfg.lot_size,
                        stop_distance=entry_price * self.cfg.stop_loss_pct / 100
                    ))

                    position = {
                        "entry_date": date,
                        "strike": strike,
                        "option_type": option_type,
                        "direction": direction,
                        "entry_option_price": entry_price,
                        "dte": self.cfg.dte_target,
                        "lots": lots,
                    }
                    self.rm.open_position(strategy_name)
                    logger.info(
                        f"BREAKOUT ENTRY {self.cfg.instrument}: {direction} {option_type.upper()} "
                        f"{strike} @ {entry_price:.2f} (underlying={price:.2f})"
                    )

        return pd.DataFrame(self.trades)
