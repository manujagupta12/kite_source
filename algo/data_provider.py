"""
data_provider.py
================
Unified data interface for AlgoTrade.
Replaces Multitrade_loader.py as the single source of truth.

Data sources (all free, no paid API):
  LIVE    — NSE Direct API (nseindia.com JSON)
  TICKS   — Dhan WebSocket (optional, free with Dhan account)
  HISTORY — NSE Bhavcopy archives (EOD, 10yr+)

Designed to be a drop-in replacement for multitrade_loader.
All function signatures match the old loader for backward compatibility.

Usage:
    from algo.data_provider import get_instruments, get_spread, get_atm_strike
    # Same API as the old multitrade_loader
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from typing import Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from algo.nse_fetcher   import NSEFetcher, get_fetcher
from algo.dhan_ticker   import get_tick_store, TickStore
from algo.bhavcopy_loader import BhavcopyCatalog, get_catalog

# ── Configuration ─────────────────────────────────────────────
DEFAULT_SYMBOL      = "BANKNIFTY"
STRIKE_MIN          = 40000
STRIKE_MAX          = 70000

# Near/far expiry index (0 = nearest, 1 = next)
NEAR_EXPIRY_IDX     = 0
FAR_EXPIRY_IDX      = 1

# Cache of last fetched chain to avoid hammering NSE on every call
_chain_cache: dict = {}
_chain_cache_ts: float = 0
_CACHE_TTL_SEC: float  = 2.0   # Refresh every 2s max

# Shared instances
_fetcher: NSEFetcher   = None
_tick_store: TickStore = None
_catalog: BhavcopyCatalog = None


def _get_fetcher() -> NSEFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = get_fetcher()
    return _fetcher

def _get_tick_store() -> TickStore:
    global _tick_store
    if _tick_store is None:
        _tick_store = get_tick_store()
    return _tick_store

def _get_catalog() -> BhavcopyCatalog:
    global _catalog
    if _catalog is None:
        _catalog = get_catalog()
    return _catalog


# ── Internal chain cache ──────────────────────────────────────

def _get_chain(
    symbol:  str = DEFAULT_SYMBOL,
    near_idx: int = NEAR_EXPIRY_IDX,
    far_idx:  int = FAR_EXPIRY_IDX,
) -> dict:
    """
    Fetch (or return cached) option chain.
    Returns dict: {'near': chain_dict, 'far': chain_dict,
                   'near_expiry': str, 'far_expiry': str,
                   'underlying': float, 'expiry_dates': list}
    """
    global _chain_cache, _chain_cache_ts

    now = time.time()
    if now - _chain_cache_ts < _CACHE_TTL_SEC and _chain_cache:
        return _chain_cache

    fetcher = _get_fetcher()
    expiries = fetcher.get_expiry_dates(symbol)
    if not expiries:
        return _chain_cache  # return stale if available

    near_expiry = expiries[near_idx] if len(expiries) > near_idx else expiries[0]
    far_expiry  = expiries[far_idx]  if len(expiries) > far_idx  else expiries[-1]

    near_result = fetcher.get_option_chain(symbol, expiry=near_expiry)
    far_result  = fetcher.get_option_chain(symbol, expiry=far_expiry)

    _chain_cache = {
        "near":         near_result.get("chain", {}),
        "far":          far_result.get("chain",  {}),
        "near_expiry":  near_expiry,
        "far_expiry":   far_expiry,
        "underlying":   near_result.get("underlying", 0),
        "expiry_dates": expiries,
        "timestamp":    near_result.get("timestamp", ""),
    }
    _chain_cache_ts = now
    return _chain_cache


# ── Public API — backward-compatible with multitrade_loader ───

def get_instruments(
    symbol:   str  = DEFAULT_SYMBOL,
    *args,                              # absorbs old (xls_path, temp_path) args
    **kwargs,
) -> Optional[pd.DataFrame]:
    """
    Drop-in replacement for multitrade_loader.get_instruments().
    Returns DataFrame with same columns as the old loader.
    Accepts (and ignores) old XLS path arguments for compatibility.
    """
    try:
        chain_data = _get_chain(symbol)
        near_chain = chain_data.get("near", {})
        far_chain  = chain_data.get("far",  {})

        if not near_chain:
            return None

        rows = []
        # Merge near + far into the same flat format as XLS loader
        all_keys = set(near_chain.keys()) | set(far_chain.keys())

        for (strike, opt_type) in all_keys:
            near = near_chain.get((strike, opt_type), {})
            far  = far_chain.get( (strike, opt_type), {})

            if not near and not far:
                continue

            rows.append({
                "TYPE":        opt_type,
                "STRIKE":      strike,
                "BID":         near.get("bid",   0.0),
                "ASK":         near.get("ask",   0.0),
                "LTP":         near.get("ltp",   0.0),
                "VOLUME":      near.get("volume",0),
                "NEAR_LEG":    near.get("ltp",   0.0),
                "FAR_LEG":     far.get( "ltp",   0.0),
                "NEAR_THETA":  near.get("theta", 0.0),
                "FAR_THETA":   far.get( "theta", 0.0),
                "NEAR_VEGA":   near.get("vega",  0.0),
                "FAR_VEGA":    far.get( "vega",  0.0),
                "NEAR_DELTA":  near.get("delta", 0.0),
                "FAR_DELTA":   far.get( "delta", 0.0),
                "NEAR_OI":     near.get("oi",    0),
                "FAR_OI":      far.get( "oi",    0),
                "IV":          near.get("iv",    0.0),
                "COST":        round(far.get("ltp", 0.0) - near.get("ltp", 0.0), 2),
            })

        if not rows:
            return None

        df = pd.DataFrame(rows)
        df = df[
            (df["STRIKE"] >= STRIKE_MIN) &
            (df["STRIKE"] <= STRIKE_MAX)
        ].copy().reset_index(drop=True)

        return df if not df.empty else None

    except Exception as e:
        print(f"[DataProvider] get_instruments error: {e}")
        return None


def get_ce(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["TYPE"] == "CE"].copy().reset_index(drop=True)


def get_pe(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["TYPE"] == "PE"].copy().reset_index(drop=True)


def get_atm_strike(
    df:     Optional[pd.DataFrame] = None,
    spot:   Optional[float] = None,
    symbol: str = DEFAULT_SYMBOL,
) -> Optional[int]:
    """
    Auto-detect ATM strike.
    If df is provided, uses it (backward-compat).
    If not, queries NSE directly.
    """
    if df is not None and not df.empty:
        # Legacy path — df provided
        ce = get_ce(df)
        if ce.empty:
            return None
        strikes = sorted(ce["STRIKE"].dropna().astype(int).unique())
        if spot and STRIKE_MIN < spot < STRIKE_MAX:
            for step in [100, 50]:
                atm_rounded = int(round(spot / step) * step)
                if atm_rounded in strikes:
                    return atm_rounded
            return min(strikes, key=lambda s: abs(s - spot))
        # Fallback: highest volume
        if "VOLUME" in ce.columns:
            ce["VOLUME"] = pd.to_numeric(ce["VOLUME"], errors="coerce")
            idx = ce["VOLUME"].idxmax()
            return int(ce.loc[idx, "STRIKE"])
        return strikes[len(strikes) // 2]

    # New path — query NSE directly
    return _get_fetcher().get_atm_strike(symbol)


def get_spread(
    df:          Optional[pd.DataFrame],
    strike:      int,
    option_type: str = "CE",
    symbol:      str = DEFAULT_SYMBOL,
) -> Optional[dict]:
    """
    Drop-in replacement for multitrade_loader.get_spread().
    Returns same dict shape as old loader (far_bid, far_ask, etc. always populated).
    """
    try:
        chain_data   = _get_chain(symbol)
        near_chain   = chain_data.get("near", {})
        far_chain    = chain_data.get("far",  {})
        near_expiry  = chain_data.get("near_expiry", "")
        far_expiry   = chain_data.get("far_expiry",  "")

        near = near_chain.get((strike, option_type), {})
        far  = far_chain.get( (strike, option_type), {})

        # Try nearest strike if exact not found
        if not near:
            avail = [k[0] for k in near_chain if k[1] == option_type]
            if avail:
                nearest = min(avail, key=lambda s: abs(s - strike))
                near    = near_chain.get((nearest, option_type), {})
                far     = far_chain.get( (nearest, option_type), {})
                strike  = nearest

        if not near:
            return None

        near_bid = near.get("bid",   0.0)
        near_ask = near.get("ask",   0.0)
        near_ltp = near.get("ltp",   0.0)
        far_bid  = far.get( "bid",   0.0)
        far_ask  = far.get( "ask",   0.0)
        far_ltp  = far.get( "ltp",   0.0)

        # Overlay Dhan ticks if available (sub-second accuracy)
        ts = _get_tick_store()
        # (Token lookup would require a symbol->token map; left as extension point)
        # If Dhan ticks available, they improve bid/ask accuracy

        near_leg = near_ltp
        far_leg  = far_ltp
        spread   = round(far_leg - near_leg, 2)

        nt = near.get("theta", 0.0)
        ft = far.get( "theta", 0.0)
        fair = round((ft - nt) * 0.5, 2) if (nt and ft) else 0.0
        deviation = round(spread - fair, 2)

        return {
            "strike":       strike,
            "type":         option_type,
            "bid":          round(near_bid, 2),
            "ask":          round(near_ask, 2),
            "ltp":          round(near_ltp, 2),
            "volume":       near.get("volume", 0),
            "near_leg":     round(near_leg, 2),
            "far_leg":      round(far_leg,  2),
            "spread":       spread,
            "fair":         fair,
            "deviation":    deviation,
            "near_theta":   near.get("theta", 0.0),
            "far_theta":    far.get( "theta", 0.0),
            "near_vega":    near.get("vega",  0.0),
            "far_vega":     far.get( "vega",  0.0),
            "near_delta":   near.get("delta", 0.0),
            "far_delta":    far.get( "delta", 0.0),
            "iv":           near.get("iv",    0.0),
            "oi":           near.get("oi",    0),
            "cost":         round(far_ltp - near_ltp, 2),
            # Keys expected by Calendaralgofinal.py — always populated now
            "far_bid":      round(far_bid, 2),
            "far_ask":      round(far_ask, 2),
            # Execution prices
            "sell_near_at": round(near_bid - 0.05, 2),
            "buy_near_at":  round(near_ask + 0.05, 2),
            "buy_far_at":   round(far_ask  + 0.05, 2),
            "sell_far_at":  round(far_bid  - 0.05, 2),
            # Expiry info
            "near_expiry":  near_expiry,
            "far_expiry":   far_expiry,
        }

    except Exception as e:
        print(f"[DataProvider] get_spread error: {e}")
        return None


def get_straddle_premium(
    df:     Optional[pd.DataFrame],
    strike: int,
    symbol: str = DEFAULT_SYMBOL,
) -> Optional[float]:
    """Drop-in replacement for multitrade_loader.get_straddle_premium()."""
    try:
        chain_data = _get_chain(symbol)
        near_chain = chain_data.get("near", {})
        ce = near_chain.get((strike, "CE"), {})
        pe = near_chain.get((strike, "PE"), {})
        if not ce or not pe:
            return None
        return round(ce.get("ltp", 0.0) + pe.get("ltp", 0.0), 2)
    except Exception:
        return None


def get_pcr(
    symbol: str = DEFAULT_SYMBOL,
    expiry: Optional[str] = None,
) -> float:
    """Put-Call Ratio by OI."""
    return _get_fetcher().get_pcr(symbol, expiry)


def summary(df: Optional[pd.DataFrame]) -> dict:
    """Quick stats for logging/debugging."""
    if df is None or df.empty:
        return {"total": 0, "ce": 0, "pe": 0, "strikes": []}
    ce = get_ce(df)
    pe = get_pe(df)
    return {
        "total":        len(df),
        "ce":           len(ce),
        "pe":           len(pe),
        "strikes":      sorted(df["STRIKE"].dropna().astype(int).unique().tolist()),
        "strike_range": f"{df['STRIKE'].min():.0f} — {df['STRIKE'].max():.0f}",
        "source":       "NSE Direct API",
    }


def get_historical_options(
    symbol:      str,
    expiry:      Optional[str] = None,
    start:       Optional[str] = None,
    end:         Optional[str] = None,
    option_type: Optional[str] = None,
    strike:      Optional[float] = None,
) -> pd.DataFrame:
    """Proxy to BhavcopyCatalog.load_options()."""
    return _get_catalog().load_options(
        symbol, expiry=expiry, start=start, end=end,
        option_type=option_type, strike=strike
    )


def get_historical_futures(
    symbol: str,
    start:  Optional[str] = None,
    end:    Optional[str] = None,
) -> pd.DataFrame:
    """Proxy to BhavcopyCatalog.load_futures()."""
    return _get_catalog().load_futures(symbol, start=start, end=end)


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("[TEST] DataProvider\n")

    df = get_instruments("BANKNIFTY")
    if df is not None:
        info = summary(df)
        print(f"  Loaded {info['total']} instruments ({info['ce']} CE / {info['pe']} PE)")
        print(f"  Source: {info['source']}")

        atm = get_atm_strike(df)
        print(f"  ATM: {atm}")

        if atm:
            ce_spread = get_spread(df, atm, "CE")
            if ce_spread:
                print(f"  CE {atm}: far_bid={ce_spread['far_bid']}  "
                      f"spread={ce_spread['spread']}  fair={ce_spread['fair']}")
    else:
        print("  get_instruments returned None (market may be closed)")

    pcr = get_pcr("BANKNIFTY")
    print(f"  PCR: {pcr}")
