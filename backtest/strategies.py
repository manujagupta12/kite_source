"""
backtest/strategies.py
======================
Reconstructed signal logic from kite_source/algo/multistrategy.py and pcr_strategy.py.
All signals are derived from real Bhavcopy data — no random values.

Strategies implemented:
  1. PCR Mean-Reversion (pcr_strategy.py)
  2. ATM Strangle (multistrategy.py — "All 7 Strategies" core)
  3. EMA Crossover on Futures (equity momentum)
  4. Calendar Spread (Calendaralgofinal.py pattern)
  5. OI-Based Directional (open interest delta signal)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal

# ─────────────────────────────────────────────────────────────────────────────
# Signal dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    date: pd.Timestamp
    strategy: str
    symbol: str
    direction: Literal["BUY", "SELL", "NEUTRAL"]
    entry_price: float
    target_price: float
    stop_loss: float
    score: float          # 0–100, mirrors dashboard signal score
    lot_size: int = 1
    notes: str = ""

    @property
    def risk_reward(self) -> float:
        if self.direction == "BUY":
            risk   = self.entry_price - self.stop_loss
            reward = self.target_price - self.entry_price
        else:
            risk   = self.stop_loss - self.entry_price
            reward = self.entry_price - self.target_price
        return round(reward / risk, 2) if risk > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["HIGH"] - df["LOW"]
    hc = (df["HIGH"] - df["CLOSE"].shift()).abs()
    lc = (df["LOW"]  - df["CLOSE"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _nearest_strike(spot: float, step: int = 50) -> int:
    return int(round(spot / step) * step)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 1 — PCR Mean-Reversion (mirrors pcr_strategy.py)
# ─────────────────────────────────────────────────────────────────────────────

class PCRMeanReversion:
    """
    Entry logic (from pcr_strategy.py):
      - Calculate daily PCR = total PE OI / total CE OI for near-expiry options
      - PCR > 1.4  → market oversold → BUY signal (CE)
      - PCR < 0.7  → market overbought → SELL signal (PE)
      - 0.7–1.4    → NEUTRAL

    Exit logic:
      - Target: PCR reverts to 1.0 band (0.9–1.1)
      - Stop:   5% adverse move on option premium
    """

    name = "PCR_MeanReversion"
    PCR_SELL_THRESHOLD = 0.70
    PCR_BUY_THRESHOLD  = 1.40
    SCORE_BASE         = 60

    def generate(self, df_options: pd.DataFrame) -> list[Signal]:
        signals = []

        # Compute daily PCR for nearest expiry
        df = df_options.copy()
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["DATE"]      = pd.to_datetime(df["DATE"])

        for dt, day_df in df.groupby("DATE"):
            # Select nearest expiry
            expiries = day_df["EXPIRY_DT"].dropna().unique()
            if len(expiries) == 0:
                continue
            near_expiry = min(expiries, key=lambda e: abs((e - dt).days) if (e - dt).days >= 0 else 9999)
            near_df = day_df[day_df["EXPIRY_DT"] == near_expiry]

            pe_oi = near_df[near_df["OPTION_TYP"].str.upper() == "PE"]["OPEN_INT"].sum()
            ce_oi = near_df[near_df["OPTION_TYP"].str.upper() == "CE"]["OPEN_INT"].sum()

            if ce_oi == 0:
                continue

            pcr = pe_oi / ce_oi

            # Spot price proxy: nearest ATM strike CLOSE
            atm_strikes = near_df["STRIKE_PR"].dropna().astype(float)
            if atm_strikes.empty:
                continue

            spot_proxy = near_df["SETTLE_PR"].dropna().mean()
            if spot_proxy <= 0:
                continue

            atm = _nearest_strike(spot_proxy)

            if pcr > self.PCR_BUY_THRESHOLD:
                direction = "BUY"
                # Buy ATM Call
                ce_row = near_df[
                    (near_df["OPTION_TYP"].str.upper() == "CE") &
                    (near_df["STRIKE_PR"].astype(float) == atm)
                ]
                if ce_row.empty:
                    continue
                entry = float(ce_row["SETTLE_PR"].iloc[0])
                score = min(95, self.SCORE_BASE + int((pcr - self.PCR_BUY_THRESHOLD) * 50))

            elif pcr < self.PCR_SELL_THRESHOLD:
                direction = "SELL"
                # Buy ATM Put (bearish)
                pe_row = near_df[
                    (near_df["OPTION_TYP"].str.upper() == "PE") &
                    (near_df["STRIKE_PR"].astype(float) == atm)
                ]
                if pe_row.empty:
                    continue
                entry = float(pe_row["SETTLE_PR"].iloc[0])
                score = min(95, self.SCORE_BASE + int((self.PCR_SELL_THRESHOLD - pcr) * 100))

            else:
                continue

            target    = entry * 1.25   # 25% gain on premium
            stop_loss = entry * 0.60   # 40% max loss on premium

            signals.append(Signal(
                date        = pd.Timestamp(dt),
                strategy    = self.name,
                symbol      = "NIFTY",
                direction   = direction,
                entry_price = round(entry, 2),
                target_price= round(target, 2),
                stop_loss   = round(stop_loss, 2),
                score       = score,
                notes       = f"PCR={pcr:.3f} ATM={atm}",
            ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 2 — ATM Strangle (multistrategy.py core pattern)
# ─────────────────────────────────────────────────────────────────────────────

class ATMStrangle:
    """
    Entry logic (from multistrategy.py read_main_table + detect_atm):
      - Sell ATM Call + Sell ATM Put on weekly expiry
      - Entry when IV (approx via CLOSE vs SETTLE spread) is elevated
      - Net credit = CE premium + PE premium

    Exit logic:
      - Target: 50% of net credit collected
      - Stop:   Net debit exceeds 2× net credit (200% loss)
      - Forced exit: 1 day before expiry
    """

    name = "ATM_Strangle"
    MIN_PREMIUM = 30   # minimum premium to justify selling

    def generate(self, df_options: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df_options.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for dt, day_df in df.groupby("DATE"):
            # Use nearest weekly expiry
            expiries = day_df["EXPIRY_DT"].dropna().unique()
            if len(expiries) == 0:
                continue

            future_expiries = [e for e in expiries if (e - dt).days >= 1]
            if not future_expiries:
                continue
            near_expiry = min(future_expiries, key=lambda e: (e - dt).days)

            near_df = day_df[day_df["EXPIRY_DT"] == near_expiry]
            days_to_expiry = (near_expiry - dt).days

            # Skip if too close to expiry
            if days_to_expiry < 2:
                continue

            # Get ATM from futures settle price as spot proxy
            settle_prices = near_df["SETTLE_PR"].dropna()
            if settle_prices.empty:
                continue

            # ATM = most actively traded strike
            oi_by_strike = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
            if oi_by_strike.empty:
                continue
            atm = float(oi_by_strike.idxmax())

            # Get ATM CE and PE premiums
            ce_rows = near_df[
                (near_df["OPTION_TYP"].str.upper() == "CE") &
                (near_df["STRIKE_PR"] == atm)
            ]
            pe_rows = near_df[
                (near_df["OPTION_TYP"].str.upper() == "PE") &
                (near_df["STRIKE_PR"] == atm)
            ]

            if ce_rows.empty or pe_rows.empty:
                continue

            ce_prem = float(ce_rows["SETTLE_PR"].iloc[0])
            pe_prem = float(pe_rows["SETTLE_PR"].iloc[0])

            if ce_prem < self.MIN_PREMIUM or pe_prem < self.MIN_PREMIUM:
                continue

            net_credit = ce_prem + pe_prem
            target     = net_credit * 0.50   # collect 50% of premium
            stop_loss  = net_credit * 2.00   # 2× premium as max loss

            # Score based on premium richness and DTE
            score = min(90, 50 + int(net_credit / 20) + max(0, 5 - days_to_expiry) * 3)

            signals.append(Signal(
                date        = pd.Timestamp(dt),
                strategy    = self.name,
                symbol      = f"NIFTY {int(atm)} CE+PE",
                direction   = "SELL",   # selling strangle = short vol
                entry_price = round(net_credit, 2),
                target_price= round(target, 2),
                stop_loss   = round(stop_loss, 2),
                score       = score,
                notes       = f"CE={ce_prem:.0f} PE={pe_prem:.0f} DTE={days_to_expiry}",
            ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 3 — EMA Crossover on Futures (equity momentum)
# ─────────────────────────────────────────────────────────────────────────────

class EMACrossover:
    """
    Entry logic:
      - Fast EMA (9) crosses above Slow EMA (21) → BUY
      - Fast EMA (9) crosses below Slow EMA (21) → SELL
      - RSI confirmation: BUY only when RSI > 50, SELL only when RSI < 50

    Exit logic:
      - Target: 2× ATR from entry
      - Stop:   1× ATR from entry

    Applies to: NIFTY and BANKNIFTY continuous futures (nearest expiry)
    """

    name = "EMA_Crossover"
    FAST = 9
    SLOW = 21

    def generate(self, df_options: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df_options.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])

        for symbol in ["NIFTY", "BANKNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol].copy()
            if sym_df.empty:
                continue

            # Build daily OHLC from nearest expiry settle prices
            # Aggregate by date: use nearest-expiry contracts
            daily = []
            for dt, day_df in sym_df.groupby("DATE"):
                expiries = day_df["EXPIRY_DT"].dropna().unique()
                if len(expiries) == 0:
                    continue
                future_exp = [e for e in expiries if (e - dt).days >= 0]
                if not future_exp:
                    continue
                near_exp = min(future_exp, key=lambda e: (e - dt).days)
                near = day_df[day_df["EXPIRY_DT"] == near_exp]
                daily.append({
                    "DATE":  pd.Timestamp(dt),
                    "OPEN":  pd.to_numeric(near["OPEN"],  errors="coerce").mean(),
                    "HIGH":  pd.to_numeric(near["HIGH"],  errors="coerce").max(),
                    "LOW":   pd.to_numeric(near["LOW"],   errors="coerce").min(),
                    "CLOSE": pd.to_numeric(near["SETTLE_PR"], errors="coerce").mean(),
                })

            if len(daily) < self.SLOW + 5:
                continue

            ohlc = pd.DataFrame(daily).set_index("DATE").sort_index()
            ohlc.dropna(subset=["CLOSE"], inplace=True)

            ohlc["EMA_FAST"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_SLOW"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]      = _rsi(ohlc["CLOSE"])
            ohlc["ATR"]      = _atr(ohlc)

            prev = ohlc.shift(1)

            buy_cross  = (ohlc["EMA_FAST"] > ohlc["EMA_SLOW"]) & (prev["EMA_FAST"] <= prev["EMA_SLOW"]) & (ohlc["RSI"] > 50)
            sell_cross = (ohlc["EMA_FAST"] < ohlc["EMA_SLOW"]) & (prev["EMA_FAST"] >= prev["EMA_SLOW"]) & (ohlc["RSI"] < 50)

            for dt_idx in ohlc.index[buy_cross]:
                row = ohlc.loc[dt_idx]
                atr = row["ATR"] if not np.isnan(row["ATR"]) else row["CLOSE"] * 0.01
                signals.append(Signal(
                    date        = dt_idx,
                    strategy    = self.name,
                    symbol      = symbol,
                    direction   = "BUY",
                    entry_price = round(row["CLOSE"], 2),
                    target_price= round(row["CLOSE"] + 2 * atr, 2),
                    stop_loss   = round(row["CLOSE"] - 1 * atr, 2),
                    score       = min(90, 60 + int(row["RSI"] - 50)),
                    notes       = f"EMA9={row['EMA_FAST']:.0f} EMA21={row['EMA_SLOW']:.0f} RSI={row['RSI']:.1f}",
                ))

            for dt_idx in ohlc.index[sell_cross]:
                row = ohlc.loc[dt_idx]
                atr = row["ATR"] if not np.isnan(row["ATR"]) else row["CLOSE"] * 0.01
                signals.append(Signal(
                    date        = dt_idx,
                    strategy    = self.name,
                    symbol      = symbol,
                    direction   = "SELL",
                    entry_price = round(row["CLOSE"], 2),
                    target_price= round(row["CLOSE"] - 2 * atr, 2),
                    stop_loss   = round(row["CLOSE"] + 1 * atr, 2),
                    score       = min(90, 60 + int(50 - row["RSI"])),
                    notes       = f"EMA9={row['EMA_FAST']:.0f} EMA21={row['EMA_SLOW']:.0f} RSI={row['RSI']:.1f}",
                ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 4 — OI Build-up Directional
# ─────────────────────────────────────────────────────────────────────────────

class OIBuildup:
    """
    Entry logic:
      - If PE OI build-up (CHG_IN_OI > 0 for PE) at strike > ATM → strong support → BUY
      - If CE OI build-up at strike < ATM → strong resistance → SELL
      - OI change must be > 1σ of rolling 10-day OI changes

    Exit logic:
      - Target: 1.5% spot move
      - Stop:   0.7% adverse spot move
    """

    name = "OI_Buildup"

    def generate(self, df_options: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df_options.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["CHG_IN_OI"] = pd.to_numeric(df["CHG_IN_OI"], errors="coerce").fillna(0)
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"],  errors="coerce").fillna(0)
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        # Rolling σ of total OI changes
        daily_oi = df.groupby("DATE")["CHG_IN_OI"].sum().sort_index()
        oi_std   = daily_oi.rolling(10).std()

        for dt, day_df in df.groupby("DATE"):
            sigma = oi_std.get(dt, None)
            if sigma is None or np.isnan(sigma) or sigma == 0:
                continue

            # Proxy ATM from highest total OI strike
            oi_by_strike = day_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
            if oi_by_strike.empty:
                continue
            atm = float(oi_by_strike.idxmax())

            # PE OI build above ATM → support → bullish
            pe_above = day_df[
                (day_df["OPTION_TYP"].str.upper() == "PE") &
                (day_df["STRIKE_PR"] > atm) &
                (day_df["CHG_IN_OI"] > sigma)
            ]

            # CE OI build below ATM → resistance → bearish
            ce_below = day_df[
                (day_df["OPTION_TYP"].str.upper() == "CE") &
                (day_df["STRIKE_PR"] < atm) &
                (day_df["CHG_IN_OI"] > sigma)
            ]

            spot_settle = day_df["SETTLE_PR"].dropna().mean()
            if spot_settle <= 0:
                continue

            if not pe_above.empty:
                score = min(88, 55 + int(pe_above["CHG_IN_OI"].sum() / sigma * 5))
                signals.append(Signal(
                    date        = pd.Timestamp(dt),
                    strategy    = self.name,
                    symbol      = "NIFTY",
                    direction   = "BUY",
                    entry_price = round(spot_settle, 2),
                    target_price= round(spot_settle * 1.015, 2),
                    stop_loss   = round(spot_settle * 0.993, 2),
                    score       = score,
                    notes       = f"PE OI build at strike>{atm:.0f} σ={sigma:.0f}",
                ))

            if not ce_below.empty:
                score = min(88, 55 + int(ce_below["CHG_IN_OI"].sum() / sigma * 5))
                signals.append(Signal(
                    date        = pd.Timestamp(dt),
                    strategy    = self.name,
                    symbol      = "NIFTY",
                    direction   = "SELL",
                    entry_price = round(spot_settle, 2),
                    target_price= round(spot_settle * 0.985, 2),
                    stop_loss   = round(spot_settle * 1.007, 2),
                    score       = score,
                    notes       = f"CE OI build at strike<{atm:.0f} σ={sigma:.0f}",
                ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Strategy 5 — Calendar Spread (Calendaralgofinal.py pattern)
# ─────────────────────────────────────────────────────────────────────────────

class CalendarSpread:
    """
    Entry logic (Calendar Spread):
      - Sell near-expiry ATM option, Buy far-expiry ATM option
      - Same strike, same type (CE or PE depending on bias)
      - Enter when near-month IV > far-month IV (IV skew > threshold)

    Exit logic:
      - Target: near-month premium decays to 40% of entry
      - Stop:   loss exceeds 1.5× net credit
    """

    name = "Calendar_Spread"
    IV_SKEW_THRESHOLD = 0.10   # 10% difference triggers entry

    def generate(self, df_options: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df_options.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for dt, day_df in df.groupby("DATE"):
            expiries = sorted(day_df["EXPIRY_DT"].dropna().unique())
            future_exp = [e for e in expiries if (e - dt).days >= 1]
            if len(future_exp) < 2:
                continue

            near_exp = future_exp[0]
            far_exp  = future_exp[1]

            near_df = day_df[day_df["EXPIRY_DT"] == near_exp]
            far_df  = day_df[day_df["EXPIRY_DT"] == far_exp]

            # Find common ATM strike
            oi_by_strike = day_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
            if oi_by_strike.empty:
                continue
            atm = float(oi_by_strike.idxmax())

            for opt_type in ["CE", "PE"]:
                near_row = near_df[
                    (near_df["OPTION_TYP"].str.upper() == opt_type) &
                    (near_df["STRIKE_PR"] == atm)
                ]
                far_row = far_df[
                    (far_df["OPTION_TYP"].str.upper() == opt_type) &
                    (far_df["STRIKE_PR"] == atm)
                ]

                if near_row.empty or far_row.empty:
                    continue

                near_prem = float(near_row["SETTLE_PR"].iloc[0])
                far_prem  = float(far_row["SETTLE_PR"].iloc[0])

                if far_prem <= 0:
                    continue

                # IV skew proxy: near premium / far premium
                iv_skew = (near_prem - far_prem) / far_prem

                if iv_skew > self.IV_SKEW_THRESHOLD:
                    net_credit = near_prem - far_prem   # sell near, buy far
                    if net_credit <= 0:
                        continue

                    score = min(85, 50 + int(iv_skew * 100))
                    signals.append(Signal(
                        date        = pd.Timestamp(dt),
                        strategy    = self.name,
                        symbol      = f"NIFTY {int(atm)} {opt_type}",
                        direction   = "SELL",   # short near, long far
                        entry_price = round(net_credit, 2),
                        target_price= round(net_credit * 0.40, 2),
                        stop_loss   = round(net_credit * 1.50, 2),
                        score       = score,
                        notes       = f"Near={near_prem:.0f} Far={far_prem:.0f} Skew={iv_skew:.2%}",
                    ))

        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────

ALL_STRATEGIES = [
    PCRMeanReversion(),
    ATMStrangle(),
    EMACrossover(),
    OIBuildup(),
    CalendarSpread(),
]
