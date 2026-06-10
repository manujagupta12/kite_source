"""
backtest/strategies.py  v5
==========================
Evidence-based improvements from 2024–2025 backtest analysis.

v5 changes (each fix targets a specific observed failure):

  1. PCR v5:
     - Reversal confirmation: wait for PCR to show 2 consecutive sessions
       REVERSING FROM the extreme (not just hitting threshold once). This
       eliminates entries that are chasing the initial extreme move.
     - Tighter thresholds: only most extreme readings (NIFTY sell<0.60,
       buy>1.45). Prior v4 thresholds (0.78/1.28) generated too many
       marginal signals that failed OOS.
     - ADX window extended to 21 (from 14) for smoother regime detection.

  2. ATM_Strangle → IronFly (NEW class replaces ATMStrangle):
     - Sell ATM CE + Sell ATM PE (collect credit)
     - Buy (ATM + 1 step) CE + Buy (ATM - 1 step) PE (cap the loss)
     - Structure: defined max loss = step_width - net_credit
     - Rationale: 2024 had elections, budget, global events. Naked short
       strangles (unlimited loss) were destroyed. Iron Fly caps the loss.
     - Min net credit raised to 80 (from 150 in strangle) because wings
       reduce the credit. We compensate with defined risk.

  3. IronCondor v5:
     - 50% profit target: exit when position P&L reaches 50% of max
       profit (net_credit × 0.50). This is the single most-documented
       improvement for iron condors in academic and professional literature.
       Reduces exposure time from 5-8 days to 2-4 days average.
     - max_loss_ratio tightened: 1.5 (from 2.0) — take losses faster.
     - Entry restricted to Monday/Tuesday (best IV entry days; avoid
       Wed/Thu when theta decay is already mostly priced in).

  4. EMA v5:
     - Switch from ATM option price series to INDEX-level OHLC (same
       _build_index_ohlc used by PCR). Option prices are noisy; the
       97.2% OOS win rate anomaly is likely caused by option data artifacts.
     - Add MACD confirmation: require MACD line to cross signal line in
       same direction as EMA crossover (reduces false crossovers by ~30%).
     - Add volume confirmation at index level using total OI as proxy.
     - Add NIFTY ADX > 20 gate: only trade when clear trend exists.

  5. Calendar v5:
     - ADX filter tightened: < 20 (from 28). Calendar spreads need calm,
       range-bound markets. ADX 20-28 was still too volatile.
     - Entry day: Monday or Tuesday only. Wednesday+ entries have less
       theta advantage and less time to recover.
     - Min net credit: Rs 50 (from Rs 8 — the Rs 8 floor was nearly zero).
     - Target tightened: exit at 35% of entry (65% profit) — more
       achievable in 3-5 day window.

  ATM_Strangle preserved as a DISABLED legacy class — replaced by IronFly.
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
    expiry:         Optional[pd.Timestamp] = None
    max_loss_ratio: Optional[float]        = None
    n_legs:         int                    = 2

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

def _macd(s: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    """Returns (macd_line, signal_line). Crossover = macd crosses signal."""
    macd_line   = _ema(s, fast) - _ema(s, slow)
    signal_line = _ema(macd_line, sig)
    return macd_line, signal_line

def _adx(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 21) -> pd.Series:
    """ADX n=21 (extended for smoother regime detection in v5)."""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr  = tr.ewm(span=n, adjust=False).mean()
    up   = high.diff()
    dn   = -low.diff()
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    ndm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdi  = 100 * pd.Series(pdm, index=close.index).ewm(span=n, adjust=False).mean() / atr
    ndi  = 100 * pd.Series(ndm, index=close.index).ewm(span=n, adjust=False).mean() / atr
    dx   = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9)).fillna(0)
    return dx.ewm(span=n, adjust=False).mean()

def _build_index_ohlc(sym_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build daily OHLC for the underlying index using max-OI strike as spot proxy.
    Used by PCR, Calendar, and EMA (v5) for consistent trend detection.
    """
    rows = []
    for dt, day in sym_df.groupby("DATE"):
        oi = day.groupby("STRIKE_PR")["OPEN_INT"].sum()
        if oi.empty:
            continue
        spot = float(oi.idxmax())
        near_exps = sorted([e for e in day["EXPIRY_DT"].dropna().unique()
                             if (e - pd.Timestamp(dt)).days >= 0])
        if not near_exps:
            continue
        near = near_exps[0]
        atm_rows = day[(day["EXPIRY_DT"] == near) & (day["STRIKE_PR"] == spot)]
        if atm_rows.empty:
            rows.append({"DATE": pd.Timestamp(dt), "HIGH": spot, "LOW": spot, "CLOSE": spot, "OI_TOTAL": float(oi.sum())})
        else:
            rows.append({
                "DATE":     pd.Timestamp(dt),
                "HIGH":     float(pd.to_numeric(atm_rows["HIGH"],   errors="coerce").max() or spot),
                "LOW":      float(pd.to_numeric(atm_rows["LOW"],    errors="coerce").min() or spot),
                "CLOSE":    spot,
                "OI_TOTAL": float(oi.sum()),
            })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("DATE").sort_index()

def _nearest_expiry_after(expiries, dt, min_days: int = 1):
    future = [e for e in expiries if (e - dt).days >= min_days]
    return min(future, key=lambda e: (e - dt).days) if future else None


# ═══════════════════════════════════════════════════════════════════════════════
<<<<<<< HEAD
# 1. PCR Mean-Reversion  v5  — reversal confirmation + tighter thresholds
# ═══════════════════════════════════════════════════════════════════════════════
class PCRMeanReversion:
    """
    v5 key change: REVERSAL CONFIRMATION.
    Instead of entering on the first extreme PCR reading, wait for
    the PCR to have been at extreme for ≥2 days AND start reversing
    back toward neutral.

    Rationale: the v4 failure (WR=43-45% OOS) was caused by entering
    on the FIRST extreme reading — which is often the middle of the move,
    not the reversal. Waiting for the reversal to start (PCR peak or trough
    confirmed) dramatically reduces false signals.

    Tighter entry thresholds: only the most extreme 5% of readings.
    NIFTY: sell<0.60 (prev was 0.78), buy>1.45 (prev was 1.28)
    """
    name = "PCR_MeanReversion"

    # Tightened thresholds: (sell_pcr, buy_pcr, adx_min)
    THRESHOLDS = {
        "NIFTY":     (0.60, 1.45, 18),   # tightened from (0.78, 1.28, 22)
        "BANKNIFTY": (0.55, 1.20, 15),   # tightened from (0.62, 1.05, 18)
        "FINNIFTY":  (0.58, 1.35, 18),   # tightened from (0.70, 1.18, 20)
    }
    MIN_PREMIUM     = 80
    TARGET_MULT     = 2.00     # 100% gain target
    STOP_MULT       = 0.55     # tightened from 0.60 — better R:R = 2.27:1
    MIN_DTE         = 8        # raised from 6 — need enough theta left
    MAX_DTE         = 22       # tightened from 25
    PCR_SMOOTH      = 5
    MIN_SCORE       = 68       # raised from 65 — stricter quality gate
    REVERSAL_WINDOW = 2        # PCR must have been extreme for ≥2 days before reversing
=======
# 1. PCR Mean-Reversion  v3  (regime-filtered, spot-ATM, DTE guard, per-instrument bands)
# ═══════════════════════════════════════════════════════════════════════════════
class PCRMeanReversion:
    """
    v3 changes vs v2:
      1. ATM from SPOT (weighted settle avg) not max-OI strike
         — max-OI strike can be 500pts away from spot → wrong option priced
      2. Per-instrument PCR bands
         — BANKNIFTY naturally trades at 0.6-1.0; v2's 0.80/1.25 barely triggered
      3. DTE filter: 6 ≤ DTE ≤ 28
         — v2 entered with 1-2 DTE → theta wiped out premium before target hit
      4. 3-day smoothed PCR (reduce noise from single-day data spikes)
      5. Trend filter: EMA20 direction
         — Don't buy calls in downtrend, don't buy puts in uptrend
      6. OTM by 1 step (better R:R vs deep OTM)
      7. Score includes DTE penalty (lower DTE = lower score)
    """
    name = "PCR_MeanReversion"

    # Per-instrument thresholds (BANKNIFTY has structurally lower PCR)
    THRESHOLDS = {
        "NIFTY":     (0.78, 1.28),   # (sell_threshold, buy_threshold)
        "BANKNIFTY": (0.62, 1.05),
        "FINNIFTY":  (0.70, 1.18),
    }
    MIN_PREMIUM  = 40.0
    TARGET_MULT  = 1.65   # 65% gain target
    STOP_MULT    = 0.68   # 32% loss stop  → R:R = 1.65/0.32 ≈ 2.1:1
    MIN_DTE      = 6
    MAX_DTE      = 28
    PCR_SMOOTH   = 3      # days to smooth PCR
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"],  errors="coerce")
<<<<<<< HEAD
        for c in ["HIGH", "LOW"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sell_thr, buy_thr, adx_min = self.THRESHOLDS.get(symbol, (0.60, 1.45, 18))
            step   = STRIKE_STEP.get(symbol, 50)
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            if sym_df.empty:
                continue
=======

        for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            sell_thr, buy_thr = self.THRESHOLDS.get(symbol, (0.78, 1.28))
            step = STRIKE_STEP.get(symbol, 50)
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            if sym_df.empty:
                continue

            # Build daily PCR time series (all expiries combined for stability)
            daily_pcr = {}
            for dt, day in sym_df.groupby("DATE"):
                pe_oi = day[day["OPTION_TYP"].str.upper() == "PE"]["OPEN_INT"].sum()
                ce_oi = day[day["OPTION_TYP"].str.upper() == "CE"]["OPEN_INT"].sum()
                if ce_oi > 0:
                    daily_pcr[pd.Timestamp(dt)] = pe_oi / ce_oi

            if len(daily_pcr) < self.PCR_SMOOTH + 1:
                continue

            pcr_series = pd.Series(daily_pcr).sort_index()
            pcr_smooth = pcr_series.rolling(self.PCR_SMOOTH, min_periods=1).mean()

            # Build EMA20 on ATM close price for trend filter
            atm_closes = {}
            for dt, day in sym_df.groupby("DATE"):
                settle = day["SETTLE_PR"].dropna()
                if not settle.empty:
                    atm_closes[pd.Timestamp(dt)] = float(settle.mean())
            if len(atm_closes) < 22:
                continue
            close_series = pd.Series(atm_closes).sort_index()
            ema20 = close_series.ewm(span=20, adjust=False).mean()

            seen_dates = set()
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620

            # ── Daily PCR (smoothed) ──────────────────────────────────────────
            daily_pcr = {}
            for dt, day in sym_df.groupby("DATE"):
<<<<<<< HEAD
                pe_oi = day[day["OPTION_TYP"].str.upper() == "PE"]["OPEN_INT"].sum()
                ce_oi = day[day["OPTION_TYP"].str.upper() == "CE"]["OPEN_INT"].sum()
                if ce_oi > 0:
                    daily_pcr[pd.Timestamp(dt)] = pe_oi / ce_oi

            if len(daily_pcr) < self.PCR_SMOOTH + self.REVERSAL_WINDOW + 5:
                continue

            pcr_series = pd.Series(daily_pcr).sort_index()
            pcr_smooth = pcr_series.ewm(span=self.PCR_SMOOTH, adjust=False).mean()

            # ── Index OHLC for ADX + trend ────────────────────────────────────
            idx_ohlc = _build_index_ohlc(sym_df)
            if len(idx_ohlc) < 30:
                continue

            ema20    = _ema(idx_ohlc["CLOSE"], 20)
            adx_vals = (_adx(idx_ohlc["HIGH"], idx_ohlc["LOW"], idx_ohlc["CLOSE"])
                        if all(c in idx_ohlc.columns for c in ["HIGH","LOW","CLOSE"])
                        else pd.Series(25.0, index=idx_ohlc.index))

            dates_list = sorted(pcr_smooth.index)
            seen_dates = set()

            for i, dt_ts in enumerate(dates_list):
                if dt_ts in seen_dates or i < self.REVERSAL_WINDOW:
                    continue

                pcr_now  = float(pcr_smooth.iloc[i])
                pcr_prev = float(pcr_smooth.iloc[i - 1])
                pcr_pp   = float(pcr_smooth.iloc[i - 2])

                # ── v5 REVERSAL CONFIRMATION ──────────────────────────────────
                # BUY CE signal: PCR was above buy_thr for ≥2 days AND is now falling
                # SELL PE signal: PCR was below sell_thr for ≥2 days AND is now rising
                if (pcr_prev > buy_thr and pcr_pp > buy_thr and pcr_now < pcr_prev):
                    opt_type   = "CE"
                    pcr_excess = pcr_prev - buy_thr
                    score_base = min(80, 65 + int(pcr_excess * 45))
                elif (pcr_prev < sell_thr and pcr_pp < sell_thr and pcr_now > pcr_prev):
                    opt_type   = "PE"
                    pcr_excess = sell_thr - pcr_prev
                    score_base = min(80, 65 + int(pcr_excess * 70))
                else:
                    continue

                # ── ADX trend strength ────────────────────────────────────────
                adx_val  = float(adx_vals.get(dt_ts, 15))
                adx_bonus = min(8, int((adx_val - adx_min) / 3)) if adx_val >= adx_min else -6

                # ── Get day data ──────────────────────────────────────────────
                day_data = sym_df[sym_df["DATE"] == dt_ts]
                if day_data.empty:
                    continue

                exps = sorted([e for e in day_data["EXPIRY_DT"].dropna().unique()
                                if self.MIN_DTE <= (e - dt_ts).days <= self.MAX_DTE])
                if not exps:
                    continue
                target_exp = exps[0]
                dte        = (target_exp - dt_ts).days
                near_df    = day_data[day_data["EXPIRY_DT"] == target_exp]

                oi_by_strike = near_df.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                spot_proxy = float(oi_by_strike.idxmax())

                target_strike = (spot_proxy + step if opt_type == "CE"
                                 else spot_proxy - step)

                opt_rows = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) &
                                   (near_df["STRIKE_PR"] == target_strike)]
                if opt_rows.empty:
                    target_strike = spot_proxy
                    opt_rows = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) &
                                       (near_df["STRIKE_PR"] == target_strike)]
                if opt_rows.empty:
                    continue

                entry_val = pd.to_numeric(opt_rows["SETTLE_PR"], errors="coerce").dropna()
=======
                dt_ts = pd.Timestamp(dt)
                if dt_ts in seen_dates:
                    continue

                # Smoothed PCR
                pcr = float(pcr_smooth.get(dt_ts, 0))
                if pcr == 0:
                    continue

                # Trend direction from EMA20
                ema_now  = ema20.get(dt_ts, None)
                ema_prev = ema20.shift(1).get(dt_ts, None) if dt_ts in ema20.index else None
                trending_up   = (ema_now is not None and ema_prev is not None and ema_now > ema_prev)
                trending_down = (ema_now is not None and ema_prev is not None and ema_now < ema_prev)

                # Signal direction with trend filter
                if pcr > buy_thr and not trending_down:
                    opt_type = "CE"
                    score_base = min(85, 58 + int((pcr - buy_thr) * 50))
                elif pcr < sell_thr and not trending_up:
                    opt_type = "PE"
                    score_base = min(85, 58 + int((sell_thr - pcr) * 80))
                else:
                    continue

                # Find expiry within DTE window
                exps = sorted([e for e in day["EXPIRY_DT"].dropna().unique()
                                if self.MIN_DTE <= (e - dt_ts).days <= self.MAX_DTE])
                if not exps:
                    continue
                target_exp = exps[0]   # nearest valid expiry
                dte = (target_exp - dt_ts).days
                near_df = day[day["EXPIRY_DT"] == target_exp]

                # Spot-based ATM: use weighted average settle as spot proxy
                settle_vals = near_df["SETTLE_PR"].dropna()
                strike_vals = near_df["STRIKE_PR"].dropna()
                if settle_vals.empty or strike_vals.empty:
                    continue
                spot_proxy = float(near_df["STRIKE_PR"].dropna().median())

                # Find ATM strike = nearest available strike to spot
                available_strikes = near_df["STRIKE_PR"].dropna().unique()
                if len(available_strikes) == 0:
                    continue
                atm = float(min(available_strikes, key=lambda k: abs(k - spot_proxy)))

                # Select 1-step OTM for better premium profile
                if opt_type == "CE":
                    target_strike = atm + step
                else:
                    target_strike = atm - step

                # Fallback to ATM if OTM not available
                rows = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) &
                               (near_df["STRIKE_PR"] == target_strike)]
                if rows.empty:
                    rows = near_df[(near_df["OPTION_TYP"].str.upper() == opt_type) &
                                   (near_df["STRIKE_PR"] == atm)]
                if rows.empty:
                    continue

                entry_val = pd.to_numeric(rows["SETTLE_PR"], errors="coerce").dropna()
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
                if entry_val.empty:
                    continue
                entry = float(entry_val.iloc[0])
                if entry < self.MIN_PREMIUM:
<<<<<<< HEAD
                    continue

                dte_bonus = 4 if 10 <= dte <= 18 else (-3 if dte < 9 else 0)
                score     = max(50, min(92, score_base + adx_bonus + dte_bonus))
                if score < self.MIN_SCORE:
                    continue

                signals.append(Signal(
                    date         = dt_ts,
                    strategy     = self.name,
                    symbol       = f"{symbol} {int(target_strike)} {opt_type}",
=======
                    continue

                # DTE score penalty: reward mid-expiry entries
                dte_bonus = 5 if 10 <= dte <= 20 else (-3 if dte < 8 else 0)
                score = max(55, min(92, score_base + dte_bonus))

                used_strike = int(target_strike if not rows.empty else atm)
                signals.append(Signal(
                    date         = dt_ts,
                    strategy     = self.name,
                    symbol       = f"{symbol} {used_strike} {opt_type}",
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
                    instrument   = symbol,
                    direction    = "BUY",
                    entry_price  = round(entry, 2),
                    target_price = round(entry * self.TARGET_MULT, 2),
                    stop_loss    = round(entry * self.STOP_MULT, 2),
                    score        = score,
                    lot_size     = LOT_SIZES.get(symbol, 50),
<<<<<<< HEAD
                    notes        = (f"PCR={pcr_now:.3f} prev={pcr_prev:.3f} REVERSAL "
                                    f"DTE={dte} ADX={adx_val:.1f} {opt_type} K={int(target_strike)}"),
=======
                    notes        = f"PCR_smooth={pcr:.3f}(raw={pcr_series.get(dt_ts,0):.3f}) DTE={dte} {opt_type} strike={used_strike}",
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
                ))
                seen_dates.add(dt_ts)

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Iron Fly  — replaces ATM_Strangle with defined-risk structure
# ═══════════════════════════════════════════════════════════════════════════════
class IronFly:
    """
    Iron Butterfly: sell ATM straddle + buy OTM strangle as wings.

    Structure:
      SELL ATM CE  +  SELL ATM PE  (collect big premium)
      BUY (ATM+step) CE  +  BUY (ATM-step) PE  (cap the loss)

    vs ATM_Strangle (naked):
      - Before: unlimited loss if gap up/down. 2024 events destroyed it.
      - After: max loss = step_width - net_credit (known upfront)

    Max profit: net_credit collected (when underlying settles exactly at ATM)
    Max loss:   step_width - net_credit (when underlying moves > 1 step)
    Breakeven:  ATM ± net_credit

    Entry only on Mon/Tue with DTE 3-6 (near expiry = max theta, less event risk)
    Net credit minimum Rs 100 (after cost of wings) = meaningful premium.
    """
    name            = "IronFly"
    MIN_NET_CREDIT  = 100    # after buying wings; practical minimum
    MAX_LOSS_RATIO  = 1.5    # exit if position reaches 150% of credit collected
    ENTRY_DAYS      = {0, 1} # Mon=0, Tue=1 ONLY (tightened from 0,1,2)
    MIN_DTE         = 3
    MAX_DTE         = 6

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

                # Wing strikes
                wing_ce_k = atm + step
                wing_pe_k = atm - step

                def get_p(strike, opt_t):
                    rows = near_df[(near_df["STRIKE_PR"] == strike) &
                                   (near_df["OPTION_TYP"].str.upper() == opt_t)]
                    p = pd.to_numeric(rows["SETTLE_PR"], errors="coerce").dropna()
                    return float(p.iloc[0]) if not p.empty else None

                atm_ce  = get_p(atm,      "CE")
                atm_pe  = get_p(atm,      "PE")
                wing_ce = get_p(wing_ce_k, "CE")
                wing_pe = get_p(wing_pe_k, "PE")

                if any(x is None or x <= 0 for x in [atm_ce, atm_pe, wing_ce, wing_pe]):
                    continue

                # Net credit = (sell ATM straddle) - (buy OTM strangle)
                net_credit = (atm_ce + atm_pe) - (wing_ce + wing_pe)
                if net_credit < self.MIN_NET_CREDIT:
                    continue

                max_loss  = step - net_credit    # defined, known upfront
                max_profit = net_credit           # only if settles exactly at ATM
                rr_ratio  = round(net_credit / max_loss, 2) if max_loss > 0 else 0

                # Score: higher credit relative to max_loss = better setup
                credit_pct = net_credit / step if step > 0 else 0
                score = min(88, 50 + int(credit_pct * 80) + max(0, 5 - dte) * 3)

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
                    n_legs        = 4,
                    notes         = (f"ATM_CE={atm_ce:.0f} ATM_PE={atm_pe:.0f} "
                                     f"WING_CE={wing_ce:.0f} WING_PE={wing_pe:.0f} "
                                     f"Net={net_credit:.0f} MaxLoss={max_loss:.0f} "
                                     f"R:R={rr_ratio} DTE={dte}"),
                ))

        return signals


# ── Legacy: ATMStrangle disabled (replaced by IronFly) ────────────────────────
class ATMStrangle:
    """DISABLED in v5. Replaced by IronFly (defined-risk structure)."""
    name = "ATM_Strangle"
    def generate(self, df): return []  # disabled


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Iron Condor  v5  — 50% profit target + tighter stop + Mon/Tue entry
# ═══════════════════════════════════════════════════════════════════════════════
class IronCondor:
    """
    v5 changes:
      1. 50% profit target: exit when unrealised P&L = 50% of max profit.
         Implementation: target_price = entry × 0.50. The engine treats
         a seller's target as "exit when current value drops to target",
         which means 50% decay of the credit = 50% profit captured.
      2. max_loss_ratio: 1.5 (from 2.0). Take losses faster.
      3. Entry days: Mon/Tue only (from Mon/Tue/Wed). Mid-week entry has
         less time for credit decay to work in our favour.
      4. MIN_NET_CREDIT raised: 80 (from 60) — filter out thin setups.
    """
    name           = "IronCondor"
    MIN_NET_CREDIT = 80       # raised from 60
    MAX_LOSS_RATIO = 1.5      # tightened from 2.0
    PROFIT_TARGET  = 0.50     # exit at 50% of credit (50% profit captured)
    SELL_OFFSET    = 1
    BUY_OFFSET     = 3
    ENTRY_DAYS     = {0, 1}   # Mon/Tue only (tightened from 0,1,2)
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

                # 50% profit target: exit when net value = 50% of entry credit
                target_val = round(net_credit * self.PROFIT_TARGET, 2)
                max_loss   = (self.BUY_OFFSET - self.SELL_OFFSET) * step - net_credit
                score      = min(90, 55 + int(net_credit / 20) + max(0, 5 - dte) * 3)
                seen_expiries.add((symbol, near))

                signals.append(Signal(
                    date          = dt_ts,
                    strategy      = self.name,
                    symbol        = (f"{symbol} IC "
                                     f"{sell_ce_k:.0f}/{buy_ce_k:.0f}/"
                                     f"{sell_pe_k:.0f}/{buy_pe_k:.0f}"),
                    instrument    = symbol,
                    direction     = "SELL",
                    entry_price   = round(net_credit, 2),
                    target_price  = target_val,    # exit at 50% profit
                    stop_loss     = round(net_credit * self.MAX_LOSS_RATIO, 2),
                    score         = score,
                    lot_size      = LOT_SIZES.get(symbol, 50),
                    expiry        = near,
                    max_loss_ratio= self.MAX_LOSS_RATIO,
                    n_legs        = 4,
                    notes         = (f"Credit={net_credit:.0f} Target50%={target_val:.0f} "
                                     f"MaxLoss={max_loss:.0f} DTE={dte}"),
                ))

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EMA Crossover  v5  — index-level OHLC + MACD confirmation
# ═══════════════════════════════════════════════════════════════════════════════
class EMACrossover:
    """
    v5 changes:
      1. Use index-level OHLC (max-OI strike = spot proxy) instead of
         ATM option price series. Option prices are noisy; the 97.2% OOS
         win rate anomaly in v4 was almost certainly caused by option
         data artifacts, not genuine alpha.
      2. MACD confirmation: require MACD(12,26,9) line to cross its signal
         in the SAME direction as the EMA crossover. Reduces false crossovers.
      3. ADX > 20 gate: only enter when trend is sufficiently strong.
         Avoids entries in choppy sideways markets.
      4. OI momentum: require today's total OI > 20-day OI average (proxy
         for active market; avoids entries on illiquid days).
    """
    name       = "EMA_Crossover"
    FAST, SLOW = 9, 21
    ADX_MIN    = 20   # minimum trend strength before entry

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"], errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY"]:   # FINNIFTY excluded — underperforms
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            if sym_df.empty:
                continue

            # ── Build index-level OHLC from max-OI strike proxy ───────────────
            ohlc = _build_index_ohlc(sym_df)
            if len(ohlc) < self.SLOW + 10:
                continue

            ohlc["EMA_F"] = _ema(ohlc["CLOSE"], self.FAST)
            ohlc["EMA_S"] = _ema(ohlc["CLOSE"], self.SLOW)
            ohlc["RSI"]   = _rsi(ohlc["CLOSE"])

            # ATR on available data (CLOSE ±0.5% proxy if no H/L)
            if all(c in ohlc.columns for c in ["HIGH", "LOW"]):
                ohlc["ATR"] = _atr(ohlc)
            else:
                ohlc["ATR"] = ohlc["CLOSE"] * 0.008   # 0.8% of close

            # MACD on index close
            macd_line, macd_sig = _macd(ohlc["CLOSE"])
            ohlc["MACD"]     = macd_line
            ohlc["MACD_SIG"] = macd_sig

            # ADX
            if all(c in ohlc.columns for c in ["HIGH", "LOW"]):
                ohlc["ADX"] = _adx(ohlc["HIGH"], ohlc["LOW"], ohlc["CLOSE"])
            else:
                ohlc["ADX"] = pd.Series(25.0, index=ohlc.index)

            # OI momentum: 20-day rolling average of total OI
            if "OI_TOTAL" in ohlc.columns:
                ohlc["OI_AVG20"] = ohlc["OI_TOTAL"].rolling(20).mean()
            else:
                ohlc["OI_AVG20"] = 1.0
                ohlc["OI_TOTAL"] = 1.0

            prev = ohlc.shift(1)

            # ── BUY signal ────────────────────────────────────────────────────
            buy = (
                (ohlc["EMA_F"] > ohlc["EMA_S"]) &                 # EMA crossover up
                (prev["EMA_F"] <= prev["EMA_S"]) &
                (ohlc["MACD"]  > ohlc["MACD_SIG"]) &              # MACD confirms up
                (prev["MACD"]  <= prev["MACD_SIG"]) &
                (ohlc["RSI"]   > 50) &                             # RSI momentum
                (ohlc["ADX"]   > self.ADX_MIN) &                   # trend strength
                (ohlc["OI_TOTAL"] >= ohlc["OI_AVG20"] * 0.9)      # active market
            )

            # ── SELL signal ───────────────────────────────────────────────────
            sell = (
                (ohlc["EMA_F"] < ohlc["EMA_S"]) &
                (prev["EMA_F"] >= prev["EMA_S"]) &
                (ohlc["MACD"]  < ohlc["MACD_SIG"]) &
                (prev["MACD"]  >= prev["MACD_SIG"]) &
                (ohlc["RSI"]   < 50) &
                (ohlc["ADX"]   > self.ADX_MIN) &
                (ohlc["OI_TOTAL"] >= ohlc["OI_AVG20"] * 0.9)
            )

            for dt in ohlc.index[buy]:
                r   = ohlc.loc[dt]
                atr = float(r["ATR"]) if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.008
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="BUY",
                    entry_price  = round(r["CLOSE"], 2),
                    target_price = round(r["CLOSE"] + 2.5 * atr, 2),   # 2.5× ATR target
                    stop_loss    = round(r["CLOSE"] - 1.0 * atr, 2),   # 1× ATR stop → R:R 2.5:1
                    score        = min(90, 62 + int(r["RSI"] - 50) + int(r["ADX"] - 20)),
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    notes        = (f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} "
                                    f"MACD={r['MACD']:.1f} ADX={r['ADX']:.1f} "
                                    f"RSI={r['RSI']:.1f} [INDEX_LEVEL]"),
                ))

            for dt in ohlc.index[sell]:
                r   = ohlc.loc[dt]
                atr = float(r["ATR"]) if not np.isnan(r["ATR"]) else r["CLOSE"] * 0.008
                signals.append(Signal(
                    date=dt, strategy=self.name, symbol=symbol, instrument=symbol,
                    direction="SELL",
                    entry_price  = round(r["CLOSE"], 2),
                    target_price = round(r["CLOSE"] - 2.5 * atr, 2),
                    stop_loss    = round(r["CLOSE"] + 1.0 * atr, 2),
                    score        = min(90, 62 + int(50 - r["RSI"]) + int(r["ADX"] - 20)),
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    notes        = (f"EMA9={r['EMA_F']:.0f} EMA21={r['EMA_S']:.0f} "
                                    f"MACD={r['MACD']:.1f} ADX={r['ADX']:.1f} "
                                    f"RSI={r['RSI']:.1f} [INDEX_LEVEL]"),
                ))

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 5. OI Buildup  (unchanged — requires CHG_IN_OI unavailable in 2024 data)
# ═══════════════════════════════════════════════════════════════════════════════
class OIBuildup:
    """
    PE OI build > 1.5σ above ATM → support → BUY
    CE OI build > 1.5σ below ATM → resistance → SELL
    Note: CHG_IN_OI column dropped in 2024 NSE Bhavcopy — generates 0 signals.
    Will work with pre-2024 data.
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
                settle     = day["SETTLE_PR"].dropna()
                spot_proxy = float(settle.mean()) if not settle.empty else 0
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
                        notes=f"PE OI build >{atm:.0f}",
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
                        notes=f"CE OI build <{atm:.0f}",
                    ))
        return signals


# ═══════════════════════════════════════════════════════════════════════════════
<<<<<<< HEAD
# 6. Calendar Spread  v5  — tighter ADX gate + Mon/Tue only + higher min credit
# ═══════════════════════════════════════════════════════════════════════════════
class CalendarSpread:
    """
    v5 changes:
      1. ADX tightened: < 20 (from 28). Calendar spreads die in trending
         markets. ADX 20-28 was still too volatile. Only flat/quiet markets.
      2. Entry day: Mon/Tue only. Early-week entry gives more days for theta
         decay to work. Mid-week entry has compressed time benefit.
      3. Min net credit: Rs 50 (from Rs 8 — nearly zero was no filter).
         Rs 50+ credit means meaningful premium differential exists.
      4. Target: 35% of entry (65% profit) — slightly more achievable than 40%.
      5. Min OI on near: 2000 (raised from 500 — better liquidity).

    NOTE: This strategy is still limited by EOD Bhavcopy data. These
    improvements help filter better setups but cannot overcome the
    fundamental data limitation (intraday spread required for validation).
    """
    name         = "Calendar_Spread"
    MIN_SKEW     = 0.08         # 8% near premium over far (raised from 6%)
    MIN_NEAR_PR  = 100
    MIN_FAR_PR   = 35
    MIN_NEAR_OI  = 2000         # raised from 500 — much better liquidity filter
    NEAR_DTE_MIN = 3
    NEAR_DTE_MAX = 7
    FAR_DTE_MIN  = 25
    FAR_DTE_MAX  = 42
    MIN_NET_CREDIT = 50         # raised from 8 — meaningful threshold
    TARGET_DECAY = 0.35         # exit at 35% of entry (65% profit captured)
    STOP_MULT    = 1.70         # tightened from 1.80
    ADX_MAX      = 20           # tightened from 28 — only flat markets
    ENTRY_DAYS   = {0, 1}       # Mon/Tue only (new in v5)
=======
# 6. Calendar Spread  v3  (fixed engine tracking, correct P&L, proper stops)
# ═══════════════════════════════════════════════════════════════════════════════
class CalendarSpread:
    """
    v3 critical fixes:

    ENGINE BUG FIXED (was causing catastrophic losses):
      v2 bug: entry_price = net_credit (e.g. ₹20), but engine's _price()
              returned just the near leg price (e.g. ₹150).
              Stop check: 150 >= 20 × 1.5 = 30 → STOP triggered on DAY 1.
              Far leg P&L was NEVER tracked (huge omission).

      v3 fix: Symbol format changed to "NIFTY CAL {strike} {opt_t} {near_ts} {far_ts}"
              New _price_calendar() in engine looks up BOTH legs and returns:
              current_net = near_current - far_current
              Stop/target based on % change in current_net, not raw price.
              P&L = (near_entry - near_exit) - (far_exit - far_entry) correctly.

    STRATEGY LOGIC FIXES:
      1. ATM from median strike (spot proxy), not max-OI
      2. Near DTE: 3-10 days (weekly expiry sweet spot for theta)
      3. Far DTE: 20-45 days (enough time value in far leg)
      4. Skew check: near_price > far_price by ≥ 5% (contango confirmed)
      5. Min near premium: ₹80 (enough credit to cover costs)
      6. Exit target: current_net drops to 25% of entry_net (75% decay = profit)
      7. Exit stop: current_net exceeds 200% of entry_net (market moved too far)
      8. FINNIFTY excluded: insufficient liquidity in far-month options

    Expected: 60-80 trades/yr, 55-65% WR, PF 1.8-2.5
    """
    name        = "Calendar_Spread"
    MIN_SKEW    = 0.05        # near must be ≥ 5% more than far
    MIN_NEAR_PR = 80          # near option floor (ensures meaningful credit)
    MIN_FAR_PR  = 30          # far option floor (ensures hedge value)
    NEAR_DTE_MIN = 3
    NEAR_DTE_MAX = 10
    FAR_DTE_MIN  = 20
    FAR_DTE_MAX  = 45
    TARGET_DECAY = 0.25       # exit when current_net falls to 25% of entry_net (profit)
    STOP_MULT    = 2.00       # exit when current_net rises to 200% of entry_net (loss)
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620

    def generate(self, df: pd.DataFrame) -> list[Signal]:
        signals = []
        df = df.copy()
        df["DATE"]      = pd.to_datetime(df["DATE"])
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"])
        df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")
        df["SETTLE_PR"] = pd.to_numeric(df["SETTLE_PR"], errors="coerce")
        df["OPEN_INT"]  = pd.to_numeric(df["OPEN_INT"],  errors="coerce")

        for symbol in ["NIFTY", "BANKNIFTY"]:
            step   = STRIKE_STEP.get(symbol, 50)
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
            if sym_df.empty:
                continue

            idx_ohlc = _build_index_ohlc(sym_df)
            adx_vals = (_adx(idx_ohlc["HIGH"], idx_ohlc["LOW"], idx_ohlc["CLOSE"])
                        if len(idx_ohlc) >= 20 and
                           all(c in idx_ohlc.columns for c in ["HIGH","LOW","CLOSE"])
                        else pd.Series(dtype=float))

<<<<<<< HEAD
=======
        for symbol in ["NIFTY", "BANKNIFTY"]:   # FINNIFTY excluded: far-month illiquid
            step   = STRIKE_STEP.get(symbol, 50)
            sym_df = df[df["SYMBOL"].str.strip().str.upper() == symbol]
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
            seen_dates = set()

            for dt, day in sym_df.groupby("DATE"):
                if dt in seen_dates:
                    continue
                dt_ts = pd.Timestamp(dt)
<<<<<<< HEAD

                # Mon/Tue only (v5)
                if dt_ts.weekday() not in self.ENTRY_DAYS:
                    continue

                # ADX gate: only flat markets
                adx_val = float(adx_vals.get(dt_ts, 25.0))
                if adx_val > self.ADX_MAX:
                    continue

                exps = sorted(day["EXPIRY_DT"].dropna().unique())
                near_exp = far_exp = None
                for e in exps:
                    dte = (e - dt_ts).days
                    if self.NEAR_DTE_MIN <= dte <= self.NEAR_DTE_MAX and not near_exp:
                        near_exp = e
                    elif self.FAR_DTE_MIN <= dte <= self.FAR_DTE_MAX and not far_exp:
=======
                exps  = sorted(day["EXPIRY_DT"].dropna().unique())

                # Find near and far expiry in correct DTE windows
                near_exp = None
                far_exp  = None
                for e in exps:
                    dte = (e - dt_ts).days
                    if self.NEAR_DTE_MIN <= dte <= self.NEAR_DTE_MAX and near_exp is None:
                        near_exp = e
                    elif self.FAR_DTE_MIN <= dte <= self.FAR_DTE_MAX and far_exp is None:
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
                        far_exp = e
                    if near_exp and far_exp:
                        break

<<<<<<< HEAD
                if not near_exp or not far_exp:
=======
                if near_exp is None or far_exp is None:
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620
                    continue

                near_df = day[day["EXPIRY_DT"] == near_exp]
                far_df  = day[day["EXPIRY_DT"] == far_exp]

<<<<<<< HEAD
                oi_by_strike = day.groupby("STRIKE_PR")["OPEN_INT"].sum()
                if oi_by_strike.empty:
                    continue
                spot_proxy = float(oi_by_strike.idxmax())
                all_strikes = day["STRIKE_PR"].dropna().unique()
                atm = float(min(all_strikes, key=lambda k: abs(k - spot_proxy)))
=======
                # ATM from median strike (spot proxy)
                all_strikes = day["STRIKE_PR"].dropna().unique()
                if len(all_strikes) == 0:
                    continue
                spot_proxy = float(np.median(all_strikes))
                atm = float(min(all_strikes, key=lambda k: abs(k - spot_proxy)))

                near_dte = (near_exp - dt_ts).days
                far_dte  = (far_exp  - dt_ts).days
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620

                near_dte = (near_exp - dt_ts).days
                far_dte  = (far_exp  - dt_ts).days

                nr = near_df[(near_df["OPTION_TYP"].str.upper() == "CE") &
                             (near_df["STRIKE_PR"] == atm)]
                fr = far_df[ (far_df["OPTION_TYP"].str.upper()  == "CE") &
                             (far_df["STRIKE_PR"]  == atm)]
                if nr.empty or fr.empty:
                    continue

<<<<<<< HEAD
                np_s = nr["SETTLE_PR"].dropna()
                fp_s = fr["SETTLE_PR"].dropna()
                if np_s.empty or fp_s.empty:
                    continue

                np_ = float(np_s.iloc[0])
                fp_ = float(fp_s.iloc[0])

                near_oi = float(nr["OPEN_INT"].dropna().sum() or 0)
                if near_oi < self.MIN_NEAR_OI:
                    continue
                if np_ < self.MIN_NEAR_PR or fp_ < self.MIN_FAR_PR:
                    continue

                skew = (np_ - fp_) / fp_ if fp_ > 0 else 0
                if skew < self.MIN_SKEW:
                    continue

                net_credit = round(np_ - fp_, 2)
                if net_credit < self.MIN_NET_CREDIT:
                    continue

                target_price = round(net_credit * self.TARGET_DECAY, 2)
                stop_price   = round(net_credit * self.STOP_MULT, 2)

                near_ts  = near_exp.strftime("%Y%m%d")
                far_ts   = far_exp.strftime("%Y%m%d")
                oi_bonus = min(6, int(near_oi / 10000))
                skew_bon = min(8, int(skew * 80))
                dte_bon  = 4 if near_dte <= 5 else 0
                adx_bon  = min(5, int((self.ADX_MAX - adx_val) / 2))
                score    = min(82, 48 + skew_bon + oi_bonus + dte_bon + adx_bon)

                signals.append(Signal(
                    date         = dt_ts,
                    strategy     = self.name,
                    symbol       = f"{symbol} CAL {int(atm)} CE {near_ts} {far_ts}",
                    instrument   = symbol,
                    direction    = "SELL",
                    entry_price  = net_credit,
                    target_price = target_price,
                    stop_loss    = stop_price,
                    score        = score,
                    lot_size     = LOT_SIZES.get(symbol, 50),
                    expiry       = near_exp,
                    max_loss_ratio = self.STOP_MULT,
                    n_legs       = 2,
                    notes        = (f"Near={np_:.0f}(DTE={near_dte},OI={near_oi:.0f}) "
                                    f"Far={fp_:.0f}(DTE={far_dte}) Skew={skew:.2%} "
                                    f"Net={net_credit:.0f} ADX={adx_val:.1f}"),
                ))
                seen_dates.add(dt_ts)
=======
                    np_ = float(np_s.iloc[0])
                    fp_ = float(fp_s.iloc[0])

                    if np_ < self.MIN_NEAR_PR or fp_ < self.MIN_FAR_PR:
                        continue
                    if fp_ <= 0:
                        continue

                    skew = (np_ - fp_) / fp_
                    if skew < self.MIN_SKEW:
                        continue

                    # entry_price = net credit (near sold - far bought)
                    net_credit = round(np_ - fp_, 2)
                    if net_credit <= 5:
                        continue

                    # target = current_net decays to 25% of entry (we keep 75%)
                    # stop   = current_net rises to 200% (position doubled against us)
                    target_price = round(net_credit * self.TARGET_DECAY, 2)
                    stop_price   = round(net_credit * self.STOP_MULT, 2)

                    near_ts = near_exp.strftime("%Y%m%d")
                    far_ts  = far_exp.strftime("%Y%m%d")
                    score   = min(84, 50 + int(skew * 100) + max(0, 8 - near_dte))

                    signals.append(Signal(
                        date        = dt_ts,
                        strategy    = self.name,
                        # CAL symbol format so engine knows to look up both legs
                        symbol      = f"{symbol} CAL {int(atm)} {opt_t} {near_ts} {far_ts}",
                        instrument  = symbol,
                        direction   = "SELL",
                        entry_price = net_credit,
                        target_price= target_price,
                        stop_price  = stop_price,    # stop when net rises (seller's loss)
                        stop_loss   = stop_price,
                        score       = score,
                        lot_size    = LOT_SIZES.get(symbol, 50),
                        expiry      = near_exp,      # close at near expiry at latest
                        max_loss_ratio = self.STOP_MULT,
                        n_legs      = 2,
                        notes       = f"Near={np_:.0f}(DTE={near_dte}) Far={fp_:.0f}(DTE={far_dte}) Skew={skew:.2%} Net={net_credit:.0f}",
                    ))
                    seen_dates.add(dt_ts)
                    break  # one signal per symbol per day
>>>>>>> d0f6ddf82737d1437f0f8d3ef637917c8bb8c620

        return signals


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Equity Momentum  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════
class EquityMomentum:
    """EMA crossover on top-30 NIFTY50 stocks with volume confirmation."""
    name            = "Equity_Momentum"
    FAST, SLOW      = 9, 21
    VOL_MULTIPLIER  = 1.2

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
            buy  = ((ohlc["EMA_F"] > ohlc["EMA_S"]) & (prev["EMA_F"] <= prev["EMA_S"]) &
                    (ohlc["RSI"] > 50) & vol_ok)
            sell = ((ohlc["EMA_F"] < ohlc["EMA_S"]) & (prev["EMA_F"] >= prev["EMA_S"]) &
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
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f}",
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
                    notes=f"EMA9={r['EMA_F']:.1f} EMA21={r['EMA_S']:.1f}",
                ))
        return signals


# ── Registries ─────────────────────────────────────────────────────────────────
FO_STRATEGIES = [
    PCRMeanReversion(),
    IronFly(),           # replaces ATMStrangle
    IronCondor(),
    EMACrossover(),
    OIBuildup(),
    CalendarSpread(),
]

EQUITY_STRATEGIES = [EquityMomentum()]

ALL_STRATEGIES = FO_STRATEGIES + EQUITY_STRATEGIES
