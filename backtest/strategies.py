"""
backtest/strategies.py  v3
==========================
All kite_source strategies — production-grade, conflict-free.

Root causes fixed vs v2:
  ATM_Strangle : Was entering DAILY → 97% stop-outs at exactly 2×.
                 Fixed: enter only Mon/Tue/Wed, hold to expiry (theta decay),
                 emergency stop at 3× only. IV proxy filter added.
  PCR           : R:R was 0.75:1 (target 30% / stop 40%). Fixed to 2:1
                 (target 60% / stop 30%). Also: limit 1 signal/symbol/day.
  CalendarSpread: IV_SKEW_MIN was 12% — too strict, only 13 trades/2yr.
                 Fixed to 4% → expect 80-120 trades/yr.
  IronCondor    : NEW. Sell OTM CE + OTM PE, buy wings for defined risk.
                 Better than naked strangle: bounded max loss.

Strategies:
  1. PCR_MeanReversion   — pcr_strategy.py logic (fixed R:R 2:1)
  2. ATM_Strangle        — hold-to-expiry + IV filter
  3. IronCondor          — defined-risk short volatility (NEW)
  4. EMA_Crossover       — NIFTY/BANKNIFTY only (FINNIFTY excluded — lossy)
  5. OI_Buildup          — sigma-filtered OI directional
  6. Calendar_Spread     — relaxed entry (skew ≥ 4%)
  7. Equity_Momentum     — top-30 NIFTY50 stocks
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Literal, Optional

# ── Lot sizes ──────────────────────────────────────────────────────────────────
LOT_SIZES = {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40, "EQUITY": 1}

# ── Strike increments ──────────────────────────────────────────────────────────
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}


# ── Signal ─────────────────────────────────────────────────────────────────────
@dataclass
class Signal:
    date:           pd.Timestamp
    strategy:       str
    symbol:         str
    instrument:     str
    direction:      Literal["BUY", "SELL"]
    entry_price:    float
    target_price:   float
    stop_loss:      float
    score:          float
    lot_size:       int                    = 50
    notes:          str                    = ""
    # Options-specific
    expiry:         Optional[pd.Timestamp] = None   # hold-to-expiry strategies
    max_loss_ratio: Optional[float]        = None   # early stop for sellers (e.g. 3.0)
    n_legs:         int                    = 2      # legs for cost calculation

    @property
    def risk_reward(self) -> float:
        if self.direction == "BUY":
            risk   = self.entry_price - self.stop_loss
            reward = self.target_price - self.entry_price
        else:
            risk   = self.stop_loss - self.entry_price
            reward = self.entry_price - self.target_price
        return round(reward / risk, 2) if risk > 0 else 0.0


# ── Indicators ─────────────────────────────────────────────────────────────────
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

def _atm_strike(spot: float, sym: str) -> int:
    step = STRIKE_STEP.get(sym, 50)
    return int(round(spot / step) * step)

def _build_ohlc(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Daily OHLC from nearest-expiry ATM option settle prices."""
    sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol.upper()].copy()
    if sym_df.empty:
        return pd.DataFrame()
    rows = []
    for dt, day in sym_df.groupby("DATE"):
        exp    = [e for e in day["EXPIRY_DT"].dropna().unique() if (e - dt).days >= 0]
        if not exp:
            continue
        near   = min(exp, key=lambda e: (e - dt).days)
        near_df = day[day["EXPIRY_DT"] == near]
        oi     = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
        if oi.empty:
            continue
        atm    = float(oi.idxmax())
        atm_df = near_df[near_df["STRIKE_PR"] == atm]
        rows.append({
            "DATE":  pd.Timestamp(dt),
            "OPEN":  pd.to_numeric(atm_df["OPEN"],      errors="coerce").mean(),
            "HIGH":  pd.to_numeric(atm_df["HIGH"],      errors="coerce").max(),
            "LOW":   pd.to_numeric(atm_df["LOW"],       errors="coerce").min(),
            "CLOSE": pd.to_numeric(atm_df["SETTLE_PR"], errors="coerce").mean(),
        })
    if not rows:
        return pd.DataFrame()
    ohlc = pd.DataFrame(rows).set_index("DATE").sort_index()
    return ohlc.dropna(subset=["CLOSE"])

def _nearest_expiry_after(expiries, dt, min_days: int = 1):
    future = [e for e in expiries if (e - dt).days >= min_days]
    return min(future, key=lambda e: (e - dt).days) if future else None


# ═══════════════════════════════════════════════════════════════════════════════
# 1. PCR Mean-Reversion  (FIXED R:R 2:1)
# ═══════════════════════════════════════════════════════════════════════════════
class PCRMeanReversion:
    """
    Entry:
      PCR > 1.25 → Buy ATM Call  (extreme fear → reversal up)
      PCR < 0.80 → Buy ATM Put   (extreme greed → reversal down)

    Exit (FIXED R:R 2:1):
      Target : 60% premium gain   (was 30% — too small)
      Stop   : 30% premium loss   (was 40% — too large)
      Breakeven win rate = 33%   (we're at ~49%)

    Filters:
      - Max 1 signal per symbol per day
      - Min premium ₹50 (avoid illiquid far-OTM options)
    """
    name               = "PCR_MeanReversion"
    PCR_BUY_THRESHOLD  = 1.25
    PCR_SELL_THRESHOLD = 0.80
    MIN_PREMIUM        = 50.0
    TARGET_MULT        = 1.60   # FIXED from 1.30
    STOP_MULT          = 0.70   # FIXED from 0.60

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals    = []
        df         = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            seen_dates = set()
            sym_df     = df[df["SYMBOL"].str.strip().str.upper() == symbol]

            for dt, day in sym_df.groupby("DATE"):
                if dt in seen_dates:
                    continue
                exp = [e for e in day["EXPIRY_DT"].dropna().unique() if (e - dt).days >= 0]
                if not exp:
                    continue
                near    = min(exp, key=lambda e: (e - dt).days)
                near_df = day[day["EXPIRY_DT"] == near]

                pe_oi = near_df[near_df["OPTION_TYP"].str.upper() == "PE"]["OPEN_INT"].sum()
                ce_oi = near_df[near_df["OPTION_TYP"].str.upper() == "CE"]["OPEN_INT"].sum()
                if ce_oi == 0:
                    continue
                pcr = pe_oi / ce_oi

                oi_by_k = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_k.empty:
                    continue
                atm = float(oi_by_k.idxmax())

                if pcr > self.PCR_BUY_THRESHOLD:
                    opt_type = "CE"
                    score    = min(92, 62 + int((pcr - self.PCR_BUY_THRESHOLD) * 60))
                elif pcr < self.PCR_SELL_THRESHOLD:
                    opt_type = "PE"
                    score    = min(92, 62 + int((self.PCR_SELL_THRESHOLD - pcr) * 120))
                else:
                    continue

                rows  = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) &
                                (near_df["STRIKE_PR"] == atm)]
                if rows.empty:
                    continue
                entry = pd.to_numeric(rows["SETTLE_PR"], errors="coerce").dropna()
                if entry.empty or float(entry.iloc[0]) < self.MIN_PREMIUM:
                    continue

                entry = float(entry.iloc[0])
                signals.append(Signal(
                    date         = pd.Timestamp(dt),
                    strategy     = self.name,
                    symbol       = f"{symbol} {int(atm)} {opt_type}",
                    instrument   = symbol,
                    direction    = "BUY",
                    entry_price  = round(entry, 2),
                    target_price = round(entry * self.TARGET_MULT, 2),
                    stop_loss    = round(entry * self.STOP_MULT, 2),
                    score        = score,
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    notes        = f"PCR={pcr:.3f} ATM={atm} {opt_type}",
                ))
                seen_dates.add(dt)

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ATM Strangle  (FIXED: hold-to-expiry + IV filter)
# ═══════════════════════════════════════════════════════════════════════════════
class ATMStrangle:
    """
    Sell ATM CE + PE on Mon/Tue/Wed.  Hold to weekly expiry (theta decay).
    Emergency exit only if combined premium hits 3× entry (MAX_LOSS_RATIO).

    IV proxy filter: net_credit must be ₹150–₹600 (reject ultra-low-IV entries).
    One entry per (symbol, expiry) — no re-entry same week.
    """
    name            = "ATM_Strangle"
    MIN_PREMIUM_LEG = 50     # each leg must be worth ≥ ₹50
    MIN_NET_CREDIT  = 150    # combined credit floor (IV proxy)
    MAX_NET_CREDIT  = 600    # combined credit ceiling (avoid event-week extremes)
    MAX_LOSS_RATIO  = 3.0    # emergency exit — hold otherwise
    ENTRY_DAYS      = {0, 1, 2}  # Mon=0, Tue=1, Wed=2
    MIN_DTE         = 2
    MAX_DTE         = 8

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            seen_expiries = set()
            sym_df        = df[df["SYMBOL"].str.strip().str.upper() == symbol]

            for dt, day in sym_df.groupby("DATE"):
                dt_ts = pd.Timestamp(dt)
                if dt_ts.weekday() not in self.ENTRY_DAYS:
                    continue

                exp = [e for e in day["EXPIRY_DT"].dropna().unique()
                       if self.MIN_DTE <= (e - dt_ts).days <= self.MAX_DTE]
                if not exp:
                    continue
                near = min(exp, key=lambda e: (e - dt_ts).days)
                dte  = (near - dt_ts).days

                if (symbol, near) in seen_expiries:
                    continue

                near_df = day[day["EXPIRY_DT"] == near]
                oi_by_k = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_k.empty:
                    continue
                atm = float(oi_by_k.idxmax())

                ce_rows = near_df[(near_df["OPTION_TYP"].str.upper() == "CE") &
                                  (near_df["STRIKE_PR"] == atm)]
                pe_rows = near_df[(near_df["OPTION_TYP"].str.upper() == "PE") &
                                  (near_df["STRIKE_PR"] == atm)]
                if ce_rows.empty or pe_rows.empty:
                    continue

                ce_p = ce_rows["SETTLE_PR"].dropna()
                pe_p = pe_rows["SETTLE_PR"].dropna()
                if ce_p.empty or pe_p.empty:
                    continue

                ce_prem    = float(ce_p.iloc[0])
                pe_prem    = float(pe_p.iloc[0])

                if ce_prem < self.MIN_PREMIUM_LEG or pe_prem < self.MIN_PREMIUM_LEG:
                    continue

                net_credit = ce_prem + pe_prem
                if net_credit < self.MIN_NET_CREDIT or net_credit > self.MAX_NET_CREDIT:
                    continue

                score = min(88, 52 + int(net_credit / 40) + max(0, 5 - dte) * 3)
                seen_expiries.add((symbol, near))

                signals.append(Signal(
                    date          = dt_ts,
                    strategy      = self.name,
                    symbol        = f"{symbol} {int(atm)} CE+PE",
                    instrument    = symbol,
                    direction     = "SELL",
                    entry_price   = round(net_credit, 2),
                    target_price  = 0.0,                               # hold to expiry
                    stop_loss     = round(net_credit * self.MAX_LOSS_RATIO, 2),
                    score         = score,
                    lot_size      = LOT_SIZES.get(symbol, 50),
                    expiry        = near,
                    max_loss_ratio= self.MAX_LOSS_RATIO,
                    n_legs        = 2,
                    notes         = f"CE={ce_prem:.0f} PE={pe_prem:.0f} DTE={dte} ATM={atm}",
                ))

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Iron Condor  (NEW — defined-risk short volatility)
# ═══════════════════════════════════════════════════════════════════════════════
class IronCondor:
    """
    Structure:
      Sell CE at ATM + 1×step   Buy CE at ATM + 3×step  ← wing caps upside loss
      Sell PE at ATM - 1×step   Buy PE at ATM - 3×step  ← wing caps downside loss

    Net credit = (sell_CE - buy_CE) + (sell_PE - buy_PE)
    Max loss   = strike_width × lot_size - net_credit

    Advantages over naked strangle:
      - Capped max loss (no margin blow-up)
      - Better capital efficiency

    Exit: hold to expiry, emergency exit if net debit reaches 2× credit received.
    """
    name           = "IronCondor"
    MIN_NET_CREDIT = 60
    MAX_LOSS_RATIO = 2.0
    SELL_OFFSET    = 1      # sell at ATM ± 1 step
    BUY_OFFSET     = 3      # buy wing at ATM ± 3 steps
    ENTRY_DAYS     = {0, 1, 2}
    MIN_DTE        = 3
    MAX_DTE        = 8

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            step          = STRIKE_STEP.get(symbol, 50)
            seen_expiries = set()
            sym_df        = df[df["SYMBOL"].str.strip().str.upper() == symbol]

            for dt, day in sym_df.groupby("DATE"):
                dt_ts = pd.Timestamp(dt)
                if dt_ts.weekday() not in self.ENTRY_DAYS:
                    continue

                exp = [e for e in day["EXPIRY_DT"].dropna().unique()
                       if self.MIN_DTE <= (e - dt_ts).days <= self.MAX_DTE]
                if not exp:
                    continue
                near = min(exp, key=lambda e: (e - dt_ts).days)
                dte  = (near - dt_ts).days

                if (symbol, near) in seen_expiries:
                    continue

                near_df = day[day["EXPIRY_DT"] == near]
                oi_by_k = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_k.empty:
                    continue
                atm = float(oi_by_k.idxmax())

                sell_ce_k = atm + self.SELL_OFFSET * step
                buy_ce_k  = atm + self.BUY_OFFSET  * step
                sell_pe_k = atm - self.SELL_OFFSET * step
                buy_pe_k  = atm - self.BUY_OFFSET  * step

                def get_p(strike, opt_t):
                    rows = near_df[(near_df["STRIKE_PR"] == strike) &
                                   (near_df["OPTION_TYP"].str.upper() == opt_t)]
                    p = pd.to_numeric(rows["SETTLE_PR"], errors="coerce").dropna()
                    return float(p.iloc[0]) if not p.empty else None

                sc = get_p(sell_ce_k, "CE")
                bc = get_p(buy_ce_k,  "CE")
                sp = get_p(sell_pe_k, "PE")
                bp = get_p(buy_pe_k,  "PE")

                if any(x is None for x in [sc, bc, sp, bp]):
                    continue

                net_credit = (sc - bc) + (sp - bp)
                if net_credit < self.MIN_NET_CREDIT:
                    continue

                max_loss = (self.BUY_OFFSET - self.SELL_OFFSET) * step - net_credit
                score    = min(90, 55 + int(net_credit / 20) + max(0, 5 - dte) * 3)
                seen_expiries.add((symbol, near))

                ic_symbol = (f"{symbol} IC "
                             f"{sell_ce_k:.0f}/{buy_ce_k:.0f}/"
                             f"{sell_pe_k:.0f}/{buy_pe_k:.0f}")

                signals.append(Signal(
                    date          = dt_ts,
                    strategy      = self.name,
                    symbol        = ic_symbol,
                    instrument    = symbol,
                    direction     = "SELL",
                    entry_price   = round(net_credit, 2),
                    target_price  = 0.0,
                    stop_loss     = round(net_credit * self.MAX_LOSS_RATIO, 2),
                    score         = score,
                    lot_size      = LOT_SIZES.get(symbol, 50),
                    expiry        = near,
                    max_loss_ratio= self.MAX_LOSS_RATIO,
                    n_legs        = 4,
                    notes         = (f"SC={sc:.0f} BC={bc:.0f} SP={sp:.0f} BP={bp:.0f} "
                                     f"Credit={net_credit:.0f} MaxLoss={max_loss:.0f} DTE={dte}"),
                ))

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EMA Crossover  (NIFTY/BANKNIFTY only — FINNIFTY excluded, lossy)
# ═══════════════════════════════════════════════════════════════════════════════
class EMACrossover:
    """EMA9 × EMA21 with RSI > 50 confirmation on ATM option price series."""
    name       = "EMA_Crossover"
    FAST, SLOW = 9, 21

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])

        for symbol in ["NIFTY", "BANKNIFTY"]:   # FINNIFTY excluded — underperforms
            ohlc = _build_ohlc(df, symbol)
            if len(ohlc) < self.SLOW + 5:
                continue

            ohlc["EMA_F"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_S"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]   = _rsi(ohlc["CLOSE"])
            ohlc["ATR"]   = _atr(ohlc)
            prev = ohlc.shift(1)

            buy  = ((ohlc["EMA_F"] > ohlc["EMA_S"]) &
                    (prev["EMA_F"] <= prev["EMA_S"]) &
                    (ohlc["RSI"] > 50))
            sell = ((ohlc["EMA_F"] < ohlc["EMA_S"]) &
                    (prev["EMA_F"] >= prev["EMA_S"]) &
                    (ohlc["RSI"] < 50))

            for dt in ohlc.index[buy]:
                r   = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.012
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="BUY", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] + 2 * atr, 2),
                    stop_loss=round(r["CLOSE"] - atr, 2),
                    score=min(90, 62 + int(r["RSI"] - 50)),
                    lot_size=LOT_SIZES.get(symbol, 50),
                    notes=f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} RSI={r['RSI']:.1f}",
                ))

            for dt in ohlc.index[sell]:
                r   = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.012
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="SELL", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] - 2 * atr, 2),
                    stop_loss=round(r["CLOSE"] + atr, 2),
                    score=min(90, 62 + int(50 - r["RSI"])),
                    lot_size=LOT_SIZES.get(symbol, 50),
                    notes=f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} RSI={r['RSI']:.1f}",
                ))
        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 5. OI Buildup  (sigma-filtered)
# ═══════════════════════════════════════════════════════════════════════════════
class OIBuildup:
    """
    PE OI build > 1.5σ above ATM → support → BUY
    CE OI build > 1.5σ below ATM → resistance → SELL
    """
    name               = "OI_Buildup"
    OI_SIGMA_THRESHOLD = 1.5

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"],  errors="coerce")
        df["CHG_IN_OI"] = pd.to_numeric(df.get("CHG_IN_OI",
                                                pd.Series(0, index=df.index)),
                                         errors="coerce").fillna(0)

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
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

                settle      = day["SETTLE_PR"].dropna()
                spot_proxy  = float(settle.mean()) if not settle.empty else 0
                if spot_proxy <= 0:
                    continue

                pe_above = day[(day["OPTION_TYP"].str.upper() == "PE") &
                               (day["STRIKE_PR"] > atm) &
                               (day["CHG_IN_OI"] > sigma * self.OI_SIGMA_THRESHOLD)]
                ce_below = day[(day["OPTION_TYP"].str.upper() == "CE") &
                               (day["STRIKE_PR"] < atm) &
                               (day["CHG_IN_OI"] > sigma * self.OI_SIGMA_THRESHOLD)]

                if not pe_above.empty:
                    score = min(86, 55 + int(pe_above["CHG_IN_OI"].sum() / sigma * 4))
                    signals.append(Signal(
                        date=pd.Timestamp(dt), strategy=self.name,
                        symbol=symbol, instrument=symbol, direction="BUY",
                        entry_price=round(spot_proxy, 2),
                        target_price=round(spot_proxy * 1.015, 2),
                        stop_loss=round(spot_proxy * 0.993, 2),
                        score=score, lot_size=LOT_SIZES.get(symbol, 50),
                        notes=f"PE OI build >{atm:.0f} σmult={pe_above['CHG_IN_OI'].sum() / sigma:.1f}",
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
                        notes=f"CE OI build <{atm:.0f} σmult={ce_below['CHG_IN_OI'].sum() / sigma:.1f}",
                    ))
        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Calendar Spread  (FIXED: IV_SKEW_MIN 12% → 4%)
# ═══════════════════════════════════════════════════════════════════════════════
class CalendarSpread:
    """
    Sell near-month ATM, buy far-month at same strike.

    FIXED: IV_SKEW_MIN reduced 12% → 4%.
    Old threshold generated only ~13 trades/2yr — statistically meaningless.
    New threshold targets 80-120 trades/yr.

    Exit: Near premium decays to 40% of entry (target) | 1.5× credit (stop).
    """
    name        = "Calendar_Spread"
    IV_SKEW_MIN = 0.04   # FIXED from 0.12
    MIN_CREDIT  = 15     # min net credit ₹

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sym_df     = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            seen_dates = set()

            for dt, day in sym_df.groupby("DATE"):
                if dt in seen_dates:
                    continue
                dt_ts   = pd.Timestamp(dt)
                exps    = sorted(day["EXPIRY_DT"].dropna().unique())
                future  = [e for e in exps if (e - dt_ts).days >= 1]
                if len(future) < 2:
                    continue
                near_exp, far_exp = future[0], future[1]

                near_df = day[day["EXPIRY_DT"] == near_exp]
                far_df  = day[day["EXPIRY_DT"] == far_exp]
                oi_by_k = day.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_k.empty:
                    continue
                atm = float(oi_by_k.idxmax())

                for opt_t in ["CE", "PE"]:
                    nr = near_df[(near_df["OPTION_TYP"].str.upper() == opt_t) &
                                 (near_df["STRIKE_PR"] == atm)]
                    fr = far_df[ (far_df["OPTION_TYP"].str.upper()  == opt_t) &
                                 (far_df["STRIKE_PR"]  == atm)]
                    if nr.empty or fr.empty:
                        continue

                    np_s = nr["SETTLE_PR"].dropna()
                    fp_s = fr["SETTLE_PR"].dropna()
                    if np_s.empty or fp_s.empty:
                        continue

                    np_ = float(np_s.iloc[0])
                    fp_ = float(fp_s.iloc[0])
                    if fp_ <= 0:
                        continue

                    skew = (np_ - fp_) / fp_
                    if skew < self.IV_SKEW_MIN:
                        continue

                    net_credit = np_ - fp_
                    if net_credit < self.MIN_CREDIT:
                        continue

                    signals.append(Signal(
                        date=dt_ts, strategy=self.name,
                        symbol=f"{symbol} {int(atm)} {opt_t}",
                        instrument=symbol, direction="SELL",
                        entry_price=round(net_credit, 2),
                        target_price=round(net_credit * 0.40, 2),
                        stop_loss=round(net_credit * 1.50, 2),
                        score=min(84, 50 + int(skew * 100)),
                        lot_size=LOT_SIZES.get(symbol, 50),
                        expiry=near_exp,
                        max_loss_ratio=1.50,
                        n_legs=2,
                        notes=f"Near={np_:.0f} Far={fp_:.0f} Skew={skew:.2%} {opt_t}",
                    ))
                    seen_dates.add(dt)
                    break  # one signal per day per symbol

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Equity Momentum  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════
class EquityMomentum:
    """EMA crossover on top-30 NIFTY50 stocks with volume confirmation."""
    name            = "Equity_Momentum"
    FAST, SLOW      = 9, 21
    VOL_MULTIPLIER  = 1.2   # volume must be 1.2× 20-day average

    def generate(self, df_equity: pd.DataFrame) -> list[Signal]:
        if df_equity is None or df_equity.empty:
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
            ohlc = grp.sort_values("DATE").set_index("DATE").dropna(subset=["CLOSE"])
            if len(ohlc) < self.SLOW + 5:
                continue

            ohlc["EMA_F"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_S"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]   = _rsi(ohlc["CLOSE"])
            ohlc["ATR"]   = _atr(ohlc)
            ohlc["VOL20"] = ohlc["VOLUME"].rolling(20).mean()
            prev   = ohlc.shift(1)
            vol_ok = ohlc["VOLUME"] >= ohlc["VOL20"] * self.VOL_MULTIPLIER

            buy  = ((ohlc["EMA_F"] > ohlc["EMA_S"]) &
                    (prev["EMA_F"] <= prev["EMA_S"]) &
                    (ohlc["RSI"] > 50) & vol_ok)
            sell = ((ohlc["EMA_F"] < ohlc["EMA_S"]) &
                    (prev["EMA_F"] >= prev["EMA_S"]) &
                    (ohlc["RSI"] < 50) & vol_ok)

            for dt in ohlc.index[buy]:
                r   = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.015
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument="EQUITY",
                    direction="BUY", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] + 2 * atr, 2),
                    stop_loss=round(r["CLOSE"] - atr, 2),
                    score=min(88, 58 + int(r["RSI"] - 50)), lot_size=1,
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f} Vol={r['VOLUME']:.0f}",
                ))

            for dt in ohlc.index[sell]:
                r   = ohlc.loc[dt]
                atr = r["ATR"] if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.015
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument="EQUITY",
                    direction="SELL", entry_price=round(r["CLOSE"], 2),
                    target_price=round(r["CLOSE"] - 2 * atr, 2),
                    stop_loss=round(r["CLOSE"] + atr, 2),
                    score=min(88, 58 + int(50 - r["RSI"])), lot_size=1,
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f} Vol={r['VOLUME']:.0f}",
                ))

        return signals


# ── Registries ─────────────────────────────────────────────────────────────────
FO_STRATEGIES = [
    PCRMeanReversion(),
    ATMStrangle(),
    IronCondor(),
    EMACrossover(),
    OIBuildup(),
    CalendarSpread(),
]

EQUITY_STRATEGIES = [EquityMomentum()]

ALL_STRATEGIES = FO_STRATEGIES + EQUITY_STRATEGIES
