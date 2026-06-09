"""
mcx_strategy.py
===============
MCX commodity signal module — additive, no F&O logic touched.

Instruments : GOLD, SILVER, CRUDEOIL, COPPER, NATURALGAS
Data source : Yahoo Finance public REST (no auth, same pattern as main.py indices)
Strategies  :
  S1 MCX TREND    — EMA 9/21 crossover + RSI(14) confirmation (5m candles)
  S2 MCX CALENDAR — Near vs far-month spread vs historical mean (daily data)

All signals carry:
  market   = "COMMODITY"
  exchange = "MCX"
  source   = "mcx_live"
  is_live  = True

Update _CALENDAR_PAIRS quarterly when MCX contracts roll.
"""

import math
import time
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, List, Dict

# ── Instrument config ─────────────────────────────────────────────────────────

_YF_TICKERS: Dict[str, str] = {
    "GOLD":       "GC=F",
    "SILVER":     "SI=F",
    "CRUDEOIL":   "CL=F",
    "COPPER":     "HG=F",
    "NATURALGAS": "NG=F",
}

# Near / far month Yahoo Finance tickers for calendar spread.
# Update the far-month ticker each quarter as contracts roll.
# Format: CME ticker + expiry code + exchange suffix
_CALENDAR_PAIRS: Dict[str, tuple] = {
    "GOLD":       ("GC=F", "GCZ26.CMX"),
    "SILVER":     ("SI=F", "SIN26.CMX"),
    "CRUDEOIL":   ("CL=F", "CLZ26.NYM"),
    "COPPER":     ("HG=F", "HGZ26.CMX"),
    "NATURALGAS": ("NG=F", "NGZ26.NYM"),
}

# MCX standard lot sizes (for display / position sizing)
MCX_LOT_SIZES: Dict[str, int] = {
    "GOLD":       10,    # 10 grams
    "SILVER":     30,    # 30 kg (mini)
    "CRUDEOIL":   100,   # 100 barrels
    "COPPER":     1000,  # 1000 kg (mini)
    "NATURALGAS": 1250,  # 1250 mmBtu
}

MCX_UNITS: Dict[str, str] = {
    "GOLD":       "USD/oz",
    "SILVER":     "USD/oz",
    "CRUDEOIL":   "USD/bbl",
    "COPPER":     "USD/lb",
    "NATURALGAS": "USD/mmBtu",
}

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
_YF_TIMEOUT = 8


# ── Yahoo Finance OHLCV fetcher ───────────────────────────────────────────────

def _fetch_ohlcv(ticker: str, interval: str = "5m", range_: str = "1d") -> List[dict]:
    """
    Fetch OHLCV candles from Yahoo Finance v8 chart API.
    Returns list of {time, open, high, low, close, volume} sorted ascending.
    """
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?interval={interval}&range={range_}")
    try:
        r = _session.get(url, timeout=_YF_TIMEOUT)
        if r.status_code != 200:
            return []
        data = r.json()
        result = data["chart"]["result"]
        if not result:
            return []
        ts_list  = result[0]["timestamp"]
        q        = result[0]["indicators"]["quote"][0]
        opens    = q.get("open")  or []
        highs    = q.get("high")  or []
        lows     = q.get("low")   or []
        closes   = q.get("close") or []
        vols     = q.get("volume") or []
        candles  = []
        for i, ts in enumerate(ts_list):
            try:
                o = float(opens[i]  or 0)
                h = float(highs[i]  or 0)
                l = float(lows[i]   or 0)
                c = float(closes[i] or 0)
                v = float(vols[i]   or 0) if i < len(vols) else 0
                if c > 0:
                    candles.append({"time": ts, "open": o, "high": h,
                                    "low": l, "close": c, "volume": v})
            except (TypeError, ValueError):
                continue
        return candles
    except Exception as e:
        logging.debug(f"[MCX] YF fetch error {ticker}: {e}")
        return []


def _fetch_ltp(ticker: str) -> Optional[float]:
    """Fetch a single current price for a ticker via Yahoo Finance."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
    try:
        r = _session.get(url, timeout=_YF_TIMEOUT)
        if r.status_code != 200:
            return None
        meta = r.json()["chart"]["result"][0]["meta"]
        return float(meta.get("regularMarketPrice") or meta.get("previousClose") or 0) or None
    except Exception:
        return None


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    if len(values) < period:
        return [None] * len(values)
    k      = 2.0 / (period + 1)
    result = [None] * (period - 1)
    ema    = sum(values[:period]) / period
    result.append(ema)
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result


def _rsi(closes: list, period: int = 14) -> list:
    if len(closes) < period + 1:
        return [None] * len(closes)
    result = [None] * period
    diffs  = [closes[i] - closes[i-1] for i in range(1, period + 1)]
    avg_g  = sum(max(d, 0) for d in diffs) / period
    avg_l  = sum(max(-d, 0) for d in diffs) / period
    rsi    = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100.0
    result.append(round(rsi, 2))
    for i in range(period + 1, len(closes)):
        d      = closes[i] - closes[i - 1]
        avg_g  = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l  = (avg_l * (period - 1) + max(-d, 0)) / period
        rsi    = 100 - (100 / (1 + avg_g / avg_l)) if avg_l else 100.0
        result.append(round(rsi, 2))
    return result


def _atr(candles: list, period: int = 14) -> Optional[float]:
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]; l = candles[i]["low"]; pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return round(sum(trs[-period:]) / period, 4)


# ── S1 MCX TREND (EMA 9/21 crossover + RSI confirmation) ─────────────────────

def s1_mcx_trend(candles: list, instrument: str) -> Optional[dict]:
    """
    Signal when EMA9 crosses EMA21 AND RSI confirms direction.
    Identical logic to gold_strategy S1 — proven, battle-tested.
    """
    if len(candles) < 25:
        return None
    closes = [c["close"] for c in candles]
    ema9   = _ema(closes, 9)
    ema21  = _ema(closes, 21)
    rsi    = _rsi(closes, 14)

    if not all([ema9[-1], ema9[-2], ema21[-1], ema21[-2], rsi[-1]]):
        return None

    ltp     = closes[-1]
    atr_val = _atr(candles, 14) or 0

    was_above = ema9[-2] > ema21[-2]
    is_above  = ema9[-1] > ema21[-1]
    if is_above == was_above:
        return None   # no crossover

    rsi_now = rsi[-1]
    if is_above:
        direction = "LONG"
        if not (45 <= rsi_now <= 72):
            return None
        score = int(50 + (rsi_now - 50) * 1.2 + (5 if atr_val > 0 else 0))
    else:
        direction = "SHORT"
        if not (28 <= rsi_now <= 55):
            return None
        score = int(50 + (50 - rsi_now) * 1.2 + (5 if atr_val > 0 else 0))

    score = min(90, max(52, score))
    if score < 55:
        return None

    sl_pts  = round(atr_val * 1.5, 4) if atr_val else round(ltp * 0.006, 4)
    tgt_pts = round(sl_pts * 2.0, 4)
    unit    = MCX_UNITS.get(instrument, "")

    return {
        "strategy":   "S1 MCX TREND",
        "direction":  direction,
        "score":      score,
        "ltp":        round(ltp, 4),
        "ema9":       round(ema9[-1], 4),
        "ema21":      round(ema21[-1], 4),
        "rsi":        rsi_now,
        "atr":        round(atr_val, 4),
        "sl_pts":     sl_pts,
        "target_pts": tgt_pts,
        "rr_ratio":   "2:1",
        "stop_loss":  round(ltp - sl_pts if direction == "LONG" else ltp + sl_pts, 4),
        "target":     round(ltp + tgt_pts if direction == "LONG" else ltp - tgt_pts, 4),
        "entry":      f"{'BUY' if direction=='LONG' else 'SELL'} {instrument} @ {ltp:.4f} {unit}",
        "reason": (f"EMA9({ema9[-1]:.2f}) crossed {'above' if is_above else 'below'} "
                   f"EMA21({ema21[-1]:.2f}) | RSI={rsi_now:.1f}"),
        "risk": "MEDIUM",
    }


# ── S2 MCX CALENDAR (near vs far-month spread) ────────────────────────────────

def s2_mcx_calendar(instrument: str, near_ticker: str, far_ticker: str) -> Optional[dict]:
    """
    Fetch near and far month daily prices, compute current spread vs 20-day mean.
    Signal fires when spread deviates >1.5 σ from its 20-day mean.

    Positive spread (far > near) = normal contango.
    Signal LONG spread (buy near, sell far) when spread is unusually WIDE (expensive carry).
    Signal SHORT spread (sell near, buy far) when spread is unusually NARROW / backwardated.
    """
    near_candles = _fetch_ohlcv(near_ticker, interval="1d", range_="3mo")
    far_candles  = _fetch_ohlcv(far_ticker,  interval="1d", range_="3mo")

    if len(near_candles) < 22 or len(far_candles) < 5:
        return None

    # Align on common timestamps (daily, use close prices)
    near_map = {c["time"]: c["close"] for c in near_candles}
    far_map  = {c["time"]: c["close"] for c in far_candles}
    common   = sorted(set(near_map) & set(far_map))

    if len(common) < 20:
        return None

    # Compute spread series: far - near (contango = positive)
    spreads = [far_map[t] - near_map[t] for t in common]

    mean    = sum(spreads) / len(spreads)
    var     = sum((x - mean) ** 2 for x in spreads) / len(spreads)
    std_dev = math.sqrt(var) if var > 0 else 0

    if std_dev == 0:
        return None

    current_spread = spreads[-1]
    z_score        = (current_spread - mean) / std_dev
    near_ltp       = near_map[common[-1]]
    far_ltp        = far_map[common[-1]]
    unit           = MCX_UNITS.get(instrument, "")

    # Signal only when spread is meaningfully dislocated
    if abs(z_score) < 1.5:
        return None

    if z_score > 1.5:
        # Spread unusually wide (expensive carry) → LONG spread: sell far, buy near
        direction = "LONG"
        reason    = (f"Spread {current_spread:+.3f} {unit} is {z_score:.1f}σ above mean "
                     f"({mean:+.3f}) — carry too expensive, expect compression")
        entry     = f"BUY near {instrument} @ {near_ltp:.3f} | SELL far @ {far_ltp:.3f}"
    else:
        # Spread unusually narrow / backwardated → SHORT spread: buy far, sell near
        direction = "SHORT"
        reason    = (f"Spread {current_spread:+.3f} {unit} is {z_score:.1f}σ below mean "
                     f"({mean:+.3f}) — backwardation, expect spread to widen")
        entry     = f"SELL near {instrument} @ {near_ltp:.3f} | BUY far @ {far_ltp:.3f}"

    score = min(88, int(55 + abs(z_score) * 10))
    sl_pts  = round(std_dev * 0.8, 4)
    tgt_pts = round(abs(current_spread - mean) * 0.7, 4)

    return {
        "strategy":       "S2 MCX CALENDAR",
        "direction":      direction,
        "score":          score,
        "ltp":            round(near_ltp, 4),
        "spread":         round(current_spread, 4),
        "spread_mean":    round(mean, 4),
        "spread_std":     round(std_dev, 4),
        "z_score":        round(z_score, 2),
        "near_ltp":       round(near_ltp, 4),
        "far_ltp":        round(far_ltp, 4),
        "near_ticker":    near_ticker,
        "far_ticker":     far_ticker,
        "sl_pts":         sl_pts,
        "target_pts":     tgt_pts,
        "rr_ratio":       f"{round(tgt_pts/sl_pts,1)}:1" if sl_pts else "—",
        "entry":          entry,
        "reason":         reason,
        "risk":           "MEDIUM" if abs(z_score) < 2.5 else "HIGH",
    }


# ── Master generator — all instruments ────────────────────────────────────────

def generate_mcx_signals(instruments: list = None) -> List[dict]:
    """
    Run S1 TREND and S2 CALENDAR for each instrument.
    instruments: subset list, default = all 5.
    Returns validated signals with full COMMODITY metadata.
    """
    instruments = instruments or list(_YF_TICKERS.keys())
    signals     = []
    now_ts      = datetime.now(timezone.utc).isoformat()

    for inst in instruments:
        ticker = _YF_TICKERS.get(inst)
        if not ticker:
            continue

        # ── S1 MCX TREND (5m candles, same session) ──────────────────────
        try:
            candles = _fetch_ohlcv(ticker, interval="5m", range_="1d")
            if len(candles) >= 25:
                sig = s1_mcx_trend(candles, inst)
                if sig and sig.get("score", 0) >= 55:
                    sig.update({
                        "timestamp":       now_ts,
                        "market":          "COMMODITY",
                        "exchange":        "MCX",
                        "instrument":      inst,
                        "symbol":          inst,
                        "timeframe":       "5m",
                        "source":          "mcx_live",
                        "event_type":      "signal",
                        "is_live":         True,
                        "regime":          "LIVE",
                        "lots_suggested":  1,
                        "lot_size":        MCX_LOT_SIZES.get(inst, 1),
                        "unit":            MCX_UNITS.get(inst, ""),
                    })
                    signals.append(sig)
        except Exception as e:
            logging.debug(f"[MCX] S1 TREND {inst}: {e}")

        # ── S2 MCX CALENDAR (daily, near/far month) ───────────────────────
        try:
            near_tk, far_tk = _CALENDAR_PAIRS.get(inst, (None, None))
            if near_tk and far_tk:
                sig = s2_mcx_calendar(inst, near_tk, far_tk)
                if sig and sig.get("score", 0) >= 55:
                    sig.update({
                        "timestamp":       now_ts,
                        "market":          "COMMODITY",
                        "exchange":        "MCX",
                        "instrument":      inst,
                        "symbol":          inst,
                        "timeframe":       "1d",
                        "source":          "mcx_live",
                        "event_type":      "signal",
                        "is_live":         True,
                        "regime":          "LIVE",
                        "lots_suggested":  1,
                        "lot_size":        MCX_LOT_SIZES.get(inst, 1),
                        "unit":            MCX_UNITS.get(inst, ""),
                    })
                    signals.append(sig)
        except Exception as e:
            logging.debug(f"[MCX] S2 CALENDAR {inst}: {e}")

    return signals


def get_mcx_prices() -> Dict[str, dict]:
    """
    Fetch current LTP for all 5 instruments.
    Returns {instrument: {ltp, change_pct, unit}} dict.
    """
    result = {}
    hdr = {"User-Agent": "Mozilla/5.0"}
    for inst, ticker in _YF_TICKERS.items():
        try:
            url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                   f"?interval=1m&range=1d")
            r   = _session.get(url, timeout=_YF_TIMEOUT)
            if r.status_code != 200:
                continue
            meta  = r.json()["chart"]["result"][0]["meta"]
            ltp   = float(meta.get("regularMarketPrice") or 0)
            prev  = float(meta.get("previousClose") or ltp)
            chp   = round((ltp - prev) / prev * 100 if prev else 0, 2)
            high  = float(meta.get("regularMarketDayHigh") or ltp)
            low   = float(meta.get("regularMarketDayLow")  or ltp)
            result[inst] = {
                "instrument":  inst,
                "ticker":      ticker,
                "ltp":         round(ltp, 4),
                "change_pct":  chp,
                "high":        round(high, 4),
                "low":         round(low, 4),
                "unit":        MCX_UNITS.get(inst, ""),
                "lot_size":    MCX_LOT_SIZES.get(inst, 1),
                "_ts":         int(time.time()),
            }
        except Exception as e:
            logging.debug(f"[MCX] Price fetch {inst}: {e}")
    return result
