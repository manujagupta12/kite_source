"""
dhan_market_feed.py
===================
Option chain builder using Dhan's FREE Market Feed LTP API.
Does NOT require paid Data API subscription — works with trading API token.

Flow:
  1. Download Dhan instruments CSV (daily cache) → get security IDs for F&O options
  2. Fetch index spot price via IDX_I segment LTP (or Yahoo Finance fallback)
  3. Find ATM±15 strikes from CSV for nearest 2 expiries
  4. POST /v2/marketfeed/ltp with NSE_FO security IDs → live LTP, OI, Volume
  5. Return NSEFetcher-compatible chain dict

Why this works:
  - /v2/marketfeed/ltp = FREE with any Dhan trading account token
  - /v2/optionchain    = PAID Data API subscription (not used here)
"""

import os
import csv
import io
import json
import time
import logging
import requests
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List

log = logging.getLogger("DhanMktFeed")

_API     = "https://api.dhan.co/v2"
_TIMEOUT = 10

# Dhan instruments master CSV (published daily by Dhan, free download)
_INSTRUMENTS_CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Disk cache
_CACHE_DIR         = Path(os.environ.get("DATA_DIR", "C:/data"))
_INSTRUMENTS_CACHE = _CACHE_DIR / "dhan_instruments.csv"
_INSTRUMENTS_META  = _CACHE_DIR / "dhan_instruments_meta.json"

# In-memory instruments lookup: {(underlying, expiry_YYYY-MM-DD, strike, opt_type): security_id}
_instruments: dict = {}
_instruments_date: str = ""

# Spot price cache
_spot_cache: dict = {}      # {symbol: (price, ts)}
_SPOT_TTL = 1.5             # seconds

# INDEX segment tokens (same as dhan_ticker.py INDEX_TOKENS)
_INDEX_TOKENS = {
    "NIFTY":     13,
    "BANKNIFTY": 25,
    "FINNIFTY":  27,
}


# ── Credentials ───────────────────────────────────────────────

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


# ── Instruments CSV ───────────────────────────────────────────

def _norm_expiry(d: str) -> str:
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(d.strip(), fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return d.strip()


def _parse_csv(text: str) -> dict:
    """
    Parse instruments master CSV.
    Handles variations in column names across Dhan CSV versions.
    Returns {(underlying, expiry, strike, opt_type): security_id}
    """
    result = {}
    try:
        reader = csv.DictReader(io.StringIO(text))
        cols   = reader.fieldnames or []

        # Column aliases (Dhan occasionally renames columns)
        def _col(*names):
            for n in names:
                if n in cols:
                    return n
            return names[0]

        col_seg    = _col("SEM_SEGMENT", "SEGMENT")
        col_secid  = _col("SEM_SMST_SECURITY_ID", "SEM_SEC_ID", "SECURITY_ID")
        col_inst   = _col("SEM_INSTRUMENT_NAME", "INSTRUMENT_NAME")
        col_sym    = _col("SM_SYMBOL_NAME", "SEM_TRADING_SYMBOL", "SYMBOL_NAME")
        col_expiry = _col("SEM_EXPIRY_DATE", "EXPIRY_DATE")
        col_strike = _col("SEM_STRIKE_PRICE", "STRIKE_PRICE")
        col_opt    = _col("SEM_OPTION_TYPE", "OPTION_TYPE")

        for row in reader:
            seg  = row.get(col_seg, "").strip().upper()
            # Only NSE F&O options
            if "NSE_FO" not in seg and "NSE_F" not in seg:
                continue
            inst = row.get(col_inst, "").strip().upper()
            if "OPT" not in inst:           # skip futures
                continue
            sec_id_str = row.get(col_secid, "").strip()
            try:
                sec_id = int(sec_id_str)
            except (ValueError, TypeError):
                continue
            underlying = row.get(col_sym, "").strip().upper()
            if not underlying:
                continue
            expiry_raw = row.get(col_expiry, "").strip()
            if not expiry_raw:
                continue
            expiry = _norm_expiry(expiry_raw)
            strike_str = row.get(col_strike, "0").strip()
            try:
                strike = int(float(strike_str))
            except (ValueError, TypeError):
                continue
            opt = row.get(col_opt, "").strip().upper()
            if opt not in ("CE", "PE", "CA", "PA"):
                continue
            opt = "CE" if opt in ("CE", "CA") else "PE"

            result[(underlying, expiry, strike, opt)] = sec_id

    except Exception as e:
        log.error(f"[MktFeed] CSV parse error: {e}")
    return result


def _load_instruments() -> dict:
    """Load instruments CSV (disk-cached daily, auto-refreshed)."""
    global _instruments, _instruments_date
    today = date.today().isoformat()

    if _instruments and _instruments_date == today:
        return _instruments

    # Try disk cache
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if _INSTRUMENTS_CACHE.exists() and _INSTRUMENTS_META.exists():
            meta = json.loads(_INSTRUMENTS_META.read_text())
            if meta.get("date") == today:
                log.info("[MktFeed] Loading instruments from disk cache")
                parsed = _parse_csv(_INSTRUMENTS_CACHE.read_text(encoding="utf-8", errors="replace"))
                if parsed:
                    _instruments = parsed
                    _instruments_date = today
                    log.info(f"[MktFeed] {len(_instruments):,} F&O option instruments loaded from cache")
                    return _instruments
    except Exception as e:
        log.debug(f"[MktFeed] Disk cache read failed: {e}")

    # Download fresh
    log.info("[MktFeed] Downloading Dhan instruments master CSV …")
    try:
        r = requests.get(_INSTRUMENTS_CSV_URL, timeout=30)
        r.raise_for_status()
        text = r.text
        try:
            _INSTRUMENTS_CACHE.write_text(text, encoding="utf-8")
            _INSTRUMENTS_META.write_text(json.dumps({"date": today}))
        except Exception as e:
            log.debug(f"[MktFeed] Disk write failed: {e}")
        _instruments = _parse_csv(text)
        _instruments_date = today
        log.info(f"[MktFeed] Downloaded {len(_instruments):,} F&O option instruments")
        return _instruments
    except Exception as e:
        log.error(f"[MktFeed] Failed to download instruments CSV: {e}")
        return {}


# ── Spot price ────────────────────────────────────────────────

def get_spot_price(symbol: str = "BANKNIFTY") -> float:
    """
    Live index spot price.
    Priority: Dhan IDX_I LTP → Dhan WebSocket TickStore → Yahoo Finance
    """
    cached = _spot_cache.get(symbol.upper())
    if cached:
        price, ts = cached
        if time.time() - ts < _SPOT_TTL:
            return price

    # 1. Dhan IDX_I LTP
    try:
        h = _headers()
        if h:
            token = _INDEX_TOKENS.get(symbol.upper())
            if token:
                r = requests.post(
                    f"{_API}/marketfeed/ltp",
                    json={"IDX_I": [token]},
                    headers=h,
                    timeout=_TIMEOUT,
                )
                if r.status_code == 200:
                    d = r.json().get("data", {}).get("IDX_I", {})
                    for val in d.values():
                        p = float(val.get("last_price") or val.get("ltp") or 0)
                        if p > 0:
                            _spot_cache[symbol.upper()] = (p, time.time())
                            return p
    except Exception as e:
        log.debug(f"[MktFeed] IDX LTP error: {e}")

    # 2. Dhan WebSocket TickStore
    try:
        from algo.dhan_ticker import get_tick_store
        ts = get_tick_store()
        token = _INDEX_TOKENS.get(symbol.upper())
        if ts and token:
            ticks = ts.get_latest()
            p = float((ticks.get(token) or {}).get("ltp", 0))
            if p > 0:
                _spot_cache[symbol.upper()] = (p, time.time())
                return p
    except Exception as e:
        log.debug(f"[MktFeed] TickStore spot error: {e}")

    # 3. Yahoo Finance
    try:
        import yfinance as yf
        _YF = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "FINNIFTY": "^CNXFINANCE"}
        ticker = _YF.get(symbol.upper())
        if ticker:
            hist = yf.Ticker(ticker).history(period="1d", interval="1m")
            if not hist.empty:
                p = round(float(hist["Close"].iloc[-1]), 2)
                _spot_cache[symbol.upper()] = (p, time.time())
                return p
    except Exception as e:
        log.debug(f"[MktFeed] Yahoo Finance spot error: {e}")

    return 0.0


# ── Market Feed LTP ───────────────────────────────────────────

def _fetch_ltp(security_ids: List[int], segment: str = "NSE_FO") -> dict:
    """
    POST /v2/marketfeed/ltp — FREE with trading API token.
    Returns {security_id: {"ltp": float, "oi": int, "volume": int}}
    """
    if not security_ids:
        return {}
    h = _headers()
    if not h:
        return {}

    result = {}
    BATCH = 100     # Dhan typically allows up to 100 per request

    for i in range(0, len(security_ids), BATCH):
        batch = security_ids[i:i + BATCH]
        try:
            r = requests.post(
                f"{_API}/marketfeed/ltp",
                json={segment: batch},
                headers=h,
                timeout=_TIMEOUT,
            )
            if r.status_code == 200:
                seg_data = r.json().get("data", {}).get(segment, {})
                for id_str, vals in seg_data.items():
                    try:
                        sid  = int(id_str)
                        ltp  = float(vals.get("last_price") or vals.get("ltp") or 0)
                        oi   = int(vals.get("oi") or vals.get("open_interest") or 0)
                        vol  = int(vals.get("volume") or 0)
                        result[sid] = {"ltp": ltp, "oi": oi, "volume": vol}
                    except (ValueError, TypeError):
                        continue
            elif r.status_code == 401:
                log.error("[MktFeed] 401 Unauthorized — token expired or invalid")
                break
            elif r.status_code == 429:
                log.warning("[MktFeed] Rate limited — sleeping 1s")
                time.sleep(1)
            else:
                log.warning(f"[MktFeed] LTP API {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.error(f"[MktFeed] LTP batch error: {e}")

    return result


# ── Public API ────────────────────────────────────────────────

def get_option_chain(
    symbol:         str = "BANKNIFTY",
    strikes_radius: int = 15,       # ATM ± N strikes
) -> dict:
    """
    Build a live option chain using Dhan Market Feed LTP (free trading API).

    Returns NSEFetcher-compatible format:
      {
        "underlying":   float,
        "timestamp":    str,
        "expiry_dates": [str, ...],
        "chain":        {(strike, opt_type): {...}},
        "_src":         "dhan_mktfeed",
      }
    """
    instruments = _load_instruments()
    if not instruments:
        log.warning("[MktFeed] No instruments loaded")
        return {}

    spot = get_spot_price(symbol)
    if not spot:
        log.warning(f"[MktFeed] Cannot determine spot for {symbol}")
        return {}

    step = 100 if "BANK" in symbol.upper() else 50
    atm  = int(round(spot / step) * step)

    today = date.today().isoformat()
    expiries = sorted(set(
        exp for (sym, exp, sk, ot) in instruments
        if sym == symbol.upper() and exp >= today
    ))[:2]

    if not expiries:
        log.warning(f"[MktFeed] No upcoming expiries found for {symbol}")
        return {}

    strike_lo = atm - strikes_radius * step
    strike_hi = atm + strikes_radius * step

    ids_to_fetch: List[int] = []
    id_to_meta:   dict      = {}   # sec_id → (strike, opt_type, expiry)

    for expiry in expiries:
        for strike in range(strike_lo, strike_hi + step, step):
            for opt_type in ("CE", "PE"):
                sec_id = instruments.get((symbol.upper(), expiry, strike, opt_type))
                if sec_id:
                    ids_to_fetch.append(sec_id)
                    id_to_meta[sec_id] = (strike, opt_type, expiry)

    if not ids_to_fetch:
        log.warning(f"[MktFeed] No instruments found for {symbol} in CSV (range {strike_lo}–{strike_hi})")
        return {}

    log.info(f"[MktFeed] Fetching LTP for {len(ids_to_fetch)} instruments ({symbol} ATM={atm})")
    ltp_data = _fetch_ltp(ids_to_fetch)

    if not ltp_data:
        log.warning("[MktFeed] LTP API returned no data")
        return {}

    chain: dict = {}
    for sec_id, market in ltp_data.items():
        meta = id_to_meta.get(sec_id)
        if not meta:
            continue
        strike, opt_type, expiry = meta
        ltp = market.get("ltp", 0.0)
        oi  = market.get("oi",  0)
        vol = market.get("volume", 0)
        if ltp <= 0 and oi == 0:
            continue    # skip zero / illiquid

        chain[(strike, opt_type)] = {
            "strike":      strike,
            "expiry":      expiry,
            "option_type": opt_type,
            "ltp":         round(ltp, 2),
            # bid/ask approximated from LTP (LTP endpoint doesn't give quotes)
            "bid":         round(max(0.05, ltp - max(0.50, ltp * 0.003)), 2),
            "ask":         round(ltp + max(0.50, ltp * 0.003), 2),
            "oi":          oi,
            "oi_change":   0,
            "volume":      vol,
            "iv":          0.0,     # not available from LTP endpoint
            "delta":       0.0,
            "gamma":       0.0,
            "theta":       0.0,
            "vega":        0.0,
            "underlying":  spot,
        }

    if not chain:
        log.warning(f"[MktFeed] Empty chain built for {symbol}")
        return {}

    log.info(f"[MktFeed] ✅ {symbol} chain: {len(chain)} entries  spot={spot}  expiries={expiries}")
    return {
        "underlying":   spot,
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_dates": expiries,
        "chain":        chain,
        "_src":         "dhan_mktfeed",
    }


def get_expiry_dates(symbol: str = "BANKNIFTY") -> List[str]:
    """Return upcoming expiry dates for symbol (from instruments CSV, no API call)."""
    instruments = _load_instruments()
    today = date.today().isoformat()
    expiries = sorted(set(
        exp for (sym, exp, sk, ot) in instruments
        if sym == symbol.upper() and exp >= today
    ))
    return expiries[:4]


def get_pcr(symbol: str = "NIFTY") -> float:
    """PCR (OI-based) computed from live Market Feed LTP data."""
    instruments = _load_instruments()
    if not instruments:
        return 0.0
    today = date.today().isoformat()
    expiries = sorted(set(
        exp for (sym, exp, sk, ot) in instruments
        if sym == symbol.upper() and exp >= today
    ))[:2]
    if not expiries:
        return 0.0
    spot = get_spot_price(symbol)
    if not spot:
        return 0.0
    step = 100 if "BANK" in symbol.upper() else 50
    atm  = int(round(spot / step) * step)
    # Use wider range for PCR (±50 strikes)
    lo = atm - 50 * step
    hi = atm + 50 * step
    ids: List[int] = []
    id_to_meta: dict = {}
    for expiry in expiries:
        for strike in range(lo, hi + step, step):
            for ot in ("CE", "PE"):
                sid = instruments.get((symbol.upper(), expiry, strike, ot))
                if sid:
                    ids.append(sid)
                    id_to_meta[sid] = (strike, ot)
    ltp_data = _fetch_ltp(ids)
    ce_oi = pe_oi = 0
    for sid, m in ltp_data.items():
        meta = id_to_meta.get(sid)
        if meta:
            _, ot = meta
            if ot == "CE":
                ce_oi += m.get("oi", 0)
            else:
                pe_oi += m.get("oi", 0)
    return round(pe_oi / ce_oi, 4) if ce_oi else 0.0


def is_available() -> bool:
    """True if Dhan trading API token is configured."""
    return bool(_headers())


# ── Self-test ─────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Load .env
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
    except ImportError:
        pass

    print("\n=== Dhan Market Feed Option Chain Test ===\n")
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "BANKNIFTY"

    print(f"Step 1 — Credentials: {'OK' if is_available() else 'MISSING'}")

    expiries = get_expiry_dates(sym)
    print(f"Step 2 — Expiries for {sym}: {expiries}")

    spot = get_spot_price(sym)
    print(f"Step 3 — Spot: {spot}")

    chain_data = get_option_chain(sym)
    if chain_data:
        chain = chain_data.get("chain", {})
        print(f"Step 4 — Chain: {len(chain)} instruments  src={chain_data.get('_src')}")
        atm_c = chain_data["underlying"]
        step  = 100 if "BANK" in sym else 50
        atm   = int(round(atm_c / step) * step)
        for ot in ("CE", "PE"):
            d = chain.get((atm, ot))
            if d:
                print(f"         ATM {atm} {ot}: ltp={d['ltp']}  oi={d['oi']}  vol={d['volume']}")
    else:
        print("Step 4 — Chain: FAILED")
    print()
