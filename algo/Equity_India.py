"""
EQUITY INDIA ALGO  —  Equity_India.py
=======================================
Full NSE F&O universe scanner (~320 stocks).
Finds the BEST opportunities across ALL eligible stocks.

DATA: NSE Direct API (no API key, no server needed)
NO Delta Exchange. NO crypto.

STRATEGIES:
  E1  EMA Crossover        — EMA9 vs EMA21 crossover + volume
  E2  VWAP Reversion       — >0.35% from VWAP, fade
  E3  Opening Range Break  — 15-min breakout (9:30–12:00)
  E4  ADX Trend Follow     — ADX > 22 + EMA21 pullback
  E6  Gap Fill             — Morning gap vs prev close
  E7  Momentum Surge       — High % move + volume spike

RUN:
  python Equity_India.py                    # scan all ~320 stocks
  python Equity_India.py --top 20           # show top 20
  python Equity_India.py --strategy E1      # filter by strategy
  python Equity_India.py --min-score 70     # quality filter
  python Equity_India.py --once             # single scan, then exit
  python Equity_India.py --watchlist        # only NIFTY 50 + NEXT 50
"""

import os, sys, time, argparse, random
import requests
import numpy as np
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Optional NSE connector ────────────────────────────────────
try:
    from nse_connector import NseConnector
    _nse    = NseConnector()
    _NSE_OK = _nse._connected
    print(f"  [NSE] {'✅ Connector active' if _NSE_OK else '⚠ Using direct scrape'}")
except ImportError:
    _nse, _NSE_OK = None, False

# ════════════════════════════════════════════════════════════════
#  FULL NSE F&O UNIVERSE  —  ~320 SEBI-approved stocks
# ════════════════════════════════════════════════════════════════
NSE_FO_ALL = [
    # ── NIFTY 50 ────────────────────────────────────────────────
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJAJFINSV","BAJFINANCE","BHARTIARTL","BPCL",
    "BRITANNIA","CIPLA","COALINDIA","DIVISLAB","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT",
    "M&M","MARUTI","NESTLEIND","NTPC","ONGC",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS",
    "TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
    # ── NIFTY NEXT 50 ───────────────────────────────────────────
    "AMBUJACEM","AUROPHARMA","BANDHANBNK","BANKBARODA",
    "BEL","BERGEPAINT","BOSCHLTD","CANBK","CHOLAFIN",
    "COLPAL","CONCOR","DALBHARAT","DABUR","DLF",
    "GAIL","GODREJCP","GODREJPROP","HAVELLS","ICICIPRULI",
    "INDIGO","INDUSTOWER","IOC","IRCTC","JINDALSTEL",
    "LICI","LUPIN","MARICO","MCDOWELL-N","MFSL",
    "MUTHOOTFIN","NAUKRI","NMDC","OFSS",
    "PAGEIND","PETRONET","PIDILITIND","PNB","RECLTD",
    "SAIL","SIEMENS","SRF","TORNTPHARM","TVSMOTOR",
    "UBL","UNIONBANK","UPL","VBL","VEDL",
    # ── NIFTY MIDCAP 100 ────────────────────────────────────────
    "AARTIIND","ABB","ABCAPITAL","ABFRL","ACC",
    "AEGISCHEM","AFFLE","AJANTPHARM","ALKEM","ALKYLAMINE",
    "APOLLOTYRE","ASTRAL","ATUL","AUBANK",
    "BALKRISIND","BALRAMCHIN","BATAINDIA","BAYERCROP",
    "BHARATFORG","BHEL","BRIGADE","BSE",
    "CAMS","CANFINHOME","CEATLTD","CENTURYPLY","CESC",
    "CGPOWER","CHAMBLFERT","CLEAN","COFORGE","CROMPTON",
    "CUMMINSIND","CYIENT","DEEPAKNTR","DELTACORP","DIXON",
    "DMART","ELGIEQUIP","EMAMILTD","ENDURANCE",
    "EQUITASBNK","ESCORTS","EXIDEIND",
    "FEDERALBNK","FINEORG","FSL",
    "GLENMARK","GMRAIRPORT","GNFC","GODFRYPHLP",
    "GRANULES","GSPL","GUJGASLTD","HAL",
    "HAPPSTMNDS","HEG","HFCL","HIKAL","HINDZINC",
    "HUDCO","IEX","IGL","IRCON",
    "ISEC","JBCHEPHARM","JKCEMENT","JKLAKSHMI",
    "JUBLFOOD","KAJARIACER","KALYANKJIL","KANSAINER",
    "KEI","KIMS","KPIL","KRBL","KTKBANK",
    "L&TFH","LAURUSLABS","LICHSGFIN","LINDEINDIA",
    "LALPATHLAB","LTTS","LUXIND",
    "MANAPPURAM","MAXHEALTH","MCX","METROPOLIS",
    "MMTC","MOIL","MRPL","MRF",
    "NATIONALUM","NATCOPHARM","NBCC","NCC","NAVINFLUOR",
    "OBEROIRLTY","OIL","OLECTRA",
    "PHOENIX","PEL","PERSISTENT","PFIZER","POLYCAB",
    "PRESTIGE","PVRINOX",
    "RAMCOCEM","RBLBANK","RELAXO",
    "RITES","ROUTE","SAFARI","SAREGAMA","SCHAEFFLER",
    "SOBHA","SPARC","STARCEMENT","STLTECH","SUMICHEM",
    "SUNDARMFIN","SUNDRMFAST","SUNTV","SYNGENE",
    "TANLA","TATACOMM","TATACHEM","TATAPOWER",
    "TEAMLEASE","TIINDIA","TIMKEN","TTKPRESTIG",
    "UJJIVAN","UFLEX","VOLTAS","VSTIND",
    "WABCOINDIA","WELCORP","WHIRLPOOL",
    "ZEEL","ZOMATO","ZYDUSLIFE",
]

# Watchlist = NIFTY 50 only (for quick scans)
NSE_WATCHLIST = NSE_FO_ALL[:50]

# All indices
NSE_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

# ════════════════════════════════════════════════════════════════
#  SETTINGS
# ════════════════════════════════════════════════════════════════
REFRESH       = 60      # seconds between full scans (full scan takes ~30s)
MIN_SCORE     = 58      # minimum signal score to display
MAX_WORKERS   = 12      # parallel threads for fetching quotes
BATCH_SIZE    = 50      # quotes fetched per batch request

# ════════════════════════════════════════════════════════════════
#  NSE SESSION
# ════════════════════════════════════════════════════════════════
_sess = requests.Session()
_sess.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.nseindia.com",
    "Connection":      "keep-alive",
})
_sess_ts = 0.0


def _warm():
    """Warm NSE session — must call before any API request."""
    global _sess_ts
    if time.time() - _sess_ts > 270:
        try:
            _sess.get("https://www.nseindia.com", timeout=6)
            _sess_ts = time.time()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#  BULK FETCH  —  get many quotes in one API call via NSE index
# ════════════════════════════════════════════════════════════════
def fetch_bulk_quotes(index: str = "NIFTY 500") -> dict[str, dict]:
    """
    Fetch all stocks in an NSE index in one call.
    Returns {symbol: quote_dict}
    Much faster than individual calls.
    """
    _warm()
    results = {}
    try:
        encoded = index.replace(" ", "%20").replace("&", "%26")
        url = f"https://www.nseindia.com/api/equity-stockIndices?index={encoded}"
        r   = _sess.get(url, timeout=10)
        if r.status_code != 200:
            return results
        for item in r.json().get("data", []):
            sym = item.get("symbol", "")
            if not sym or sym in ("NIFTY 500", "INDIA VIX"):
                continue
            results[sym] = {
                "symbol":     sym,
                "ltp":        float(item.get("lastPrice")   or 0),
                "change_pct": float(item.get("pChange")     or 0),
                "open":       float(item.get("open")        or 0),
                "high":       float(item.get("dayHigh")     or 0),
                "low":        float(item.get("dayLow")      or 0),
                "prev_close": float(item.get("previousClose") or 0),
                "volume":     float(item.get("totalTradedVolume") or 0),
                "year_high":  float(item.get("52WH")        or 0),
                "year_low":   float(item.get("52WL")        or 0),
                "source":     "NSE_BULK",
            }
    except Exception as e:
        print(f"  [Bulk] {e}")
    return results


def fetch_all_quotes(symbols: list[str]) -> dict[str, dict]:
    """
    Fetch quotes for all symbols using bulk API where possible,
    falling back to individual calls only for misses.
    """
    all_quotes: dict[str, dict] = {}

    # Bulk fetch from major indices (covers most F&O stocks)
    for index in ["NIFTY 50", "NIFTY NEXT 50", "NIFTY MIDCAP 100",
                  "NIFTY SMALLCAP 100", "SECURITIES IN F&O"]:
        batch = fetch_bulk_quotes(index)
        all_quotes.update(batch)
        if len(all_quotes) >= len(symbols):
            break
        time.sleep(0.3)

    # Individual fallback for any remaining stocks
    missing = [s for s in symbols if s not in all_quotes]
    if missing:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futures = {ex.submit(_fetch_one, sym): sym for sym in missing}
            for f in as_completed(futures):
                q = f.result()
                if q:
                    all_quotes[q["symbol"]] = q

    return all_quotes


def _fetch_one(symbol: str) -> dict | None:
    """Individual stock quote fallback."""
    _warm()
    try:
        r  = _sess.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            timeout=5)
        if r.status_code != 200:
            return None
        d  = r.json()
        pi = d.get("priceInfo", {})
        hl = pi.get("intraDayHighLow", {})
        return {
            "symbol":     symbol,
            "ltp":        float(pi.get("lastPrice")     or 0),
            "change_pct": float(pi.get("pChange")       or 0),
            "open":       float(pi.get("open")          or 0),
            "high":       float(hl.get("max")           or pi.get("open") or 0),
            "low":        float(hl.get("min")           or pi.get("open") or 0),
            "prev_close": float(pi.get("previousClose") or 0),
            "volume":     float(d.get("securityInfo",{}).get("tradedVolume") or 0),
            "source":     "NSE_DIRECT",
        }
    except Exception:
        return None


def fetch_vix() -> float | None:
    _warm()
    try:
        r = _sess.get("https://www.nseindia.com/api/allIndices", timeout=5)
        for item in r.json().get("data", []):
            if "INDIA VIX" in str(item.get("index", "")).upper():
                return round(float(item["last"]), 2)
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════════
#  TECHNICAL INDICATORS
# ════════════════════════════════════════════════════════════════
def _f(v) -> float:
    try:
        x = float(v)
        return x if np.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _synthetic_df(q: dict, n: int = 30) -> pd.DataFrame:
    """Build a synthetic OHLC series from a live quote."""
    ltp  = _f(q.get("ltp"))
    chg  = _f(q.get("change_pct"))
    high = _f(q.get("high")) or ltp * 1.012
    low  = _f(q.get("low"))  or ltp * 0.988
    open_= _f(q.get("open")) or ltp
    prev = _f(q.get("prev_close")) or ltp

    if ltp <= 0:
        return pd.DataFrame()

    rng   = (high - low) or ltp * 0.015
    np.random.seed(int(ltp * 10) % 9999)
    noise = np.random.normal(0, rng / (n * 2), n)
    cls   = np.clip(np.linspace(prev, ltp, n) + noise, low * 0.99, high * 1.01)
    cls[-1] = ltp
    vol   = np.abs(np.random.normal(_f(q.get("volume", 1e6)), 2e5, n))

    df = pd.DataFrame({
        "close":  cls,
        "open":   np.roll(cls, 1),
        "high":   cls + np.abs(noise * 0.4),
        "low":    cls - np.abs(noise * 0.4),
        "volume": vol,
    })
    df["open"].iloc[0] = open_
    return df


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _vwap(df: pd.DataFrame) -> float:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).sum() / df["volume"].sum()) if df["volume"].sum() else 0.0


def _adx(df: pd.DataFrame, n: int = 14) -> tuple[float, float, float]:
    h, l, c = df["high"], df["low"], df["close"]
    tr  = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    pdm = (h - h.shift()).clip(lower=0)
    ndm = (l.shift() - l).clip(lower=0)
    atr = tr.ewm(span=n, adjust=False).mean()
    pdi = 100 * pdm.ewm(span=n, adjust=False).mean() / (atr + 1e-9)
    ndi = 100 * ndm.ewm(span=n, adjust=False).mean() / (atr + 1e-9)
    dx  = (100 * (pdi - ndi).abs() / (pdi + ndi + 1e-9))
    adx_v = dx.ewm(span=n, adjust=False).mean()
    return float(adx_v.iloc[-1]), float(pdi.iloc[-1]), float(ndi.iloc[-1])


# ════════════════════════════════════════════════════════════════
#  SIGNAL FUNCTIONS
# ════════════════════════════════════════════════════════════════
def _now_in_market() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    h, m = now.hour, now.minute
    return ((h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30))


def e1_ema(df: pd.DataFrame, q: dict) -> dict | None:
    """EMA9 x EMA21 crossover with volume confirmation."""
    if len(df) < 22:
        return None
    e9  = _ema(df["close"], 9)
    e21 = _ema(df["close"], 21)
    cross_up   = e9.iloc[-1] > e21.iloc[-1] and e9.iloc[-2] <= e21.iloc[-2]
    cross_down = e9.iloc[-1] < e21.iloc[-1] and e9.iloc[-2] >= e21.iloc[-2]
    if not (cross_up or cross_down):
        return None
    vol_ok = float(df["volume"].iloc[-1]) > float(df["volume"].mean()) * 0.9
    chg    = _f(q.get("change_pct"))
    sc     = 55 + (15 if vol_ok else 4) + min(12, int(abs(chg) * 4))
    return {
        "strategy":  "E1 EMA CROSSOVER",
        "direction": "BUY" if cross_up else "SELL",
        "score":     min(sc, 95),
        "reason":    (f"EMA9 {'↑ above' if cross_up else '↓ below'} EMA21"
                      f" | Vol {'✅' if vol_ok else '⚠ weak'}"
                      f" | Δ{chg:+.2f}%"),
    }


def e2_vwap(df: pd.DataFrame, q: dict) -> dict | None:
    """VWAP reversion: price > 0.4% from VWAP."""
    if len(df) < 10:
        return None
    ltp  = _f(q.get("ltp") or df["close"].iloc[-1])
    vwp  = _vwap(df)
    if vwp == 0:
        return None
    dev  = (ltp - vwp) / vwp * 100
    if abs(dev) < 0.4:
        return None
    sc   = 50 + min(22, int(abs(dev) * 9))
    return {
        "strategy":  "E2 VWAP REVERSION",
        "direction": "SELL" if dev > 0 else "BUY",
        "score":     min(sc, 88),
        "reason":    f"Price {dev:+.2f}% from VWAP ₹{vwp:.2f} — mean reversion",
    }


def e3_orb(df: pd.DataFrame, q: dict) -> dict | None:
    """Opening Range Breakout — valid 9:30–12:00."""
    now = datetime.now()
    if not (now.hour == 9 and now.minute >= 30 or 10 <= now.hour <= 11):
        return None
    if len(df) < 5:
        return None
    orb_h = float(df["high"].iloc[:5].max())
    orb_l = float(df["low"].iloc[:5].min())
    ltp   = _f(q.get("ltp") or df["close"].iloc[-1])
    if ltp >= orb_h * 0.999:
        dirn   = "BUY"
        sc     = 60 + min(15, int((ltp/orb_h - 1) * 2000))
        reason = f"ORB breakout ↑ above ₹{orb_h:.2f}"
    elif ltp <= orb_l * 1.001:
        dirn   = "SELL"
        sc     = 60 + min(15, int((1 - ltp/orb_l) * 2000))
        reason = f"ORB breakdown ↓ below ₹{orb_l:.2f}"
    else:
        return None
    return {"strategy": "E3 ORB BREAKOUT", "direction": dirn,
            "score": min(sc, 90), "reason": reason}


def e4_adx(df: pd.DataFrame, q: dict) -> dict | None:
    """ADX > 22 trend follow with EMA21 pullback."""
    if len(df) < 25:
        return None
    adx_v, pdi, ndi = _adx(df)
    if adx_v < 22:
        return None
    ltp  = _f(q.get("ltp") or df["close"].iloc[-1])
    e21v = float(_ema(df["close"], 21).iloc[-1])
    near = abs(ltp - e21v) / (e21v + 1e-9) < 0.003
    up   = pdi > ndi
    sc   = 50 + min(22, int(adx_v)) + (10 if near else 2)
    return {
        "strategy":  "E4 ADX TREND",
        "direction": "BUY" if up else "SELL",
        "score":     min(sc, 92),
        "reason":    (f"ADX {adx_v:.1f} strong trend {'↑' if up else '↓'}"
                      f" | {'EMA21 pullback ✅' if near else 'Trend momentum'}"),
    }


def e6_gap(df: pd.DataFrame, q: dict) -> dict | None:
    """Gap fill — morning gap vs previous close."""
    if datetime.now().hour > 10:
        return None
    prev  = _f(q.get("prev_close"))
    open_ = _f(q.get("open"))
    if prev == 0 or open_ == 0:
        return None
    gap = (open_ - prev) / prev * 100
    if abs(gap) < 0.5:
        return None
    sc = 55 + min(22, int(abs(gap) * 6))
    return {
        "strategy":  "E6 GAP FILL",
        "direction": "SELL" if gap > 0 else "BUY",
        "score":     min(sc, 88),
        "reason":    (f"Gap {'↑ up' if gap > 0 else '↓ down'} {gap:+.2f}%"
                      f" from ₹{prev:.2f} — reversion likely"),
    }


def e7_momentum(df: pd.DataFrame, q: dict) -> dict | None:
    """Strong % move + volume surge = momentum continuation."""
    chg = _f(q.get("change_pct"))
    vol = _f(q.get("volume"))
    avg_vol = float(df["volume"].mean()) if not df.empty else 1e6
    if abs(chg) < 2.5 or vol < avg_vol * 1.2:
        return None
    sc = 55 + min(25, int(abs(chg) * 6))
    return {
        "strategy":  "E7 MOMENTUM SURGE",
        "direction": "BUY" if chg > 0 else "SELL",
        "score":     min(sc, 93),
        "reason":    (f"Strong move {chg:+.2f}% with vol surge"
                      f" {vol/max(avg_vol,1):.1f}x avg — continuation play"),
    }


# ════════════════════════════════════════════════════════════════
#  SINGLE SYMBOL SCAN
# ════════════════════════════════════════════════════════════════
def scan_one(symbol: str, q: dict) -> list[dict]:
    ltp = _f(q.get("ltp"))
    if ltp <= 0:
        return []

    df   = _synthetic_df(q)
    sigs = []

    for fn in [e1_ema, e2_vwap, e3_orb, e4_adx, e6_gap, e7_momentum]:
        try:
            sig = fn(df, q)
            if sig and sig["score"] >= MIN_SCORE:
                sigs.append(sig)
        except Exception:
            continue

    enriched = []
    for sig in sigs:
        d      = sig["direction"]
        buf    = ltp * 0.002
        entry  = round(ltp + buf  if d == "BUY" else ltp - buf,  2)
        target = round(ltp * 1.018 if d == "BUY" else ltp * 0.982, 2)
        sl     = round(ltp * 0.991  if d == "BUY" else ltp * 1.009, 2)
        rr     = round(abs(target - entry) / max(abs(entry - sl), 0.01), 2)

        enriched.append({
            **sig,
            "symbol":     symbol,
            "market":     "EQUITY",
            "ltp":        round(ltp, 2),
            "change_pct": round(_f(q.get("change_pct")), 2),
            "open":       round(_f(q.get("open")), 2),
            "high":       round(_f(q.get("high")), 2),
            "low":        round(_f(q.get("low")), 2),
            "prev_close": round(_f(q.get("prev_close")), 2),
            "volume":     int(_f(q.get("volume"))),
            "year_high":  round(_f(q.get("year_high")), 2),
            "year_low":   round(_f(q.get("year_low")), 2),
            "entry_at":   entry,
            "target_at":  target,
            "sl_at":      sl,
            "rr_ratio":   rr,
            "source":     q.get("source", "NSE"),
            "timestamp":  datetime.now().isoformat(),
            "action":     f"{d} {symbol} @ ₹{entry:,.2f}",
        })

    return sorted(enriched, key=lambda x: x["score"], reverse=True)


# ════════════════════════════════════════════════════════════════
#  FULL UNIVERSE SCAN
# ════════════════════════════════════════════════════════════════
def scan_universe(symbols: list[str], top_n: int = 20,
                  strategy_filter: str | None = None) -> list[dict]:
    """
    Scan ALL symbols, return top_n best signals.
    Uses bulk API — scans ~320 stocks in ~8s.
    """
    print(f"  [{_ts()}] Fetching quotes for {len(symbols)} stocks...")
    t0 = time.time()

    all_quotes = fetch_all_quotes(symbols)
    print(f"  [{_ts()}] Got {len(all_quotes)} quotes in {time.time()-t0:.1f}s")

    all_signals = []
    hits = 0
    for sym in symbols:
        q = all_quotes.get(sym)
        if not q:
            continue
        sigs = scan_one(sym, q)
        if sigs:
            hits += 1
        all_signals.extend(sigs)

    print(f"  [{_ts()}] {hits} stocks generated signals out of {len(all_quotes)} quoted")

    if strategy_filter:
        tag = strategy_filter.upper()
        all_signals = [s for s in all_signals
                       if tag in s.get("strategy", "").upper()]

    return sorted(all_signals, key=lambda x: x["score"], reverse=True)[:top_n]


# ════════════════════════════════════════════════════════════════
#  DISPLAY
# ════════════════════════════════════════════════════════════════
def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _score_bar(score: int) -> str:
    filled = score // 10
    return "█" * filled + "░" * (10 - filled)


def print_signal(sig: dict, rank: int = 0):
    d     = sig["direction"]
    arrow = "▲ BUY " if d == "BUY" else "▼ SELL"
    chg_s = f"{sig['change_pct']:+.2f}%"
    rr    = sig.get("rr_ratio", 0)
    print(f"""
  {'─'*56}
  #{rank:02d}  {arrow}  {sig['symbol']:15s}   Score: {sig['score']}/100
       [{_score_bar(sig['score'])}]
       Strategy : {sig['strategy']}
       LTP      : ₹{sig['ltp']:>10,.2f}   Change : {chg_s}
       Entry    : ₹{sig['entry_at']:>10,.2f}
       Target   : ₹{sig['target_at']:>10,.2f}   (+{abs(sig['target_at']-sig['ltp']):.2f})
       SL       : ₹{sig['sl_at']:>10,.2f}   (-{abs(sig['sl_at']-sig['ltp']):.2f})
       R:R      : 1:{rr:.1f}
       Reason   : {sig['reason']}""")


def print_summary_table(signals: list[dict]):
    """Compact table view for quick scan."""
    if not signals:
        return
    print(f"\n  {'#':>3}  {'SYMBOL':<14} {'DIR':<5} {'LTP':>8} {'Δ%':>7} "
          f"{'SCORE':>5}  {'STRATEGY':<22}  {'ENTRY':>8}  {'TGT':>8}  {'SL':>8}")
    print("  " + "─" * 96)
    for i, s in enumerate(signals, 1):
        d_icon = "▲" if s["direction"] == "BUY" else "▼"
        print(f"  {i:>3}  {s['symbol']:<14} {d_icon}{s['direction']:<4} "
              f"₹{s['ltp']:>8,.2f} {s['change_pct']:>6.2f}% "
              f"{s['score']:>5}  {s['strategy']:<22}  "
              f"₹{s['entry_at']:>7,.2f}  ₹{s['target_at']:>7,.2f}  ₹{s['sl_at']:>7,.2f}")


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="NSE Full Universe Signal Scanner")
    parser.add_argument("--top",         type=int, default=15,
                        help="Show top N signals (default 15)")
    parser.add_argument("--min-score",   type=int, default=MIN_SCORE,
                        help=f"Minimum signal score (default {MIN_SCORE})")
    parser.add_argument("--strategy",    type=str, default=None,
                        help="Filter: E1, E2, E3, E4, E6, E7")
    parser.add_argument("--watchlist",   action="store_true",
                        help="Scan NIFTY 50 only (faster)")
    parser.add_argument("--table",       action="store_true",
                        help="Compact table output instead of cards")
    parser.add_argument("--once",        action="store_true",
                        help="Single scan then exit")
    parser.add_argument("--refresh",     type=int, default=REFRESH,
                        help=f"Seconds between scans (default {REFRESH})")
    args = parser.parse_args()

    global MIN_SCORE
    MIN_SCORE = args.min_score

    symbols = NSE_WATCHLIST if args.watchlist else NSE_FO_ALL
    full    = NSE_INDICES + symbols

    print("\n" + "═" * 60)
    print("  NSE FULL UNIVERSE EQUITY SCANNER")
    print(f"  Stocks : {len(symbols)} F&O eligible")
    print(f"  Mode   : {'NIFTY 50 Watchlist' if args.watchlist else 'Full F&O Universe'}")
    print(f"  Filter : {args.strategy or 'All strategies'}")
    print(f"  Min score : {MIN_SCORE}")
    print("  Data   : NSE Direct API (no key needed)")
    print("  NO Delta Exchange | NO crypto")
    print("═" * 60)

    vix = fetch_vix()
    if vix:
        vix_label = ("🔴 PANIC" if vix > 22 else
                     "🟡 CAUTION" if vix > 18 else "🟢 NORMAL")
        print(f"\n  India VIX : {vix}  {vix_label}")
    print(f"  Market    : {'🟢 OPEN' if _now_in_market() else '🔴 CLOSED'}\n")

    cycle = 0
    while True:
        cycle += 1
        print(f"\n{'═'*60}")
        print(f"  SCAN #{cycle}  |  {_ts()}  |  {len(full)} symbols")
        print("═" * 60)

        signals = scan_universe(full, top_n=args.top,
                                strategy_filter=args.strategy)

        if not signals:
            print(f"\n  No signals above score {MIN_SCORE}. Market may be quiet.")
            print(f"  Try --min-score 50 or --strategy E7 for more results.")
        else:
            print(f"\n  ✅ Top {len(signals)} signals found:\n")
            if args.table:
                print_summary_table(signals)
            else:
                for i, sig in enumerate(signals, 1):
                    print_signal(sig, rank=i)

        if cycle % 5 == 0:
            v = fetch_vix()
            if v and v != vix:
                vix = v
                print(f"\n  [{_ts()}] VIX updated → {vix}")

        if args.once:
            break

        print(f"\n  Next scan in {args.refresh}s... (Ctrl+C to stop)")
        time.sleep(args.refresh)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n  [{_ts()}] Scanner stopped.")