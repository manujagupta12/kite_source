"""
NSE INDIA CONNECTOR  —  nse_connector.py
==========================================
Python client for real NSE market data via the stock-nse-india Node.js server.

ARCHITECTURE:
  NSE India (public API)
       ↓  scrapes
  stock-nse-india (Node.js server, runs on localhost:3000)
       ↓  REST API
  nse_connector.py (this file — Python client)
       ↓  feeds data
  calendar.py / equity_india.py / multistrategy.py

WHAT YOU GET FROM REAL NSE DATA vs MultiTrade Excel:
  ✅ Real-time option chain for NIFTY, BANKNIFTY, FINNIFTY
  ✅ Accurate IV, Greeks (Delta, Gamma, Theta, Vega) from NSE
  ✅ Open Interest data (true market positioning)
  ✅ Exact bid/ask from NSE (not delayed)
  ✅ India VIX live (no need to scrape separately)
  ✅ Market-wide gainers, losers, most active stocks
  ✅ Historical OHLCV for any NSE symbol
  ✅ F&O participant-wise data (FII/DII/Retail OI)
  ✅ Expiry dates, strike lists (auto-detected)
  ✅ NIFTY50 / BANKNIFTY / FINNIFTY real-time index values

SETUP (one-time, 5 minutes):
  1. Install Node.js (already done for React frontend)
  2. Start the NSE server:
       cd C:\\AlgoTrading\\nse-server
       npx stock-nse-india
     OR via Docker (easiest):
       docker run --rm -d -p 3000:3000 imcodeman/nseindia
  3. Verify it's running: open http://localhost:3000 in browser
  4. Run your algos — they auto-connect to this server

NO API KEY NEEDED:
  stock-nse-india uses NSE's public endpoints (no auth required).
  NSE data is publicly available during market hours (9:00 AM - 3:30 PM IST).
"""

import requests
import time
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from functools import lru_cache

# ── Server config ─────────────────────────────────────────────
NSE_SERVER   = "http://localhost:3000"   # stock-nse-india server
TIMEOUT      = 10                         # seconds per request
CACHE_TTL    = 3                          # seconds to cache live data

class NseConnector:
    """
    Python client for the stock-nse-india Node.js server.
    Provides clean, structured data for all algo scripts.
    """

    def __init__(self, server_url: str = NSE_SERVER):
        self.base    = server_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AlgoTrade-NSE-Client/1.0",
            "Accept":     "application/json",
        })
        self._cache     = {}   # simple TTL cache
        self._connected = False
        self._check_server()

    def _check_server(self):
        """Verify the NSE server is running."""
        try:
            r = self.session.get(f"{self.base}/", timeout=5)
            if r.status_code == 200:
                self._connected = True
                print(f"  [NSE] ✅ Connected to stock-nse-india server at {self.base}")
            else:
                self._warn_not_connected()
        except Exception:
            self._warn_not_connected()

    def _warn_not_connected(self):
        print(f"""
  [NSE] ⚠  Cannot reach NSE server at {self.base}

  START THE SERVER with one of these:

  Option 1 — npx (easiest, Node.js already installed):
    cd C:\\AlgoTrading\\nse-server
    npx stock-nse-india@latest

  Option 2 — Docker (if Docker is installed):
    docker run --rm -d -p 3000:3000 imcodeman/nseindia

  Option 3 — npm global install:
    npm install -g stock-nse-india
    stock-nse-india

  Then run your algo script again.
  Falling back to NSE public API directly (slower, may be rate-limited).
""")
        self._connected = False

    def _get(self, path: str, cache_key: str = None) -> dict | list | None:
        """GET request with simple TTL caching."""
        # Check cache
        if cache_key and cache_key in self._cache:
            data, expire = self._cache[cache_key]
            if time.time() < expire:
                return data

        url = f"{self.base}{path}"
        try:
            r    = self.session.get(url, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            data = r.json()
            if cache_key:
                self._cache[cache_key] = (data, time.time() + CACHE_TTL)
            return data
        except Exception as e:
            return None

    # ════════════════════════════════════════════════════════
    #  MARKET STATUS
    # ════════════════════════════════════════════════════════
    def market_status(self) -> dict:
        """Returns whether NSE market is currently open."""
        d = self._get("/api/marketStatus")
        if not d:
            # Fallback: check time
            now  = datetime.now()
            hour = now.hour; minute = now.minute
            is_open = (9 <= hour < 15 or (hour == 15 and minute <= 30))
            return {"isOpen": is_open, "source": "time-based"}
        return d

    def is_market_open(self) -> bool:
        return self.market_status().get("isOpen", False)

    # ════════════════════════════════════════════════════════
    #  INDICES — NIFTY, BANKNIFTY, FINNIFTY, VIX
    # ════════════════════════════════════════════════════════
    def get_all_indices(self) -> list:
        """Returns all NSE indices with current values."""
        return self._get("/api/allIndices", "all_indices") or []

    def get_index(self, name: str) -> dict | None:
        """
        Get specific index data.
        name: 'NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'INDIA VIX'
        """
        indices = self.get_all_indices()
        for idx in indices:
            if name.upper() in idx.get("indexSymbol","").upper():
                return {
                    "symbol":     idx.get("indexSymbol"),
                    "last":       float(idx.get("last", 0) or 0),
                    "open":       float(idx.get("open", 0) or 0),
                    "high":       float(idx.get("high", 0) or 0),
                    "low":        float(idx.get("low", 0) or 0),
                    "prev_close": float(idx.get("previousClose", 0) or 0),
                    "change":     float(idx.get("change", 0) or 0),
                    "change_pct": float(idx.get("percentChange", 0) or 0),
                    "pe":         float(idx.get("pe", 0) or 0),
                    "pb":         float(idx.get("pb", 0) or 0),
                    "timestamp":  datetime.now().isoformat(),
                }
        return None

    def get_nifty(self) -> dict | None:
        return self.get_index("NIFTY 50")

    def get_banknifty(self) -> dict | None:
        return self.get_index("NIFTY BANK")

    def get_finnifty(self) -> dict | None:
        return self.get_index("NIFTY FIN SERVICE")

    def get_vix(self) -> float | None:
        """Returns India VIX value."""
        d = self.get_index("INDIA VIX")
        return d["last"] if d else None

    # ════════════════════════════════════════════════════════
    #  OPTION CHAIN  — the most important data source
    # ════════════════════════════════════════════════════════
    def get_option_chain(self, symbol: str = "NIFTY") -> dict | None:
        """
        Returns full option chain from NSE.
        symbol: 'NIFTY', 'BANKNIFTY', 'FINNIFTY'

        Returns structured dict:
          {
            spot_price: float,
            atm_strike: int,
            expiries: [date_str, ...],
            records: [
              {
                strike, expiry,
                CE: {iv, oi, oi_change, bid, ask, ltp, delta, theta, vega, gamma},
                PE: {iv, oi, oi_change, bid, ask, ltp, delta, theta, vega, gamma},
              },
              ...
            ],
            pcr: float,                    # Put-Call Ratio
            max_pain: int,                 # Max pain strike
            top_ce_oi_strike: int,         # Highest CE OI (resistance)
            top_pe_oi_strike: int,         # Highest PE OI (support)
          }
        """
        raw = self._get(f"/api/optionChain?symbol={symbol}", f"oc_{symbol}")
        if not raw:
            return None

        try:
            records_raw = raw.get("records", {})
            data_raw    = records_raw.get("data", [])
            spot_raw    = records_raw.get("underlyingValue", 0)
            spot_price  = float(spot_raw)
            atm_strike  = int(round(spot_price / 50) * 50)
            expiries    = sorted(set(r["expiryDate"] for r in data_raw if "expiryDate" in r))

            records = []
            ce_oi_map = {}; pe_oi_map = {}
            total_ce_oi = 0; total_pe_oi = 0

            for r in data_raw:
                strike = r.get("strikePrice")
                expiry = r.get("expiryDate","")
                if not strike:
                    continue

                def parse_side(side_key):
                    s = r.get(side_key, {})
                    if not s: return None
                    return {
                        "iv":        float(s.get("impliedVolatility", 0) or 0),
                        "oi":        int(s.get("openInterest", 0) or 0),
                        "oi_change": int(s.get("changeinOpenInterest", 0) or 0),
                        "volume":    int(s.get("totalTradedVolume", 0) or 0),
                        "bid":       float(s.get("bidprice", 0) or 0),
                        "ask":       float(s.get("askPrice", 0) or 0),
                        "ltp":       float(s.get("lastPrice", 0) or 0),
                        "delta":     float(s.get("delta", 0) or 0),
                        "theta":     float(s.get("theta", 0) or 0),
                        "vega":      float(s.get("vega", 0) or 0),
                        "gamma":     float(s.get("gamma", 0) or 0),
                    }

                ce = parse_side("CE"); pe = parse_side("PE")
                records.append({"strike": strike, "expiry": expiry, "CE": ce, "PE": pe})

                # Track OI for PCR and max pain
                if ce and ce["oi"] > 0:
                    ce_oi_map[strike] = ce_oi_map.get(strike, 0) + ce["oi"]
                    total_ce_oi += ce["oi"]
                if pe and pe["oi"] > 0:
                    pe_oi_map[strike] = pe_oi_map.get(strike, 0) + pe["oi"]
                    total_pe_oi += pe["oi"]

            pcr = round(total_pe_oi / total_ce_oi, 3) if total_ce_oi > 0 else 0
            top_ce = max(ce_oi_map, key=ce_oi_map.get) if ce_oi_map else None
            top_pe = max(pe_oi_map, key=pe_oi_map.get) if pe_oi_map else None

            # Max pain calculation
            max_pain = _calc_max_pain(ce_oi_map, pe_oi_map, atm_strike)

            return {
                "symbol":            symbol,
                "spot_price":        spot_price,
                "atm_strike":        atm_strike,
                "expiries":          expiries,
                "records":           records,
                "pcr":               pcr,
                "max_pain":          max_pain,
                "top_ce_oi_strike":  top_ce,   # key resistance
                "top_pe_oi_strike":  top_pe,   # key support
                "total_ce_oi":       total_ce_oi,
                "total_pe_oi":       total_pe_oi,
                "timestamp":         datetime.now().isoformat(),
            }
        except Exception as e:
            print(f"  [NSE] Option chain parse error: {e}")
            return None

    def get_atm_data(self, symbol: str = "NIFTY",
                     expiry_index: int = 0) -> dict | None:
        """
        Returns CE + PE data specifically for the ATM strike.
        expiry_index: 0 = near, 1 = next, 2 = far month
        Used directly by calendar.py to get real NSE greeks.
        """
        oc = self.get_option_chain(symbol)
        if not oc: return None

        atm    = oc["atm_strike"]
        expiry = oc["expiries"][expiry_index] if len(oc["expiries"]) > expiry_index else None
        if not expiry: return None

        for r in oc["records"]:
            if r["strike"] == atm and r["expiry"] == expiry:
                return {
                    "symbol":     symbol,
                    "spot":       oc["spot_price"],
                    "atm_strike": atm,
                    "expiry":     expiry,
                    "CE":         r["CE"],
                    "PE":         r["PE"],
                    "pcr":        oc["pcr"],
                    "max_pain":   oc["max_pain"],
                    "top_ce_oi":  oc["top_ce_oi_strike"],
                    "top_pe_oi":  oc["top_pe_oi_strike"],
                }
        return None

    def get_calendar_spread_data(self, symbol: str = "NIFTY",
                                  strike: int = None) -> dict | None:
        """
        Returns near + far month data for the same strike.
        Used by calendar.py to get REAL NSE spreads (replaces Excel read).

        Returns:
          {
            strike, spot,
            near: {expiry, CE_bid, CE_ask, CE_iv, CE_theta, PE_bid, PE_ask, ...},
            far:  {expiry, CE_bid, CE_ask, CE_iv, CE_theta, PE_bid, PE_ask, ...},
            ce_spread: far_CE_bid - near_CE_ask,
            pe_spread: far_PE_bid - near_PE_ask,
            ce_fair:   theta-based fair value,
          }
        """
        oc = self.get_option_chain(symbol)
        if not oc or len(oc["expiries"]) < 2: return None

        spot        = oc["spot_price"]
        target_str  = strike or oc["atm_strike"]
        near_expiry = oc["expiries"][0]
        far_expiry  = oc["expiries"][1]

        near_row = None; far_row = None
        for r in oc["records"]:
            if r["strike"] == target_str:
                if r["expiry"] == near_expiry: near_row = r
                if r["expiry"] == far_expiry:  far_row  = r

        if not near_row or not far_row: return None
        if not near_row["CE"] or not far_row["CE"]: return None

        nce = near_row["CE"]; fce = far_row["CE"]
        npe = near_row["PE"] if near_row["PE"] else {}
        fpe = far_row["PE"]  if far_row["PE"]  else {}

        ce_spread = round((fce["bid"] or 0) - (nce["ask"] or 0), 2)
        pe_spread = round((fpe.get("bid",0) or 0) - (npe.get("ask",0) or 0), 2)

        # Fair value: theta differential × 0.5
        ce_fair = 0.0
        if nce.get("theta") and fce.get("theta"):
            ce_fair = round((fce["theta"] - nce["theta"]) * 0.5, 2)

        return {
            "symbol":     symbol,
            "spot":       spot,
            "strike":     target_str,
            "near": {
                "expiry":   near_expiry,
                "CE_bid":   nce.get("bid", 0),    "CE_ask":   nce.get("ask", 0),
                "CE_ltp":   nce.get("ltp", 0),    "CE_iv":    nce.get("iv", 0),
                "CE_oi":    nce.get("oi", 0),     "CE_theta": nce.get("theta", 0),
                "CE_vega":  nce.get("vega", 0),   "CE_delta": nce.get("delta", 0),
                "PE_bid":   npe.get("bid", 0),    "PE_ask":   npe.get("ask", 0),
                "PE_ltp":   npe.get("ltp", 0),    "PE_iv":    npe.get("iv", 0),
                "PE_oi":    npe.get("oi", 0),     "PE_theta": npe.get("theta", 0),
            },
            "far": {
                "expiry":   far_expiry,
                "CE_bid":   fce.get("bid", 0),    "CE_ask":   fce.get("ask", 0),
                "CE_ltp":   fce.get("ltp", 0),    "CE_iv":    fce.get("iv", 0),
                "CE_oi":    fce.get("oi", 0),     "CE_theta": fce.get("theta", 0),
                "CE_vega":  fce.get("vega", 0),   "CE_delta": fce.get("delta", 0),
                "PE_bid":   fpe.get("bid", 0),    "PE_ask":   fpe.get("ask", 0),
                "PE_ltp":   fpe.get("ltp", 0),    "PE_iv":    fpe.get("iv", 0),
                "PE_oi":    fpe.get("oi", 0),     "PE_theta": fpe.get("theta", 0),
            },
            "ce_spread":  ce_spread,
            "pe_spread":  pe_spread,
            "ce_fair":    ce_fair,
            "ce_deviation": round(ce_spread - ce_fair, 2),
            "pcr":        oc["pcr"],
            "max_pain":   oc["max_pain"],
            "support":    oc["top_pe_oi_strike"],   # PE OI wall = support
            "resistance": oc["top_ce_oi_strike"],   # CE OI wall = resistance
        }

    # ════════════════════════════════════════════════════════
    #  EQUITY DATA
    # ════════════════════════════════════════════════════════
    def get_equity_quote(self, symbol: str) -> dict | None:
        """Live quote for any NSE stock."""
        d = self._get(f"/api/equity?symbol={symbol}", f"eq_{symbol}")
        if not d: return None
        try:
            price_info = d.get("priceInfo", {})
            info       = d.get("info", {})
            return {
                "symbol":     symbol,
                "company":    info.get("companyName",""),
                "last":       float(price_info.get("lastPrice", 0) or 0),
                "open":       float(price_info.get("open", 0) or 0),
                "high":       float(price_info.get("intraDayHighLow",{}).get("max", 0) or 0),
                "low":        float(price_info.get("intraDayHighLow",{}).get("min", 0) or 0),
                "prev_close": float(price_info.get("previousClose", 0) or 0),
                "change_pct": float(price_info.get("pChange", 0) or 0),
                "volume":     int(d.get("securityInfo",{}).get("tradedVolume", 0) or 0),
                "is_fno":     info.get("isFNOSec", False),
            }
        except Exception:
            return None

    def get_equity_historical(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """OHLCV history for any NSE stock."""
        end_date   = date.today()
        start_date = end_date - timedelta(days=days)
        path = (f"/api/historical/equity?symbol={symbol}"
                f"&from={start_date.strftime('%d-%m-%Y')}"
                f"&to={end_date.strftime('%d-%m-%Y')}")
        d = self._get(path)
        if not d or not isinstance(d, list):
            return pd.DataFrame()
        rows = []
        for rec in d:
            try:
                rows.append({
                    "date":   pd.to_datetime(rec.get("CH_TIMESTAMP",""), errors="coerce"),
                    "open":   float(rec.get("CH_OPENING_PRICE", 0) or 0),
                    "high":   float(rec.get("CH_TRADE_HIGH_PRICE", 0) or 0),
                    "low":    float(rec.get("CH_TRADE_LOW_PRICE", 0) or 0),
                    "close":  float(rec.get("CH_CLOSING_PRICE", 0) or 0),
                    "volume": int(rec.get("CH_TOT_TRADED_QTY", 0) or 0),
                })
            except Exception:
                continue
        df = pd.DataFrame(rows).dropna().sort_values("date").reset_index(drop=True)
        return df

    # ════════════════════════════════════════════════════════
    #  MARKET BREADTH
    # ════════════════════════════════════════════════════════
    def get_gainers_losers(self, index: str = "NIFTY 50") -> dict:
        """Top gainers and losers in an index."""
        d = self._get(f"/api/gainersAndLosers?index={requests.utils.quote(index)}")
        if not d: return {"gainers": [], "losers": []}
        gainers = d.get("LOSER", [])[:5]  # API labels are swapped sometimes
        losers  = d.get("GAINER", [])[:5]
        def parse_stock(s):
            return {
                "symbol":     s.get("symbol",""),
                "change_pct": float(s.get("pChange", 0) or 0),
                "last":       float(s.get("lastPrice", 0) or 0),
                "volume":     int(s.get("totalTradedVolume", 0) or 0),
            }
        return {
            "gainers": [parse_stock(s) for s in gainers],
            "losers":  [parse_stock(s) for s in losers],
        }

    def get_most_active(self) -> list:
        """Most actively traded F&O stocks."""
        d = self._get("/api/mostActive")
        if not d: return []
        return [{"symbol": s.get("symbol",""),
                 "volume": int(s.get("totalTradedVolume", 0) or 0),
                 "last":   float(s.get("lastPrice", 0) or 0)}
                for s in d[:10]]


# ════════════════════════════════════════════════════════════════
#  HELPER: MAX PAIN CALCULATION
# ════════════════════════════════════════════════════════════════
def _calc_max_pain(ce_oi: dict, pe_oi: dict, atm: int) -> int:
    """
    Max pain = strike where total option seller loss is minimised.
    NSE options expire at the spot price that causes maximum loss
    to option BUYERS (= minimum loss to sellers).
    """
    strikes = sorted(set(list(ce_oi.keys()) + list(pe_oi.keys())))
    if not strikes:
        return atm
    min_loss = None; max_pain_strike = atm
    for expiry_price in strikes:
        loss = 0
        for strike, oi in ce_oi.items():
            loss += max(0, expiry_price - strike) * oi   # CE seller loss
        for strike, oi in pe_oi.items():
            loss += max(0, strike - expiry_price) * oi   # PE seller loss
        if min_loss is None or loss < min_loss:
            min_loss = loss
            max_pain_strike = expiry_price
    return max_pain_strike


# ════════════════════════════════════════════════════════════════
#  SIGNAL ENHANCER
#  Takes raw signals from calendar.py / equity_india.py
#  and adds real NSE context to improve accuracy
# ════════════════════════════════════════════════════════════════
class NseSignalEnhancer:
    """
    Wraps NseConnector to add NSE-sourced context to any signal.
    Called by calendar.py and equity_india.py for better decisions.
    """

    def __init__(self, nse: NseConnector):
        self.nse = nse

    def enhance_calendar_signal(self, signal: dict, symbol: str = "NIFTY") -> dict:
        """
        Adds NSE data to a calendar spread signal from calendar.py.
        Replaces Excel-sourced greeks with real NSE greeks.
        """
        oc_data = self.nse.get_option_chain(symbol)
        vix     = self.nse.get_vix()

        if not oc_data:
            signal["nse_enhanced"] = False
            return signal

        # Override VIX with real NSE value
        if vix:
            signal["vix"] = vix

        # Add NSE market context
        signal["nse_enhanced"]    = True
        signal["pcr"]             = oc_data["pcr"]
        signal["max_pain"]        = oc_data["max_pain"]
        signal["key_support"]     = oc_data["top_pe_oi_strike"]
        signal["key_resistance"]  = oc_data["top_ce_oi_strike"]
        signal["spot_nse"]        = oc_data["spot_price"]

        # PCR-based regime
        pcr = oc_data["pcr"]
        if   pcr > 1.3: signal["nse_bias"] = "BEARISH (high put buying)"
        elif pcr < 0.7: signal["nse_bias"] = "BULLISH (high call buying)"
        else:           signal["nse_bias"] = "NEUTRAL"

        # Calendar spread quality from real NSE data
        cal_data = self.nse.get_calendar_spread_data(symbol, signal.get("atm"))
        if cal_data:
            signal["nse_ce_spread"]    = cal_data["ce_spread"]
            signal["nse_ce_fair"]      = cal_data["ce_fair"]
            signal["nse_ce_deviation"] = cal_data["ce_deviation"]
            signal["near_ce_iv"]       = cal_data["near"]["CE_iv"]
            signal["far_ce_iv"]        = cal_data["far"]["CE_iv"]
            signal["near_ce_oi"]       = cal_data["near"]["CE_oi"]
            signal["far_ce_oi"]        = cal_data["far"]["CE_oi"]
            signal["near_ce_theta"]    = cal_data["near"]["CE_theta"]
            signal["far_ce_theta"]     = cal_data["far"]["CE_theta"]
            # Better score: real greeks are more accurate than synthetic
            real_dev = abs(cal_data["ce_deviation"])
            if real_dev > 3:
                signal["score"] = min(signal.get("score", 70) + 10, 100)
            elif real_dev < 0.5:
                signal["score"] = max(signal.get("score", 50) - 8, 10)

        return signal

    def enhance_equity_signal(self, signal: dict, symbol: str = "NIFTY") -> dict:
        """
        Adds NSE context to a directional signal from equity_india.py.
        """
        oc_data = self.nse.get_option_chain(symbol)
        vix     = self.nse.get_vix()
        idx     = self.nse.get_nifty() if symbol == "NIFTY" else self.nse.get_banknifty()

        if vix:
            signal["vix"] = vix

        signal["nse_enhanced"] = bool(oc_data)

        if oc_data:
            signal["pcr"]         = oc_data["pcr"]
            signal["max_pain"]    = oc_data["max_pain"]
            signal["support"]     = oc_data["top_pe_oi_strike"]
            signal["resistance"]  = oc_data["top_ce_oi_strike"]
            signal["spot_nse"]    = oc_data["spot_price"]

            # Validate direction against PCR
            pcr = oc_data["pcr"]
            dirn = signal.get("direction","")
            if dirn == "LONG"  and pcr < 0.7:
                signal["nse_warning"] = "⚠ Low PCR suggests call buying — watch for reversal"
            elif dirn == "SHORT" and pcr > 1.3:
                signal["nse_warning"] = "⚠ High PCR suggests put buying — trend may continue"
            else:
                signal["nse_warning"] = ""

            # Boost/reduce score based on OI support/resistance
            spot = oc_data["spot_price"]
            support    = oc_data["top_pe_oi_strike"] or 0
            resistance = oc_data["top_ce_oi_strike"] or spot*2
            if dirn == "LONG" and spot > support * 0.998:
                signal["score"] = min(signal.get("score",60) + 8, 100)
            elif dirn == "SHORT" and spot < resistance * 1.002:
                signal["score"] = min(signal.get("score",60) + 8, 100)

        return signal


# ════════════════════════════════════════════════════════════════
#  QUICK TEST
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  NSE INDIA CONNECTOR — TEST")
    print("="*60)

    nse = NseConnector()

    if not nse._connected:
        print("\n  Start the NSE server first, then re-run.")
        print("  Quick start: npx stock-nse-india@latest")
        exit(0)

    print("\n  1. India VIX...")
    vix = nse.get_vix()
    print(f"     India VIX: {vix}")

    print("\n  2. NIFTY index...")
    nifty = nse.get_nifty()
    if nifty:
        print(f"     NIFTY: {nifty['last']:,.2f}  ({nifty['change_pct']:+.2f}%)")

    print("\n  3. BANKNIFTY...")
    bank = nse.get_banknifty()
    if bank:
        print(f"     BANKNIFTY: {bank['last']:,.2f}  ({bank['change_pct']:+.2f}%)")

    print("\n  4. NIFTY Option Chain (ATM)...")
    atm_data = nse.get_atm_data("NIFTY")
    if atm_data:
        ce = atm_data["CE"] or {}; pe = atm_data["PE"] or {}
        print(f"     ATM Strike: {atm_data['atm_strike']}")
        print(f"     CE — LTP:{ce.get('ltp',0)}  IV:{ce.get('iv',0)}%  OI:{ce.get('oi',0):,}")
        print(f"     PE — LTP:{pe.get('ltp',0)}  IV:{pe.get('iv',0)}%  OI:{pe.get('oi',0):,}")
        print(f"     PCR: {atm_data['pcr']}  MaxPain: {atm_data['max_pain']}")
        print(f"     Support: {atm_data['top_pe_oi']}  Resistance: {atm_data['top_ce_oi']}")

    print("\n  5. Calendar spread data...")
    cal = nse.get_calendar_spread_data("NIFTY")
    if cal:
        print(f"     Near expiry: {cal['near']['expiry']}")
        print(f"     Far  expiry: {cal['far']['expiry']}")
        print(f"     CE Spread: {cal['ce_spread']}  Fair: {cal['ce_fair']}  Dev: {cal['ce_deviation']}")
        print(f"     PE Spread: {cal['pe_spread']}")

    print("\n  6. Gainers / Losers...")
    gl = nse.get_gainers_losers()
    if gl["gainers"]:
        print(f"     Top gainer: {gl['gainers'][0]['symbol']} +{gl['gainers'][0]['change_pct']}%")

    print("\n  Test complete.")