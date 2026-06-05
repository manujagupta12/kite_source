"""
gold_strategy.py
================
Gold (XAUUSD) trading strategies for Delta Exchange.

Three strategies:
  S1 - Gold Momentum (EMA crossover + RSI)
  S2 - Gold Breakout (key level break + volume surge)
  S3 - Gold Mean Reversion (RSI extreme + BB squeeze)

Data source: Delta Exchange public API (no auth needed for prices).
Signals are generated in USD since Gold is priced in USD on Delta.
"""

import time
import math
from typing import Optional
from datetime import datetime, timezone

# ── Indicator helpers ─────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return [None] * len(values)
    result = [None] * (period - 1)
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    result.append(ema)
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result

def _rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return [None] * len(closes)
    result = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    rsi = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100
    result.append(round(rsi, 2))
    for i in range(period + 1, len(closes)):
        diff  = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(diff, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-diff, 0)) / period
        rsi   = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100
        result.append(round(rsi, 2))
    return result

def _atr(candles: list, period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"];  l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 4)

def _bollinger(closes: list, period: int = 20, std: float = 2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid    = sum(window) / period
    var    = sum((x - mid) ** 2 for x in window) / period
    sigma  = math.sqrt(var)
    return round(mid - std * sigma, 2), round(mid, 2), round(mid + std * sigma, 2)

def _vwap(candles: list) -> Optional[float]:
    pv = tp = 0
    for c in candles:
        tp_val = (c["high"] + c["low"] + c["close"]) / 3
        pv    += tp_val * c["volume"]
        tp    += c["volume"]
    return round(pv / tp, 2) if tp else None


# ── Strategy 1: Gold Momentum (EMA 9/21 + RSI 14) ─────────────

def s1_gold_momentum(candles: list) -> Optional[dict]:
    """
    Signal when:
      - EMA(9) crosses above/below EMA(21)
      - RSI(14) confirms direction (>55 for LONG, <45 for SHORT)
      - Not overbought/oversold (RSI 40-70 zone only)

    Best timeframe: 5m or 15m candles.
    """
    if len(candles) < 25:
        return None
    closes = [c["close"] for c in candles]
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    rsi    = _rsi(closes, 14)

    if not all([ema9[-1], ema9[-2], ema21[-1], ema21[-2], rsi[-1]]):
        return None

    curr_ltp = closes[-1]
    atr_val  = _atr(candles, 14) or 0

    # Crossover detection
    was_above = ema9[-2] > ema21[-2]
    is_above  = ema9[-1] > ema21[-1]
    crossover = is_above != was_above

    if not crossover:
        return None

    rsi_now = rsi[-1]
    if is_above:
        direction = "LONG"
        if rsi_now < 45 or rsi_now > 72:
            return None
        score = int(50 + (rsi_now - 50) * 1.2 + (1 if atr_val > 2 else 0) * 5)
    else:
        direction = "SHORT"
        if rsi_now > 55 or rsi_now < 28:
            return None
        score = int(50 + (50 - rsi_now) * 1.2 + (1 if atr_val > 2 else 0) * 5)

    score = min(90, max(50, score))
    if score < 55:
        return None

    sl   = round(atr_val * 1.5, 2) if atr_val else round(curr_ltp * 0.005, 2)
    tgt  = round(sl * 2.0, 2)
    side = "BUY" if direction == "LONG" else "SELL"

    return {
        "strategy":   "S1 GOLD MOMENTUM",
        "direction":  direction,
        "score":      score,
        "ltp":        round(curr_ltp, 2),
        "ema9":       round(ema9[-1], 2),
        "ema21":      round(ema21[-1], 2),
        "rsi":        rsi_now,
        "atr":        round(atr_val, 2),
        "sl_pts":     sl,
        "target_pts": tgt,
        "rr_ratio":   "2:1",
        "entry":      f"{side} XAUUSD @ ${curr_ltp:,.2f}",
        "stop_loss":  round(curr_ltp - sl if direction=="LONG" else curr_ltp + sl, 2),
        "target":     round(curr_ltp + tgt if direction=="LONG" else curr_ltp - tgt, 2),
        "reason":     f"EMA9({ema9[-1]:.1f}) crossed {'above' if is_above else 'below'} EMA21({ema21[-1]:.1f}) | RSI={rsi_now:.1f}",
        "risk":       "MEDIUM",
    }


# ── Strategy 2: Gold Breakout ──────────────────────────────────

def s2_gold_breakout(candles: list) -> Optional[dict]:
    """
    Signal when price breaks above/below a consolidation range
    with above-average volume.

    Consolidation = last 10 candles trading within 0.3% range.
    Break = price moves >0.4% outside that range.
    Volume = current volume > 1.5x 10-bar average.
    """
    if len(candles) < 20:
        return None

    lookback  = candles[-12:-2]   # consolidation window (excluding last 2)
    last_two  = candles[-2:]      # breakout candles

    hi   = max(c["high"]  for c in lookback)
    lo   = min(c["low"]   for c in lookback)
    mid  = (hi + lo) / 2
    rng  = (hi - lo) / mid if mid else 0
    if rng > 0.008:               # range too wide — no consolidation
        return None

    curr_close = candles[-1]["close"]
    prev_close = candles[-2]["close"]
    move       = (curr_close - prev_close) / prev_close

    # Volume check
    avg_vol = sum(c["volume"] for c in candles[-12:-2]) / 10
    cur_vol = candles[-1]["volume"]
    vol_surge = (cur_vol / avg_vol) if avg_vol else 0

    if vol_surge < 1.4:
        return None

    atr_val = _atr(candles, 14) or round(curr_close * 0.005, 2)

    if curr_close > hi and move > 0.003:
        direction = "LONG"
        score     = int(60 + min(20, vol_surge * 8))
        entry_txt = f"BUY XAUUSD @ ${curr_close:,.2f} (break above ${hi:,.2f})"
    elif curr_close < lo and move < -0.003:
        direction = "SHORT"
        score     = int(60 + min(20, vol_surge * 8))
        entry_txt = f"SELL XAUUSD @ ${curr_close:,.2f} (break below ${lo:,.2f})"
    else:
        return None

    score = min(88, score)
    sl    = round(atr_val * 1.8, 2)
    tgt   = round(sl * 2.2, 2)

    return {
        "strategy":     "S2 GOLD BREAKOUT",
        "direction":    direction,
        "score":        score,
        "ltp":          round(curr_close, 2),
        "range_high":   round(hi,  2),
        "range_low":    round(lo,  2),
        "range_pct":    round(rng * 100, 3),
        "vol_surge":    round(vol_surge, 2),
        "atr":          round(atr_val, 2),
        "sl_pts":       sl,
        "target_pts":   tgt,
        "rr_ratio":     "2.2:1",
        "entry":        entry_txt,
        "stop_loss":    round(curr_close - sl if direction=="LONG" else curr_close + sl, 2),
        "target":       round(curr_close + tgt if direction=="LONG" else curr_close - tgt, 2),
        "reason":       f"Breakout from ${lo:,.2f}-${hi:,.2f} range | Vol surge {vol_surge:.1f}x",
        "risk":         "MEDIUM-HIGH",
    }


# ── Strategy 3: Gold Mean Reversion ───────────────────────────

def s3_gold_mean_reversion(candles: list) -> Optional[dict]:
    """
    Signal when:
      - Price hits Bollinger Band extreme (outer band)
      - RSI is oversold (<32) or overbought (>68)
      - Price is >0.8% from VWAP

    Counter-trend setup — lower score, tighter stops.
    Best for ranging gold markets (VIX < 15).
    """
    if len(candles) < 22:
        return None

    closes    = [c["close"] for c in candles]
    curr      = closes[-1]
    rsi       = _rsi(closes, 14)
    rsi_now   = rsi[-1] if rsi[-1] else 50
    bb_lo, bb_mid, bb_hi = _bollinger(closes, 20, 2.0)
    vwap      = _vwap(candles[-30:]) or curr
    atr_val   = _atr(candles, 14) or round(curr * 0.005, 2)

    if not (bb_lo and bb_hi):
        return None

    vwap_dev = (curr - vwap) / vwap if vwap else 0

    if curr <= bb_lo and rsi_now < 33 and vwap_dev < -0.006:
        direction = "LONG"
        score     = int(55 + (33 - rsi_now) * 0.8)
        reason    = f"Price at BB lower ({bb_lo:,.2f}), RSI={rsi_now:.1f} oversold, VWAP dev {vwap_dev*100:.2f}%"
    elif curr >= bb_hi and rsi_now > 67 and vwap_dev > 0.006:
        direction = "SHORT"
        score     = int(55 + (rsi_now - 67) * 0.8)
        reason    = f"Price at BB upper ({bb_hi:,.2f}), RSI={rsi_now:.1f} overbought, VWAP dev {vwap_dev*100:.2f}%"
    else:
        return None

    score = min(80, max(52, score))
    sl    = round(atr_val * 1.2, 2)
    tgt   = round(abs(curr - vwap) * 0.7, 2)   # target = 70% return to VWAP

    return {
        "strategy":     "S3 GOLD MEAN REVERSION",
        "direction":    direction,
        "score":        score,
        "ltp":          round(curr, 2),
        "bb_upper":     bb_hi,
        "bb_lower":     bb_lo,
        "bb_mid":       bb_mid,
        "rsi":          rsi_now,
        "vwap":         vwap,
        "vwap_dev_pct": round(vwap_dev * 100, 3),
        "atr":          round(atr_val, 2),
        "sl_pts":       sl,
        "target_pts":   tgt,
        "rr_ratio":     "variable",
        "entry":        f"{'BUY' if direction=='LONG' else 'SELL'} XAUUSD @ ${curr:,.2f}",
        "stop_loss":    round(curr - sl if direction=="LONG" else curr + sl, 2),
        "target":       vwap,
        "reason":       reason,
        "risk":         "LOW-MEDIUM",
    }


# ── Master signal generator ───────────────────────────────────

def generate_gold_signals(candles_5m: list, candles_15m: list = None) -> list:
    """
    Run all three strategies and return validated signals.
    candles_5m: 5-minute candles (100 bars recommended)
    candles_15m: 15-minute candles for S1 confirmation (optional)
    """
    signals = []
    now_ts  = datetime.now(timezone.utc).isoformat()
    spot    = candles_5m[-1]["close"] if candles_5m else 0

    for strategy_fn, candles, tf in [
        (s1_gold_momentum,    candles_15m or candles_5m, "15m"),
        (s2_gold_breakout,    candles_5m,                "5m"),
        (s3_gold_mean_reversion, candles_5m,             "5m"),
    ]:
        try:
            sig = strategy_fn(candles)
            if sig and sig.get("score", 0) >= 55:
                sig.update({
                    "timestamp":  now_ts,
                    "market":     "COMMODITY",
                    "instrument": "XAUUSD",
                    "symbol":     "GOLD",
                    "spot":       round(spot, 2),
                    "timeframe":  tf,
                    "source":     "delta_live",
                    "event_type": "signal",
                    "is_live":    True,
                    "regime":     "LIVE",
                    "lots_suggested": 1,
                    "exchange":   "Delta Exchange India",
                })
                signals.append(sig)
        except Exception as e:
            import logging
            logging.debug(f"[Gold] {strategy_fn.__name__} error: {e}")

    return signals
