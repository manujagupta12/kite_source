"""
dhan_data.py - Fallback data provider when NSE scraping is blocked.

Data sources:
  1. Dhan Data API /v2/optionchain  — requires paid subscription (auto-disabled on 401)
  2. Dhan Market Feed /v2/marketfeed/ltp — FREE with trading API token (fallback)
  3. Yahoo Finance                  — index spot + VIX (free, no auth)
"""
import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

_API     = "https://api.dhan.co/v2"
_TIMEOUT = 8
_session = requests.Session()

# Set False after first 401 to stop spamming Dhan paid APIs
_DHAN_OC_ENABLED = True


def _headers() -> Optional[dict]:
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    token     = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if not (client_id and token):
        tf = Path(__file__).parent.parent / "data" / "dhan_token.json"
        if tf.exists():
            try:
                d = json.loads(tf.read_text())
                client_id = d.get("client_id", "")
                token     = d.get("access_token", "")
            except Exception:
                pass
    if not (client_id and token):
        return None
    return {
        "access-token": token,
        "client-id":    client_id,
        "Content-Type": "application/json",
        "Accept":       "application/json",
    }


def get_index_ltp() -> Dict[str, dict]:
    """
    Returns live LTP for NIFTY, BANKNIFTY, FINNIFTY, VIX via Yahoo Finance.
    Free, no auth, not blocked by NSE.
    """
    _YF_TICKERS = {
        "NIFTY":     "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "FINNIFTY":  "^CNXFINANCE",
        "VIX":       "^INDIAVIX",
    }
    try:
        import yfinance as yf
    except ImportError:
        print("[DhanData] yfinance not installed - run: pip install yfinance")
        return {}
    result = {}
    try:
        tickers = list(_YF_TICKERS.values())
        data = yf.download(tickers, period="1d", interval="1m",
                           progress=False, threads=True, auto_adjust=True)
        if data.empty:
            return {}
        prices = data["Close"] if "Close" in data else data
        for name, ticker in _YF_TICKERS.items():
            try:
                if ticker not in prices.columns:
                    continue
                series = prices[ticker].dropna()
                if series.empty:
                    continue
                ltp  = float(series.iloc[-1])
                prev = float(series.iloc[-2]) if len(series) > 1 else ltp
                ch   = round(ltp - prev, 2)
                chp  = round((ch / prev * 100) if prev else 0, 2)
                result[name] = {
                    "label":      name,
                    "ltp":        round(ltp, 2),
                    "change_pct": chp,
                    "change":     ch,
                    "high":       round(float(prices[ticker].max()), 2),
                    "low":        round(float(prices[ticker].min()), 2),
                    "_ts":        int(time.time()),
                    "_src":       "yahoo",
                }
            except Exception:
                continue
        if result:
            n = result.get("NIFTY", {}).get("ltp", "?")
            b = result.get("BANKNIFTY", {}).get("ltp", "?")
            print(f"[DhanData] Yahoo Finance OK -- NIFTY={n} BANKNIFTY={b}")
    except Exception as e:
        print(f"[DhanData] Yahoo Finance error: {e}")
    return result


def get_option_chain(symbol: str = "BANKNIFTY", expiry_count: int = 2) -> dict:
    """
    Dhan option chain. Auto-disabled after first 401 (Data API not subscribed).
    Upgrade at web.dhan.co > DhanHQ APIs > Data APIs to enable.
    """
    global _DHAN_OC_ENABLED
    if not _DHAN_OC_ENABLED:
        return {}
    h = _headers()
    if not h:
        return {}
    _OC_SCRIP = {
        "NIFTY":     {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"},
        "BANKNIFTY": {"UnderlyingScrip": 25, "UnderlyingSeg": "IDX_I"},
        "FINNIFTY":  {"UnderlyingScrip": 27, "UnderlyingSeg": "IDX_I"},
    }
    scrip = _OC_SCRIP.get(symbol.upper())
    if not scrip:
        return {}
    try:
        r = _session.post(
            f"{_API}/optionchain",
            json={**scrip, "ExpiryCount": expiry_count},
            headers=h, timeout=10,
        )
        if r.status_code == 401:
            _DHAN_OC_ENABLED = False
            print("[DhanData] Dhan Data APIs not subscribed -- option chain disabled")
            return {}
        if r.status_code != 200:
            return {}
        return r.json()
    except Exception:
        return {}


def get_parsed_option_chain(symbol: str = "BANKNIFTY") -> dict:
    """Fetch + parse Dhan option chain into NSEFetcher-compatible format."""
    raw = get_option_chain(symbol)
    if not raw or "data" not in raw:
        return {}
    data     = raw["data"]
    oc_map   = data.get("oc", {})
    spot     = float(data.get("underlying_ltp") or 0)
    expiries = data.get("expiry_list") or list(oc_map.keys())
    if not (expiries and spot):
        return {}
    step = 100 if "BANK" in symbol.upper() else 50
    atm  = int(round(spot / step) * step)
    chain = {}
    ce_oi_map = {}
    pe_oi_map = {}
    total_ce = total_pe = 0
    for expiry, strikes in oc_map.items():
        for sk, sides in strikes.items():
            try:
                strike = int(float(sk))
            except Exception:
                continue
            def _side(d):
                if not d:
                    return None
                return {
                    "ltp":   float(d.get("ltp") or 0),
                    "bid":   float(d.get("bid_price") or 0),
                    "ask":   float(d.get("ask_price") or 0),
                    "oi":    int(d.get("oi") or 0),
                    "iv":    float(d.get("iv") or 0),
                    "delta": float(d.get("delta") or 0),
                    "theta": float(d.get("theta") or 0),
                    "vega":  float(d.get("vega") or 0),
                    "gamma": float(d.get("gamma") or 0),
                }
            ce = _side(sides.get("call_options") or sides.get("CE"))
            pe = _side(sides.get("put_options")  or sides.get("PE"))
            chain[(strike, expiry)] = {"strike": strike, "expiry": expiry, "CE": ce, "PE": pe}
            if ce and ce["oi"]:
                ce_oi_map[strike] = ce_oi_map.get(strike, 0) + ce["oi"]
                total_ce += ce["oi"]
            if pe and pe["oi"]:
                pe_oi_map[strike] = pe_oi_map.get(strike, 0) + pe["oi"]
                total_pe += pe["oi"]
    pcr    = round(total_pe / total_ce, 3) if total_ce else 0
    top_ce = max(ce_oi_map, key=ce_oi_map.get) if ce_oi_map else None
    top_pe = max(pe_oi_map, key=pe_oi_map.get) if pe_oi_map else None
    return {
        "symbol": symbol, "spot_price": spot, "atm_strike": atm,
        "expiries": expiries, "chain": chain, "pcr": pcr,
        "top_ce_oi_strike": top_ce, "top_pe_oi_strike": top_pe,
        "_src": "dhan",
    }


# ── NSEFetcher-compatible adapter ─────────────────────────────

_raw_cache: dict = {}       # {SYMBOL: (raw_dict, expire_epoch)}
_RAW_CACHE_TTL = 3.0        # seconds — avoids double API calls per tick


def _get_raw_cached(symbol: str) -> dict:
    """Cached wrapper around get_option_chain. Prevents duplicate Dhan API calls."""
    key = symbol.upper()
    cached = _raw_cache.get(key)
    if cached:
        data, expires = cached
        if time.time() < expires:
            return data
    raw = get_option_chain(symbol, expiry_count=2)
    if raw:
        _raw_cache[key] = (raw, time.time() + _RAW_CACHE_TTL)
    return raw or {}


# ── Market Feed chain cache ───────────────────────────────────

_mf_chain_cache: dict = {}          # {SYMBOL: (chain_result, expire_epoch)}
_MF_CHAIN_TTL = 2.0                 # seconds


def _get_mktfeed_chain(symbol: str, expiry: str = None) -> dict:
    """
    Build option chain from Dhan Market Feed LTP (free trading API).
    Used when Dhan paid Data API is not subscribed (_DHAN_OC_ENABLED = False).
    Caches results to avoid repeated CSV lookups and HTTP calls.
    """
    key    = symbol.upper()
    cached = _mf_chain_cache.get(key)
    if cached:
        data, expires = cached
        if time.time() < expires:
            # Apply expiry filter from cache if requested
            if expiry and data.get("chain"):
                filtered = {k: v for k, v in data["chain"].items()
                            if v.get("expiry") == expiry}
                if filtered:
                    return {**data, "chain": filtered}
            return data
    try:
        from algo.dhan_market_feed import get_option_chain as _mf_chain
        result = _mf_chain(symbol)
        if result:
            _mf_chain_cache[key] = (result, time.time() + _MF_CHAIN_TTL)
            if expiry and result.get("chain"):
                filtered = {k: v for k, v in result["chain"].items()
                            if v.get("expiry") == expiry}
                if filtered:
                    return {**result, "chain": filtered}
        return result or {}
    except Exception as e:
        print(f"[DhanData] Market feed chain error: {e}")
        return {}


def _norm_expiry(d: str) -> str:
    """Normalise any Dhan/NSE expiry format to YYYY-MM-DD."""
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(d, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return d


def get_nse_compatible_chain(symbol: str = "BANKNIFTY",
                             expiry: str = None) -> dict:
    """
    Fetch Dhan option chain in NSEFetcher-compatible format.
    Keys: (strike, 'CE'/'PE') filtered to the requested expiry.
    Drop-in for NSEFetcher.get_option_chain().

    Source priority:
      1. Dhan paid Data API (/v2/optionchain)  — disabled on 401
      2. Dhan Market Feed   (/v2/marketfeed/ltp) — free trading API
    """
    # Primary: paid Data API
    raw = _get_raw_cached(symbol)
    if not raw or "data" not in raw:
        # Fallback: free Market Feed LTP
        return _get_mktfeed_chain(symbol, expiry=expiry)

    data         = raw["data"]
    oc_map       = data.get("oc", {})
    spot         = float(data.get("underlying_ltp") or 0)
    expiries_raw = data.get("expiry_list") or list(oc_map.keys())
    if not (oc_map and spot):
        return {}

    norm_expiries = [_norm_expiry(e) for e in expiries_raw]
    chain = {}

    for exp_raw, strikes in oc_map.items():
        exp_norm = _norm_expiry(exp_raw)
        if expiry and exp_norm != expiry:
            continue
        for sk, sides in strikes.items():
            try:
                strike = int(float(sk))
            except Exception:
                continue
            for opt_type, raw_key in (("CE", "call_options"), ("PE", "put_options")):
                d = sides.get(raw_key) or sides.get(opt_type)
                if not d:
                    continue
                chain[(strike, opt_type)] = {
                    "strike":      strike,
                    "expiry":      exp_norm,
                    "option_type": opt_type,
                    "ltp":         float(d.get("ltp") or 0),
                    "bid":         float(d.get("bid_price") or 0),
                    "ask":         float(d.get("ask_price") or 0),
                    "oi":          int(d.get("oi") or 0),
                    "oi_change":   int(d.get("oi_change") or 0),
                    "volume":      int(d.get("volume") or 0),
                    "iv":          float(d.get("iv") or 0),
                    "delta":       float(d.get("delta") or 0),
                    "gamma":       float(d.get("gamma") or 0),
                    "theta":       float(d.get("theta") or 0),
                    "vega":        float(d.get("vega") or 0),
                    "underlying":  spot,
                }

    if not chain:
        return {}

    return {
        "underlying":   spot,
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_dates": norm_expiries,
        "chain":        chain,
        "_src":         "dhan",
    }


def get_expiry_dates(symbol: str) -> list:
    """
    Return expiry dates from Dhan.
    Falls back to Market Feed instruments CSV when paid Data API unavailable.
    """
    raw = _get_raw_cached(symbol)
    if raw and "data" in raw:
        data         = raw["data"]
        expiries_raw = data.get("expiry_list") or list(data.get("oc", {}).keys())
        return [_norm_expiry(e) for e in expiries_raw]
    # Fallback: parse from instruments CSV (free)
    try:
        from algo.dhan_market_feed import get_expiry_dates as _mf_expiries
        return _mf_expiries(symbol)
    except Exception:
        return []


def get_vix() -> Optional[float]:
    """India VIX via Yahoo Finance (free, no auth, not rate-limited by NSE)."""
    try:
        import yfinance as yf
        hist = yf.Ticker("^INDIAVIX").history(period="1d", interval="1m")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        pass
    return None


def get_pcr(symbol: str = "NIFTY") -> float:
    """
    PCR (OI-based) computed from Dhan option chain.
    Falls back to Market Feed LTP when paid Data API unavailable.
    """
    raw = _get_raw_cached(symbol)
    if raw and "data" in raw:
        oc_map = raw["data"].get("oc", {})
        ce_oi = pe_oi = 0
        for exp, strikes in oc_map.items():
            for sk, sides in strikes.items():
                ce = sides.get("call_options") or sides.get("CE") or {}
                pe = sides.get("put_options")  or sides.get("PE") or {}
                ce_oi += int(ce.get("oi") or 0)
                pe_oi += int(pe.get("oi") or 0)
        return round(pe_oi / ce_oi, 4) if ce_oi else 0.0
    # Fallback: compute PCR from Market Feed LTP OI
    try:
        from algo.dhan_market_feed import get_pcr as _mf_pcr
        return _mf_pcr(symbol)
    except Exception:
        return 0.0


def is_available() -> bool:
    return bool(_headers())
