"""
nse_fetcher.py
==============
Free NSE India option chain fetcher.
No broker account needed. No API key. Completely free.

Data source : https://www.nseindia.com/api/option-chain-indices
Refresh rate: safe at 2s intervals (NSE rate-limits aggressive scrapers)
Covers      : BANKNIFTY, NIFTY, FINNIFTY, MIDCPNIFTY + any F&O stock

Usage:
    from algo.nse_fetcher import NSEFetcher
    fetcher = NSEFetcher()
    chain   = fetcher.get_option_chain("BANKNIFTY", expiry="2026-06-05")
    vix     = fetcher.get_vix()
"""

import time
import requests
import numpy as np
from datetime import datetime
from typing import Optional

# ── NSE endpoints ─────────────────────────────────────────────
_BASE        = "https://www.nseindia.com"
_OC_INDEX    = "/api/option-chain-indices?symbol={sym}"
_OC_EQUITY   = "/api/option-chain-equities?symbol={sym}"
_ALL_INDICES  = "/api/allIndices"

_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com/option-chain",
    "Connection":      "keep-alive",
    "Cache-Control":   "no-cache",
}

# Index symbols use the indices endpoint; everything else uses equities
_INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

# Strike range guard per symbol
_STRIKE_RANGES = {
    "BANKNIFTY":  (40000, 70000),
    "NIFTY":      (20000, 35000),
    "FINNIFTY":   (20000, 28000),
    "MIDCPNIFTY": (10000, 20000),
}
_DEFAULT_RANGE = (0, 999999)


class NSEFetcher:
    """
    Thread-safe NSE option chain fetcher.
    Maintains a persistent requests.Session with NSE cookie handshake.
    Auto-refreshes the session when NSE returns a 403/empty response.
    """

    def __init__(self, session_refresh_interval: int = 120):
        self._session: requests.Session = None
        self._session_born: float = 0
        self._refresh_interval = session_refresh_interval
        self._fail_count: int = 0          # consecutive failures
        self._backoff_until: float = 0     # epoch time to stop backing off
        self._init_session()

    # ── Session management ────────────────────────────────────

    def _init_session(self):
        """Create a fresh session and warm up NSE cookies."""
        s = requests.Session()
        s.headers.update(_HEADERS)
        try:
            s.get(_BASE, timeout=8)                                      # home page
            time.sleep(0.3)
            s.get(_BASE + "/option-chain", timeout=8)                   # option chain page
            time.sleep(0.3)
            s.get(_BASE + _ALL_INDICES, timeout=8)                       # allIndices
            time.sleep(0.3)
        except Exception as e:
            print(f"[NSEFetcher] Session init warning: {e}")
        self._session = s
        self._session_born = time.time()

    def _ensure_session(self):
        """Refresh session if it's stale (older than refresh_interval)."""
        if time.time() - self._session_born > self._refresh_interval:
            self._init_session()

    # ── Public API ────────────────────────────────────────────

    def get_vix(self) -> Optional[float]:
        """Fetch India VIX from NSE allIndices endpoint."""
        self._ensure_session()
        try:
            r = self._session.get(_BASE + _ALL_INDICES, timeout=8)
            r.raise_for_status()
            for item in r.json().get("data", []):
                if "INDIA VIX" in str(item.get("index", "")).upper():
                    return round(float(item["last"]), 2)
        except Exception as e:
            print(f"[NSEFetcher] VIX error: {e}")
            self._init_session()
        return None

    def get_expiry_dates(self, symbol: str) -> list[str]:
        """
        Return list of available expiry dates for a symbol.
        Format: ['2026-06-05', '2026-06-12', ...]
        """
        raw = self._fetch_raw(symbol)
        if not raw:
            return []
        dates = raw.get("records", {}).get("expiryDates", [])
        # NSE returns dates like "05-Jun-2026" — normalise to YYYY-MM-DD
        result = []
        for d in dates:
            try:
                result.append(datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m-%d"))
            except Exception:
                result.append(d)
        return result

    def get_option_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
    ) -> dict:
        """
        Fetch and parse option chain.

        Args:
            symbol: 'BANKNIFTY', 'NIFTY', 'RELIANCE', etc.
            expiry: 'YYYY-MM-DD' — if None, returns ALL expiries

        Returns dict:
        {
          'underlying': 52450.30,
          'timestamp':  '2026-05-29 10:30:05',
          'expiry_dates': ['2026-06-05', ...],
          'chain': {
              (52000, 'CE'): {strike, expiry, option_type, ltp, bid, ask, oi, iv, delta, ...},
              (52000, 'PE'): {...},
              ...
          }
        }
        """
        raw = self._fetch_raw(symbol)
        if not raw:
            return {}

        records  = raw.get("records", {})
        data     = records.get("data", [])
        expiries = records.get("expiryDates", [])
        spot     = float(records.get("underlyingValue", 0))

        strike_min, strike_max = _STRIKE_RANGES.get(symbol.upper(), _DEFAULT_RANGE)
        chain = {}

        for row in data:
            row_expiry_raw = row.get("expiryDate", "")
            # Normalise expiry to YYYY-MM-DD
            try:
                row_expiry = datetime.strptime(row_expiry_raw, "%d-%b-%Y").strftime("%Y-%m-%d")
            except Exception:
                row_expiry = row_expiry_raw

            # Filter by requested expiry
            if expiry and row_expiry != expiry:
                continue

            strike = row.get("strikePrice", 0)
            if not (strike_min <= strike <= strike_max):
                continue

            for opt_type, side_key in (("CE", "CE"), ("PE", "PE")):
                side = row.get(side_key, {})
                if not side:
                    continue

                depth_buy  = side.get("bidQty", 0)
                depth_sell = side.get("askQty", 0)

                chain[(strike, opt_type)] = {
                    "strike":       strike,
                    "expiry":       row_expiry,
                    "option_type":  opt_type,
                    "ltp":          float(side.get("lastPrice",         0) or 0),
                    "bid":          float(side.get("bidprice",          0) or 0),
                    "ask":          float(side.get("askPrice",          0) or 0),
                    "oi":           int(  side.get("openInterest",      0) or 0),
                    "oi_change":    int(  side.get("changeinOpenInterest", 0) or 0),
                    "volume":       int(  side.get("totalTradedVolume", 0) or 0),
                    "iv":           float(side.get("impliedVolatility", 0) or 0),
                    "delta":        float(side.get("delta",             0) or 0),
                    "gamma":        float(side.get("gamma",             0) or 0),
                    "theta":        float(side.get("theta",             0) or 0),
                    "vega":         float(side.get("vega",              0) or 0),
                    "underlying":   spot,
                }

        # Normalise expiry dates
        norm_expiries = []
        for d in expiries:
            try:
                norm_expiries.append(datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m-%d"))
            except Exception:
                norm_expiries.append(d)

        return {
            "underlying":   spot,
            "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expiry_dates": norm_expiries,
            "chain":        chain,
        }

    def get_calendar_legs(
        self,
        symbol:      str,
        near_expiry: str,
        far_expiry:  str,
        strike:      int,
    ) -> dict:
        """
        Return CE and PE data for both legs of a calendar spread.
        Replaces the XLS-based far_bid / far_ask lookup entirely.

        Returns:
        {
          'CE': { strike, near_bid, near_ask, near_ltp, near_oi, near_theta,
                  far_bid, far_ask, far_ltp, far_oi, far_theta, spread },
          'PE': { ... }
        }
        """
        near_chain = self.get_option_chain(symbol, expiry=near_expiry).get("chain", {})
        far_chain  = self.get_option_chain(symbol, expiry=far_expiry ).get("chain", {})

        result = {}
        for opt in ("CE", "PE"):
            near = near_chain.get((strike, opt), {})
            far  = far_chain.get( (strike, opt), {})

            near_bid = near.get("bid",   0.0)
            near_ask = near.get("ask",   0.0)
            near_ltp = near.get("ltp",   0.0)
            far_bid  = far.get( "bid",   0.0)   # ← fixes KeyError: 'far_bid'
            far_ask  = far.get( "ask",   0.0)
            far_ltp  = far.get( "ltp",   0.0)

            # Calendar spread = far mid - near mid
            spread = round(far_ltp - near_ltp, 2)

            # Fair value from theta differential (same formula as XLS loader)
            nt = near.get("theta", 0.0)
            ft = far.get( "theta", 0.0)
            fair = round((ft - nt) * 0.5, 2) if (nt and ft) else 0.0

            result[opt] = {
                "strike":     strike,
                "near_bid":   near_bid,
                "near_ask":   near_ask,
                "near_ltp":   near_ltp,
                "near_oi":    near.get("oi",    0),
                "near_theta": near.get("theta", 0.0),
                "near_vega":  near.get("vega",  0.0),
                "near_delta": near.get("delta", 0.0),
                "far_bid":    far_bid,
                "far_ask":    far_ask,
                "far_ltp":    far_ltp,
                "far_oi":     far.get("oi",     0),
                "far_theta":  far.get("theta",  0.0),
                "far_vega":   far.get("vega",   0.0),
                "far_delta":  far.get("delta",  0.0),
                "spread":     spread,
                "fair":       fair,
                "deviation":  round(spread - fair, 2),
                # Execution price helpers (same keys as old XLS loader)
                "bid":          near_bid,
                "ask":          near_ask,
                "ltp":          near_ltp,
                "buy_far_at":   round(far_ask  + 0.05, 2),
                "sell_near_at": round(near_bid - 0.05, 2),
                "buy_near_at":  round(near_ask + 0.05, 2),
                "sell_far_at":  round(far_bid  - 0.05, 2),
            }
        return result

    def get_pcr(self, symbol: str, expiry: Optional[str] = None) -> float:
        """Put-Call Ratio by Open Interest."""
        chain = self.get_option_chain(symbol, expiry=expiry).get("chain", {})
        ce_oi = sum(v["oi"] for k, v in chain.items() if k[1] == "CE")
        pe_oi = sum(v["oi"] for k, v in chain.items() if k[1] == "PE")
        return round(pe_oi / ce_oi, 4) if ce_oi else 0.0

    def get_atm_strike(self, symbol: str, expiry: Optional[str] = None) -> Optional[int]:
        """Return ATM strike closest to current underlying value."""
        result = self.get_option_chain(symbol, expiry=expiry)
        spot   = result.get("underlying", 0)
        chain  = result.get("chain", {})
        if not spot or not chain:
            return None
        strikes = sorted({k[0] for k in chain.keys()})
        # Round to nearest 100 (BANKNIFTY) or 50 (NIFTY)
        step = 100 if symbol.upper() == "BANKNIFTY" else 50
        atm_rounded = int(round(spot / step) * step)
        if atm_rounded in strikes:
            return atm_rounded
        return min(strikes, key=lambda s: abs(s - spot))

    # ── Internal ──────────────────────────────────────────────

    def _fetch_raw(self, symbol: str) -> Optional[dict]:
        """Fetch raw option chain JSON with circuit breaker to prevent 404 spam."""
        sym = symbol.upper()

        # Circuit breaker — back off for 5 min after 3 consecutive failures
        if self._fail_count >= 3:
            if time.time() < self._backoff_until:
                return None  # silent — already logged
            else:
                self._fail_count = 0  # reset after backoff

        # Primary: nsepython
        try:
            from nsepython import nse_optionchain_scrapper
            data = nse_optionchain_scrapper(sym)
            if data and data.get("records"):
                self._fail_count = 0
                return data
        except Exception as e:
            if "No module" not in str(e):
                print(f"[NSEFetcher] nsepython failed for {sym}: {e}")

        # Fallback: raw requests
        self._ensure_session()
        url = _BASE + (_OC_INDEX if sym in _INDEX_SYMBOLS else _OC_EQUITY).format(sym=sym)
        try:
            r = self._session.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get("records"):
                self._fail_count = 0
                return data
        except Exception as e:
            pass  # fall through to failure handling

        # Both failed
        self._fail_count += 1
        if self._fail_count == 1:
            print(f"[NSEFetcher] Option chain unavailable for {sym} — NSE may be blocking. Retrying in 5 min.")
        if self._fail_count >= 3:
            self._backoff_until = time.time() + 300
        return None


# ── Singleton for use across algo scripts ─────────────────────
_fetcher: Optional[NSEFetcher] = None

def get_fetcher() -> NSEFetcher:
    """Return shared singleton NSEFetcher instance."""
    global _fetcher
    if _fetcher is None:
        _fetcher = NSEFetcher()
    return _fetcher


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("[TEST] NSEFetcher\n")
    f = NSEFetcher()

    vix = f.get_vix()
    print(f"  VIX: {vix}")

    expiries = f.get_expiry_dates("BANKNIFTY")
    print(f"  BANKNIFTY expiries: {expiries[:4]}")

    if expiries:
        atm = f.get_atm_strike("BANKNIFTY", expiries[0])
        print(f"  ATM: {atm}")

        pcr = f.get_pcr("BANKNIFTY", expiries[0])
        print(f"  PCR: {pcr}")

        if len(expiries) >= 2 and atm:
            legs = f.get_calendar_legs("BANKNIFTY", expiries[0], expiries[1], atm)
            ce = legs.get("CE", {})
            print(f"  Calendar CE {atm}: far_bid={ce.get('far_bid')} near_ask={ce.get('near_ask')} spread={ce.get('spread')}")
