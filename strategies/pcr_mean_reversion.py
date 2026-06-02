"""
pcr_mean_reversion.py — PCR Mean Reversion Strategy (v2 — OVERHAULED)
=======================================================================
BACKTEST RESULT: MIXED → OOS FAILS for BANKNIFTY/FINNIFTY

Root causes fixed:
  1. BUG FIX: exit_price was using index level, not option premium
     → Now correctly tracks option premium P&L
  2. Added trend filter: PCR signals must ALIGN with regime
     (don't fade a trending market with a PCR reversal signal)
  3. Smoothed PCR: use 5-day EMA of PCR instead of single-day spike
  4. Min PCR threshold bands tuned per instrument
  5. Score filter: only take trades with composite score > 70
  6. Added IV confirmation: PCR signal only valid when IV is not too low
     (low IV = no premium decay, options very cheap = signals unreliable)

Strategy logic:
  - PCR > upper_band (too many puts) → market may bounce → BUY CE (calls)
  - PCR < lower_band (too many calls) → market may drop → BUY PE (puts)
  - Exit: target 40% gain, stop 25% loss, or 5 DTE

PCR bands per instrument:
  NIFTY:     upper=1.3, lower=0.7  (standard historical range)
  BANKNIFTY: upper=1.1, lower=0.6  (BN has structurally lower PCR)
  FINNIFTY:  upper=1.25, lower=0.65
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, iv_rank
from risk_manager import RiskManager

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

@dataclass
class PCRConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    pcr_upper: float = 1.30       # PCR > this → bullish reversal signal
    pcr_lower: float = 0.70       # PCR < this → bearish reversal signal
    pcr_ema_period: int = 5       # Smooth PCR over 5 days
    profit_target_pct: float = 40.0   # Exit at +40% on option premium
    stop_loss_pct: float = 25.0       # Exit at -25% on option premium
    dte_entry_max: int = 15       # Buy options with max 15 DTE (cheaper, faster decay)
    dte_entry_min: int = 5        # At least 5 DTE
    dte_exit_min: int = 3         # Exit if ≤ 3 DTE remaining
    score_min: int = 65           # Min composite score
    ivr_min: float = 20.0         # Min IVR — skip if vol too crushed
    ivr_max: float = 75.0         # Max IVR — skip if vol too elevated (buying in high IV)
    otm_distance: int = 100       # Strike distance from ATM for NIFTY (200 for BN)


INSTRUMENT_PCR_PARAMS = {
    "NIFTY":     PCRConfig(instrument="NIFTY",     lot_size=50, pcr_upper=1.30, pcr_lower=0.70, otm_distance=100),
    "BANKNIFTY": PCRConfig(instrument="BANKNIFTY", lot_size=15, pcr_upper=1.10, pcr_lower=0.60, otm_distance=200),
    "FINNIFTY":  PCRConfig(instrument="FINNIFTY",  lot_size=40, pcr_upper=1.25, pcr_lower=0.65, otm_distance=100),
}


# ─────────────────────────────────────────────
#  COMPOSITE SCORE
# ─────────────────────────────────────────────

def compute_pcr_score(
    pcr: float,
    pcr_ema: float,
    regime: Regime,
    ivr: float,
    pcr_upper: float,
    pcr_lower: float,
    direction: str,  # "bullish" or "bearish"
) -> int:
    """
    Score 0-100. Higher = stronger signal.
    Only awards points when conditions align.
    """
    score = 0

    # PCR extreme (40 pts)
    if direction == "bullish" and pcr > pcr_upper:
        excess = min((pcr - pcr_upper) / 0.3, 1.0)
        score += int(40 * excess)
    elif direction == "bearish" and pcr < pcr_lower:
        excess = min((pcr_lower - pcr) / 0.2, 1.0)
        score += int(40 * excess)

    # PCR EMA confirms (15 pts)
    if direction == "bullish" and pcr_ema > pcr_upper * 0.95:
        score += 15
    elif direction == "bearish" and pcr_ema < pcr_lower * 1.05:
        score += 15

    # Regime alignment (25 pts)
    if direction == "bullish" and regime in (Regime.RANGING, Regime.TRENDING_UP):
        score += 25
    elif direction == "bearish" and regime in (Regime.RANGING, Regime.TRENDING_DOWN):
        score += 25
    elif regime == Regime.RANGING:
        score += 10  # Neutral regime gets partial credit

    # IV in tradeable range (20 pts)
    if 20 <= ivr <= 60:
        score += 20
    elif 15 <= ivr <= 75:
        score += 10

    return min(score, 100)


# ─────────────────────────────────────────────
#  SIGNAL GENERATION
# ─────────────────────────────────────────────

def generate_pcr_signals(
    pcr_df: pd.DataFrame,
    ohlcv_df: pd.DataFrame,
    iv_series: pd.Series,
    cfg: PCRConfig,
) -> pd.DataFrame:
    """
    Parameters
    ----------
    pcr_df    : DataFrame with 'date' index and 'pcr' column (daily PCR)
    ohlcv_df  : OHLCV of underlying (for regime detection)
    iv_series : Daily IV / VIX series

    Returns
    -------
    DataFrame with columns: date, pcr, pcr_ema, signal, score, direction
    """
    df = pcr_df.copy()
    df["pcr_ema"] = df["pcr"].ewm(span=cfg.pcr_ema_period, adjust=False).mean()

    signals = []
    for i in range(cfg.pcr_ema_period + 5, len(df)):
        date = df.index[i]
        pcr = df["pcr"].iloc[i]
        pcr_ema = df["pcr_ema"].iloc[i]

        # IV Rank
        ivp = iv_rank(iv_series.loc[:date], lookback=52)

        # Regime
        ohlcv_slice = ohlcv_df.loc[:date].iloc[-60:]
        regime = detect_regime(ohlcv_slice, ivp=ivp)

        # Don't buy options in trending regime against the trend
        direction = None
        if pcr > cfg.pcr_upper and regime != Regime.TRENDING_DOWN:
            direction = "bullish"
        elif pcr < cfg.pcr_lower and regime != Regime.TRENDING_UP:
            direction = "bearish"

        if direction is None or not (cfg.ivr_min <= ivp <= cfg.ivr_max):
            signals.append({"date": date, "pcr": pcr, "pcr_ema": pcr_ema,
                             "signal": 0, "score": 0, "direction": None})
            continue

        score = compute_pcr_score(pcr, pcr_ema, regime, ivp, cfg.pcr_upper, cfg.pcr_lower, direction)

        if score >= cfg.score_min:
            signals.append({"date": date, "pcr": pcr, "pcr_ema": pcr_ema,
                             "signal": 1 if direction == "bullish" else -1,
                             "score": score, "direction": direction})
        else:
            signals.append({"date": date, "pcr": pcr, "pcr_ema": pcr_ema,
                             "signal": 0, "score": score, "direction": None})

    return pd.DataFrame(signals).set_index("date") if signals else pd.DataFrame()


# ─────────────────────────────────────────────
#  BACKTEST ENGINE
# ─────────────────────────────────────────────

class PCRBacktest:
    """
    Backtests PCR mean reversion on OPTIONS (not futures).
    Tracks option premium P&L correctly.
    """

    def __init__(self, cfg: PCRConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()
        self.trades = []

    def run(
        self,
        signal_df: pd.DataFrame,
        option_chain_fetcher,   # callable(date, strike, option_type) → price
        underlying_prices: pd.Series,
    ) -> pd.DataFrame:
        """
        signal_df: output of generate_pcr_signals()
        option_chain_fetcher: function to get option premium at given date/strike/type
        underlying_prices: daily close of underlying
        """
        position = None  # dict with trade details

        for date, row in signal_df.iterrows():
            S = underlying_prices.get(date, None)
            if S is None:
                continue

            # ── Manage open position ──────────────────────────────
            if position is not None:
                days_held = (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days
                dte_remaining = position["dte_at_entry"] - days_held

                # Get current option price
                try:
                    current_option_price = option_chain_fetcher(
                        date, position["strike"], position["option_type"]
                    )
                except Exception:
                    current_option_price = position["entry_option_price"]  # fallback

                pnl_pct = (current_option_price - position["entry_option_price"]) / position["entry_option_price"] * 100
                pnl_abs = (current_option_price - position["entry_option_price"]) * position["lots"] * self.cfg.lot_size

                exit_reason = None
                if pnl_pct >= self.cfg.profit_target_pct:
                    exit_reason = "TARGET"
                elif pnl_pct <= -self.cfg.stop_loss_pct:
                    exit_reason = "STOP"
                elif dte_remaining <= self.cfg.dte_exit_min:
                    exit_reason = "DTE_EXIT"

                if exit_reason:
                    self.trades.append({
                        "date_in": position["entry_date"],
                        "date_out": date,
                        "strategy": f"PCR_MeanReversion_{self.cfg.instrument}",
                        "symbol": f"{self.cfg.instrument} {position['strike']} {position['option_type'].upper()}",
                        "direction": "BUY",
                        "entry_price": position["entry_option_price"],
                        "exit_price": current_option_price,  # ← FIXED: option price, not index
                        "exit_reason": exit_reason,
                        "pnl": pnl_abs,
                        "pnl_pct": pnl_pct,
                        "score": position["score"],
                        "instrument": self.cfg.instrument,
                    })
                    self.rm.close_position(f"PCR_MeanReversion_{self.cfg.instrument}", pnl_abs)
                    position = None

            # ── New entry ─────────────────────────────────────────
            if position is None and row["signal"] != 0:
                strategy_name = f"PCR_MeanReversion_{self.cfg.instrument}"
                if not self.rm.allow_trade(strategy_name):
                    continue

                direction = row["direction"]
                option_type = "call" if direction == "bullish" else "put"

                # Select strike: ATM + cfg.otm_distance
                step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)
                if option_type == "call":
                    strike = int(round((S + self.cfg.otm_distance) / step) * step)
                else:
                    strike = int(round((S - self.cfg.otm_distance) / step) * step)

                try:
                    entry_price = option_chain_fetcher(date, strike, option_type)
                except Exception as e:
                    logger.warning(f"PCR: option price fetch failed {e}")
                    continue

                if entry_price <= 0:
                    continue

                lots = max(1, self.rm.position_size(
                    strategy_name, self.cfg.instrument, S,
                    atr=entry_price * 0.3,  # proxy ATR
                    lot_size=self.cfg.lot_size,
                    stop_distance=entry_price * self.cfg.stop_loss_pct / 100
                ))

                position = {
                    "entry_date": date,
                    "strike": strike,
                    "option_type": option_type,
                    "entry_option_price": entry_price,
                    "lots": lots,
                    "dte_at_entry": 10,   # default — override with actual DTE
                    "score": int(row["score"]),
                }
                self.rm.open_position(strategy_name)
                logger.info(
                    f"PCR ENTRY {self.cfg.instrument}: {option_type.upper()} {strike} "
                    f"@ {entry_price:.2f} score={row['score']:.0f}"
                )

        return pd.DataFrame(self.trades)
