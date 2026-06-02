"""
regime_filter.py — Market Regime Detection
==========================================
Classifies market into: TRENDING_UP, TRENDING_DOWN, RANGING, HIGH_VOL
Used by all strategies to gate entries.

Regime logic:
  - ADX > 25 + EMA slope positive  → TRENDING_UP
  - ADX > 25 + EMA slope negative  → TRENDING_DOWN
  - ADX < 20                        → RANGING
  - IVP > 80                        → HIGH_VOL (overlay)
"""

import numpy as np
import pandas as pd
from enum import Enum


class Regime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOL = "HIGH_VOL"
    UNKNOWN = "UNKNOWN"


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    atr = tr.ewm(span=period, adjust=False).mean()

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100 * pd.Series(plus_dm, index=close.index).ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=close.index).ewm(span=period, adjust=False).mean() / atr

    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di)).fillna(0)
    adx_val = dx.ewm(span=period, adjust=False).mean()
    return adx_val, plus_di, minus_di


def detect_regime(
    ohlcv: pd.DataFrame,
    ivp: float = None,  # IV Percentile 0-100
    adx_period: int = 14,
    ema_period: int = 50,
    adx_trend_threshold: float = 25.0,
    adx_range_threshold: float = 20.0,
    ivp_high_threshold: float = 80.0,
) -> Regime:
    """
    Returns the current market regime based on the last row of OHLCV data.

    Parameters
    ----------
    ohlcv : DataFrame with columns [open, high, low, close, volume]
    ivp   : Current IV Percentile (0-100). If None, HIGH_VOL not used.
    """
    if len(ohlcv) < max(adx_period * 2, ema_period) + 5:
        return Regime.UNKNOWN

    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]

    adx_val, plus_di, minus_di = adx(high, low, close, adx_period)
    ema_val = ema(close, ema_period)

    cur_adx = adx_val.iloc[-1]
    cur_ema = ema_val.iloc[-1]
    cur_close = close.iloc[-1]

    # Slope: EMA 5-bar rate of change
    ema_slope = (ema_val.iloc[-1] - ema_val.iloc[-6]) / ema_val.iloc[-6] * 100

    if ivp is not None and ivp >= ivp_high_threshold:
        return Regime.HIGH_VOL

    if cur_adx >= adx_trend_threshold:
        if ema_slope > 0 and cur_close > cur_ema:
            return Regime.TRENDING_UP
        elif ema_slope < 0 and cur_close < cur_ema:
            return Regime.TRENDING_DOWN

    if cur_adx < adx_range_threshold:
        return Regime.RANGING

    # Weak trend — treat as ranging
    return Regime.RANGING


def iv_percentile(iv_series: pd.Series, lookback: int = 252) -> float:
    """Compute current IV Percentile over last `lookback` days."""
    recent = iv_series.iloc[-lookback:]
    current = iv_series.iloc[-1]
    return float((recent < current).mean() * 100)


def iv_rank(iv_series: pd.Series, lookback: int = 52) -> float:
    """Compute IV Rank: (current - min) / (max - min) × 100."""
    recent = iv_series.iloc[-lookback:]
    mn, mx = recent.min(), recent.max()
    if mx == mn:
        return 50.0
    return float((iv_series.iloc[-1] - mn) / (mx - mn) * 100)
