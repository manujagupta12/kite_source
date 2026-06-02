"""
backtest/strategies.py
======================
All trading strategies for kite_source backtester.
Covers: NIFTY, BANKNIFTY, FINNIFTY (F&O) + Top 30 Equity stocks.

Strategies:
  1. PCR_MeanReversion    — pcr_strategy.py
  2. ATM_Strangle         — multistrategy.py (S3 Short Straddle)
  3. EMA_Crossover        — multistrategy.py (E1 EMA Crossover)
  4. OI_Buildup           — nse_connector.py OI data
  5. Calendar_Spread      — Calendaralgofinal.py
  6. Equity_Momentum      — multistrategy.py (E3 ORB / E1 EMA on stocks)
  7. IV_Rank_Strangle      — high-IV entry filter on strangles
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal

# ─────────────────────────────────────────────────────────────────────────────
# Lot sizes (NSE official, 2024)
# ─────────────────────────────────────────────────────────────────────────────
LOT_SIZES = {
    "NIFTY":     50,
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
    "EQUITY":     1,   # shares — sized by capital in run_backtest.py
}

# ─────────────────────────────────────────────────────────────────────────────
# Signal dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Signal:
    date: pd.Timestamp
    strategy: str
    symbol: str
    instrument: str          # "NIFTY" | "BANKNIFTY" | "FINNIFTY" | "EQUITY"
    direction: Literal["BUY", "SELL"]
    entry_price: float
    target_price: float
    stop_loss: float
    score: float             # 0–100
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
# Indicator helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["HIGH"] - df["LOW"]
    hc = (df["HIGH"] - df["CLOSE"].shift()).abs()
    lc = (df["LOW"]  - df["CLOSE"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(n).mean()

def _nearest_strike(spot: float, step: int = 50) -> int:
    return int(round(spot / step) * step)

def _build_daily_ohlc(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Build daily OHLC from nearest-expiry options settle prices."""
    sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol.upper()].copy()
    if sym_df.empty:
        return pd.DataFrame()

    rows = []
    for dt, day in sym_df.groupby("DATE"):
        expiries = day["EXPIRY_DT"].dropna().unique()
        future   = [e for e in expiries if (e - dt).days >= 0]
        if not future:
            continue
        near = min(future, key=lambda e: (e - dt).days)
        near_df = day[day["EXPIRY_DT"] == near]

        # Use highest-OI strike as ATM proxy
        oi_by_strike = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
        if oi_by_strike.empty:
            continue
        atm = float(oi_by_strike.idxmax())
        atm_opts = near_df[near_df["STRIKE_PR"] == atm]

        rows.append({
            "DATE":  pd.Timestamp(dt),
            "OPEN":  pd.to_numeric(atm_opts["OPEN"],      errors="coerce").mean(),
            "HIGH":  pd.to_numeric(atm_opts["HIGH"],      errors="coerce").max(),
            "LOW":   pd.to_numeric(atm_opts["LOW"],       errors="coerce").min(),
            "CLOSE": pd.to_numeric(atm_opts["SETTLE_PR"], errors="coerce").mean(),
        })

    if not rows:
        return pd.DataFrame()

    ohlc = pd.DataFrame(rows).set_index("DATE").sort_index()
    ohlc.dropna(subset=["CLOSE"], inplace=True)
    return ohlc


# ─────────────────────────────────────────────────────────────────────────────
# 1. PCR Mean-Reversion
# ─────────────────────────────────────────────────────────────────────────────

class PCRMeanReversion:
    """
    Entry: PCR > 1.25 → BUY (buy ATM Call, fear extreme → reversal up)
           PCR < 0.80 → BUY put (direction stored as SELL for engine)
    Exit:  25% premium gain = target | 40% premium loss = stop
    Applies to: NIFTY, BANKNIFTY, FINNIFTY
    """
    name = "PCR_MeanReversion"
    PCR_BUY_THRESHOLD  = 1.25   # extreme fear → buy call
    PCR_SELL_THRESHOLD = 0.80   # extreme greed → buy put
    SCORE_BASE         = 62

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            for dt, day in sym_df.groupby("DATE"):
                expiries = day["EXPIRY_DT"].dropna().unique()
                future   = [e for e in expiries if (e - dt).days >= 0]
                if not future:
                    continue
                near     = min(future, key=lambda e: (e - dt).days)
                near_df  = day[day["EXPIRY_DT"] == near]

                pe_oi = near_df[near_df["OPTION_TYP"].str.upper() == "PE"]["OPEN_INT"].sum()
                ce_oi = near_df[near_df["OPTION_TYP"].str.upper() == "CE"]["OPEN_INT"].sum()
                if ce_oi == 0:
                    continue
                pcr = pe_oi / ce_oi

                oi_by_strike = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                atm = float(oi_by_strike.idxmax())

                if pcr > self.PCR_BUY_THRESHOLD:
                    opt_type, direction = "CE", "BUY"
                    score = min(93, self.SCORE_BASE + int((pcr - self.PCR_BUY_THRESHOLD) * 60))
                elif pcr < self.PCR_SELL_THRESHOLD:
                    opt_type, direction = "PE", "BUY"  # buying put = premium rises when market falls
                    score = min(93, self.SCORE_BASE + int((self.PCR_SELL_THRESHOLD - pcr) * 120))
                else:
                    continue

                opt_rows = near_df[
                    (near_df["OPTION_TYP"].str.upper() == opt_type) &
                    (near_df["STRIKE_PR"] == atm)
                ]
                if opt_rows.empty:
                    continue
                entry = pd.to_numeric(opt_rows["SETTLE_PR"], errors="coerce").dropna()
                if entry.empty or entry.iloc[0] <= 10:
                    continue
                entry = float(entry.iloc[0])

                signals.append(Signal(
                    date         = pd.Timestamp(dt),
                    strategy     = self.name,
                    symbol       = f"{symbol} {int(atm)} {opt_type}",
                    instrument   = symbol,
                    direction    = direction,
                    entry_price  = round(entry, 2),
                    target_price = round(entry * 1.30, 2),  # 30% gain on premium
                    stop_loss    = round(entry * 0.60, 2),  # 40% loss
                    score        = score,
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    notes        = f"PCR={pcr:.3f} ATM={atm} {opt_type}",
                ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# 2. ATM Strangle (Short Volatility)
# ─────────────────────────────────────────────────────────────────────────────

class ATMStrangle:
    """
    Entry: Sell ATM CE + ATM PE on weekly expiry when IV is elevated
           (both premiums > MIN_PREMIUM, net credit > 150)
    Exit:  collect 50% of net credit = target | 2× net credit debit = stop
    Applies to: NIFTY, BANKNIFTY, FINNIFTY
    """
    name = "ATM_Strangle"
    MIN_PREMIUM   = 80    # minimum per leg to ensure IV is elevated
    MIN_NET_CREDIT = 150  # minimum combined premium to justify trade

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            for dt, day in sym_df.groupby("DATE"):
                expiries = day["EXPIRY_DT"].dropna().unique()
                future   = [e for e in expiries if (e - dt).days >= 1]
                if not future:
                    continue
                near = min(future, key=lambda e: (e - dt).days)
                dte  = (near - dt).days
                if dte < 2 or dte > 8:   # weekly window only
                    continue

                near_df = day[day["EXPIRY_DT"] == near]
                oi_by_strike = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                atm = float(oi_by_strike.idxmax())

                ce_rows = near_df[(near_df["OPTION_TYP"].str.upper() == "CE") & (near_df["STRIKE_PR"] == atm)]
                pe_rows = near_df[(near_df["OPTION_TYP"].str.upper() == "PE") & (near_df["STRIKE_PR"] == atm)]
                if ce_rows.empty or pe_rows.empty:
                    continue

                ce_p = ce_rows["SETTLE_PR"].dropna()
                pe_p = pe_rows["SETTLE_PR"].dropna()
                if ce_p.empty or pe_p.empty:
                    continue

                ce_prem = float(ce_p.iloc[0])
                pe_prem = float(pe_p.iloc[0])

                if ce_prem < self.MIN_PREMIUM or pe_prem < self.MIN_PREMIUM:
                    continue
                net_credit = ce_prem + pe_prem
                if net_credit < self.MIN_NET_CREDIT:
                    continue

                score = min(88, 52 + int(net_credit / 30) + max(0, 5 - dte) * 2)

                signals.append(Signal(
                    date         = pd.Timestamp(dt),
                    strategy     = self.name,
                    symbol       = f"{symbol} {int(atm)} CE+PE",
                    instrument   = symbol,
                    direction    = "SELL",
                    entry_price  = round(net_credit, 2),
                    target_price = round(net_credit * 0.50, 2),
                    stop_loss    = round(net_credit * 2.00, 2),
                    score        = score,
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    notes        = f"CE={ce_prem:.0f} PE={pe_prem:.0f} DTE={dte}",
                ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# 3. EMA Crossover (Trend)
# ─────────────────────────────────────────────────────────────────────────────

class EMACrossover:
    """
    Entry: EMA9 × EMA21 with RSI confirmation on ATM option price series
    Exit:  2×ATR target | 1×ATR stop
    Applies to: NIFTY, BANKNIFTY, FINNIFTY
    """
    name = "EMA_Crossover"
    FAST, SLOW = 9, 21

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            ohlc = _build_daily_ohlc(df, symbol)
            if len(ohlc) < self.SLOW + 5:
                continue

            ohlc["EMA_F"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_S"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]   = _rsi(ohlc["CLOSE"])
            ohlc["ATR"]   = _atr(ohlc)
            prev = ohlc.shift(1)

            buy  = (ohlc["EMA_F"] > ohlc["EMA_S"]) & (prev["EMA_F"] <= prev["EMA_S"]) & (ohlc["RSI"] > 50)
            sell = (ohlc["EMA_F"] < ohlc["EMA_S"]) & (prev["EMA_F"] >= prev["EMA_S"]) & (ohlc["RSI"] < 50)

            for dt in ohlc.index[buy]:
                r = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.012
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="BUY", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] + 2*atr, 2),
                    stop_loss=round(r["CLOSE"] - atr, 2),
                    score=min(90, 62 + int(r["RSI"] - 50)),
                    lot_size=LOT_SIZES.get(symbol, 50),
                    notes=f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} RSI={r['RSI']:.1f}",
                ))

            for dt in ohlc.index[sell]:
                r = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.012
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="SELL", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] - 2*atr, 2),
                    stop_loss=round(r["CLOSE"] + atr, 2),
                    score=min(90, 62 + int(50 - r["RSI"])),
                    lot_size=LOT_SIZES.get(symbol, 50),
                    notes=f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} RSI={r['RSI']:.1f}",
                ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# 4. OI Build-up Directional
# ─────────────────────────────────────────────────────────────────────────────

class OIBuildup:
    """
    Entry: Significant PE OI build above ATM → support → BUY
           Significant CE OI build below ATM → resistance → SELL
    Exit:  1.5% spot move (target) | 0.7% adverse (stop)
    Applies to: NIFTY, BANKNIFTY, FINNIFTY
    """
    name = "OI_Buildup"
    OI_SIGMA_THRESHOLD = 1.5   # OI change must be > 1.5σ of rolling 10-day OI

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["CHG_IN_OI"] = pd.to_numeric(df.get("CHG_IN_OI", pd.Series()), errors="coerce").fillna(0)
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"],  errors="coerce").fillna(0)
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol].copy()
            if sym_df.empty:
                continue

            daily_oi = sym_df.groupby("DATE")["CHG_IN_OI"].sum().sort_index()
            if len(daily_oi) < 10:
                continue
            oi_std = daily_oi.rolling(10).std()

            for dt, day in sym_df.groupby("DATE"):
                sigma = oi_std.get(dt, None)
                if sigma is None or pd.isna(sigma) or sigma == 0:
                    continue

                oi_by_strike = day.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                atm = float(oi_by_strike.idxmax())

                settle = day["SETTLE_PR"].dropna()
                spot_proxy = float(settle.mean()) if not settle.empty else 0
                if spot_proxy <= 0:
                    continue

                pe_above = day[
                    (day["OPTION_TYP"].str.upper() == "PE") &
                    (day["STRIKE_PR"] > atm) &
                    (day["CHG_IN_OI"] > sigma * self.OI_SIGMA_THRESHOLD)
                ]
                ce_below = day[
                    (day["OPTION_TYP"].str.upper() == "CE") &
                    (day["STRIKE_PR"] < atm) &
                    (day["CHG_IN_OI"] > sigma * self.OI_SIGMA_THRESHOLD)
                ]

                if not pe_above.empty:
                    score = min(86, 55 + int(pe_above["CHG_IN_OI"].sum() / sigma * 4))
                    signals.append(Signal(
                        date=pd.Timestamp(dt), strategy=self.name,
                        symbol=symbol, instrument=symbol, direction="BUY",
                        entry_price=round(spot_proxy, 2),
                        target_price=round(spot_proxy * 1.015, 2),
                        stop_loss=round(spot_proxy * 0.993, 2),
                        score=score, lot_size=LOT_SIZES.get(symbol, 50),
                        notes=f"PE OI build >{atm:.0f} σmult={pe_above['CHG_IN_OI'].sum()/sigma:.1f}",
                    ))

                if not ce_below.empty:
                    score = min(86, 55 + int(ce_below["CHG_IN_OI"].sum() / sigma * 4))
                    signals.append(Signal(
                        date=pd.Timestamp(dt), strategy=self.name,
                        symbol=symbol, instrument=symbol, direction="SELL",
                        entry_price=round(spot_proxy, 2),
                        target_price=round(spot_proxy * 0.985, 2),
                        stop_loss=round(spot_proxy * 1.007, 2),
                        score=score, lot_size=LOT_SIZES.get(symbol, 50),
                        notes=f"CE OI build <{atm:.0f} σmult={ce_below['CHG_IN_OI'].sum()/sigma:.1f}",
                    ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# 5. Calendar Spread
# ─────────────────────────────────────────────────────────────────────────────

class CalendarSpread:
    """
    Entry: Near-month ATM premium > far-month ATM premium by > 12% (IV skew)
           Sell near, buy far at same strike
    Exit:  Near premium decays to 40% of entry (target) | loss > 1.5× credit (stop)
    Applies to: NIFTY, BANKNIFTY, FINNIFTY
    """
    name = "Calendar_Spread"
    IV_SKEW_MIN = 0.12

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            for dt, day in sym_df.groupby("DATE"):
                expiries = sorted(day["EXPIRY_DT"].dropna().unique())
                future   = [e for e in expiries if (e - dt).days >= 1]
                if len(future) < 2:
                    continue
                near_exp, far_exp = future[0], future[1]
                near_df = day[day["EXPIRY_DT"] == near_exp]
                far_df  = day[day["EXPIRY_DT"] == far_exp]

                oi_by_strike = day.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                atm = float(oi_by_strike.idxmax())

                for opt_type in ["CE", "PE"]:
                    nr = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) & (near_df["STRIKE_PR"] == atm)]
                    fr = far_df[ (far_df["OPTION_TYP"].str.upper()  == opt_type) & (far_df["STRIKE_PR"]  == atm)]
                    if nr.empty or fr.empty:
                        continue

                    near_p = nr["SETTLE_PR"].dropna()
                    far_p  = fr["SETTLE_PR"].dropna()
                    if near_p.empty or far_p.empty:
                        continue

                    np_ = float(near_p.iloc[0])
                    fp_ = float(far_p.iloc[0])
                    if fp_ <= 0:
                        continue

                    skew = (np_ - fp_) / fp_
                    if skew < self.IV_SKEW_MIN:
                        continue

                    net_credit = np_ - fp_
                    if net_credit <= 20:
                        continue

                    signals.append(Signal(
                        date=pd.Timestamp(dt), strategy=self.name,
                        symbol=f"{symbol} {int(atm)} {opt_type}",
                        instrument=symbol, direction="SELL",
                        entry_price=round(net_credit, 2),
                        target_price=round(net_credit * 0.40, 2),
                        stop_loss=round(net_credit * 1.50, 2),
                        score=min(84, 50 + int(skew * 100)),
                        lot_size=LOT_SIZES.get(symbol, 50),
                        notes=f"Near={np_:.0f} Far={fp_:.0f} Skew={skew:.2%} {opt_type}",
                    ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# 6. Equity Momentum (EMA on top NIFTY50 stocks)
# ─────────────────────────────────────────────────────────────────────────────

class EquityMomentum:
    """
    Entry: EMA9 × EMA21 crossover on equity OHLC, volume > 20-day avg volume
    Exit:  2×ATR target | 1×ATR stop
    Applies to: top 30 NIFTY50 stocks
    """
    name = "Equity_Momentum"
    FAST, SLOW = 9, 21
    VOL_MULTIPLIER = 1.2   # volume must be 1.2× 20-day average

    def generate(self, df_equity: pd.DataFrame) -> list[Signal]:
        if df_equity.empty:
            return []

        signals = []
        df = df_equity.copy()
        df["DATE"]   = pd.to_datetime(df["DATE"])
        df["CLOSE"]  = pd.to_numeric(df["CLOSE"],  errors="coerce")
        df["HIGH"]   = pd.to_numeric(df["HIGH"],   errors="coerce")
        df["LOW"]    = pd.to_numeric(df["LOW"],    errors="coerce")
        df["OPEN"]   = pd.to_numeric(df["OPEN"],   errors="coerce")
        df["VOLUME"] = pd.to_numeric(df.get("VOLUME", pd.Series(0, index=df.index)),
                                      errors="coerce").fillna(0)

        for symbol, grp in df.groupby("SYMBOL"):
            ohlc = grp.sort_values("DATE").set_index("DATE")
            ohlc.dropna(subset=["CLOSE"], inplace=True)
            if len(ohlc) < self.SLOW + 5:
                continue

            ohlc["EMA_F"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_S"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]   = _rsi(ohlc["CLOSE"])
            ohlc["ATR"]   = _atr(ohlc)
            ohlc["VOL20"] = ohlc["VOLUME"].rolling(20).mean()
            prev = ohlc.shift(1)

            vol_ok = ohlc["VOLUME"] >= ohlc["VOL20"] * self.VOL_MULTIPLIER

            buy  = (ohlc["EMA_F"] > ohlc["EMA_S"]) & (prev["EMA_F"] <= prev["EMA_S"]) & (ohlc["RSI"] > 50) & vol_ok
            sell = (ohlc["EMA_F"] < ohlc["EMA_S"]) & (prev["EMA_F"] >= prev["EMA_S"]) & (ohlc["RSI"] < 50) & vol_ok

            for dt in ohlc.index[buy]:
                r = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.015
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol,
                    instrument="EQUITY", direction="BUY",
                    entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] + 2*atr, 2),
                    stop_loss=round(r["CLOSE"] - atr, 2),
                    score=min(88, 58 + int(r["RSI"] - 50)),
                    lot_size=1,
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f} Vol={r['VOLUME']:.0f}",
                ))

            for dt in ohlc.index[sell]:
                r = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.015
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol,
                    instrument="EQUITY", direction="SELL",
                    entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] - 2*atr, 2),
                    stop_loss=round(r["CLOSE"] + atr, 2),
                    score=min(88, 58 + int(50 - r["RSI"])),
                    lot_size=1,
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f} Vol={r['VOLUME']:.0f}",
                ))
        return signals


# ─────────────────────────────────────────────────────────────────────────────
# Registries
# ─────────────────────────────────────────────────────────────────────────────

FO_STRATEGIES = [
    PCRMeanReversion(),
    ATMStrangle(),
    EMACrossover(),
    OIBuildup(),
    CalendarSpread(),
]

EQUITY_STRATEGIES = [
    EquityMomentum(),
]

ALL_STRATEGIES = FO_STRATEGIES + EQUITY_STRATEGIES
