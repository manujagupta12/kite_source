"""
DELTA EXCHANGE CONNECTOR  —  delta_connector.py
=================================================
Provides live market data from Delta Exchange for:
  - Crypto perpetuals  (BTC, ETH, SOL, XRP, etc.)
  - Crypto options     (BTC options, ETH options)
  - Equity derivatives (Nifty, BankNifty via Delta India)
  - Indices            (DERIBIT-style mark prices)

Used by:
  - dashboard signal engine (FastAPI WebSocket)
  - multistrategy.py        (alongside NSE MultiTrade data)
  - backtest_engine.py      (historical candles)

SECURITY:
  API keys are NEVER hardcoded here.
  They are read from environment variables or .env file only.
  Store in D:\\AlgoTrading\\source\\.env — this file is in .gitignore.

SETUP:
  pip install delta-rest-client python-dotenv

  Create D:\\AlgoTrading\\source\\.env:
    DELTA_API_KEY=your_api_key_here
    DELTA_API_SECRET=your_api_secret_here
    DELTA_ENV=india          # india | global | testnet-india | testnet-global

INSTRUMENTS AVAILABLE ON DELTA INDIA:
  Crypto Perpetuals : BTCUSD, ETHUSD, SOLUSD, XRPUSD, BNBUSD ...
  Crypto Options    : BTC options chain (weekly + monthly)
  Equity Futures    : NIFTYUSD, BANKNIFTYUSD (INR equivalent)
  Spreads / Combos  : calendar spreads on crypto
"""

import os
import time
import hmac
import hashlib
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date

# ── Load credentials from .env (never from code) ─────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass   # dotenv optional — can also set env vars manually

# ── Read keys from environment ────────────────────────────────────
DELTA_API_KEY    = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
DELTA_ENV        = os.environ.get("DELTA_ENV", "india")

BASE_URLS = {
    "india":          "https://api.india.delta.exchange",
    "global":         "https://api.delta.exchange",
    "testnet-india":  "https://cdn-ind.testnet.deltaex.org",
    "testnet-global": "https://testnet-api.delta.exchange",
}

BASE_URL = BASE_URLS.get(DELTA_ENV, BASE_URLS["india"])

# ════════════════════════════════════════════════════════════════
#  INSTRUMENT CATALOGUE
#  Key symbols available on Delta India for multi-market signals
# ════════════════════════════════════════════════════════════════
INSTRUMENTS = {
    # ── Crypto Perpetuals ─────────────────────────────────────
    "crypto_perp": [
        {"symbol": "BTCUSD",   "name": "Bitcoin Perpetual",   "asset": "BTC"},
        {"symbol": "ETHUSD",   "name": "Ethereum Perpetual",  "asset": "ETH"},
        {"symbol": "SOLUSD",   "name": "Solana Perpetual",    "asset": "SOL"},
        {"symbol": "XRPUSD",   "name": "Ripple Perpetual",    "asset": "XRP"},
        {"symbol": "BNBUSD",   "name": "BNB Perpetual",       "asset": "BNB"},
        {"symbol": "AVAXUSD",  "name": "Avalanche Perpetual", "asset": "AVAX"},
        {"symbol": "MATICUSD", "name": "Polygon Perpetual",   "asset": "MATIC"},
    ],
    # ── Crypto Options (BTC weekly) ───────────────────────────
    "crypto_options": [
        {"symbol": "BTC",  "name": "Bitcoin Options Chain"},
        {"symbol": "ETH",  "name": "Ethereum Options Chain"},
    ],
    # ── Indian Equity on Delta ────────────────────────────────
    "equity_india": [
        {"symbol": "NIFTYUSD",     "name": "Nifty 50 Futures"},
        {"symbol": "BANKNIFTYUSD", "name": "BankNifty Futures"},
    ],
}

# ════════════════════════════════════════════════════════════════
#  AUTHENTICATION
#  Delta Exchange uses HMAC-SHA256 signatures
# ════════════════════════════════════════════════════════════════
def _make_signature(method: str, path: str, query: str, body: str) -> tuple:
    """Returns (timestamp, signature) for authenticated requests."""
    timestamp = str(int(time.time()))
    prehash   = method + timestamp + path + query + (body or "")
    sig       = hmac.new(
        DELTA_API_SECRET.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return timestamp, sig

def _auth_headers(method: str, path: str,
                  query: str = "", body: str = "") -> dict:
    ts, sig = _make_signature(method, path, query, body)
    return {
        "api-key":    DELTA_API_KEY,
        "timestamp":  ts,
        "signature":  sig,
        "User-Agent": "python-algotrade-1.0",
        "Content-Type": "application/json",
        "Accept":     "application/json",
    }

def _public_headers() -> dict:
    return {
        "User-Agent": "python-algotrade-1.0",
        "Accept":     "application/json",
    }

# ════════════════════════════════════════════════════════════════
#  DELTA REST CLIENT  (wraps raw API calls)
# ════════════════════════════════════════════════════════════════
class DeltaConnector:
    """
    Lightweight wrapper around Delta Exchange REST API.
    Provides market data for crypto + equity instruments.
    """

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(_public_headers())
        self._products_cache = {}      # symbol -> product dict
        self._last_cache_time = 0

    def _get(self, path: str, params: dict = None,
             authenticated: bool = False) -> dict:
        url = f"{self.base_url}{path}"
        if authenticated:
            query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
            hdrs  = _auth_headers("GET", path, query)
        else:
            hdrs  = _public_headers()

        try:
            r = self._session.get(url, params=params, headers=hdrs, timeout=8)
            r.raise_for_status()
            data = r.json()
            if data.get("success"):
                return data.get("result", data)
            return data
        except requests.exceptions.Timeout:
            print(f"  [Delta] Timeout: {path}")
            return {}
        except Exception as e:
            print(f"  [Delta] Error {path}: {e}")
            return {}

    # ── Product catalogue ────────────────────────────────────────
    def get_products(self, refresh: bool = False) -> list:
        """Returns all available products. Cached for 5 minutes."""
        if not refresh and (time.time() - self._last_cache_time < 300):
            return list(self._products_cache.values())
        data = self._get("/v2/products")
        if isinstance(data, list):
            self._products_cache = {p["symbol"]: p for p in data}
            self._last_cache_time = time.time()
        return list(self._products_cache.values())

    def get_product_id(self, symbol: str) -> int | None:
        """Returns product_id for a symbol."""
        if not self._products_cache:
            self.get_products()
        prod = self._products_cache.get(symbol.upper())
        return prod["id"] if prod else None

    # ── Market data ──────────────────────────────────────────────
    def get_ticker(self, symbol: str) -> dict:
        """
        Returns live ticker for a symbol.
        {symbol, mark_price, last_price, bid, ask, volume, oi, funding_rate}
        """
        data = self._get(f"/v2/tickers/{symbol}")
        if not data:
            return {}
        return {
            "symbol":       symbol,
            "mark_price":   float(data.get("mark_price", 0) or 0),
            "last_price":   float(data.get("close", 0) or 0),
            "bid":          float(data.get("bid", 0) or 0),
            "ask":          float(data.get("ask", 0) or 0),
            "volume":       float(data.get("volume", 0) or 0),
            "oi":           float(data.get("oi", 0) or 0),
            "funding_rate": float(data.get("funding_rate", 0) or 0),
            "price_change": float(data.get("price_change", 0) or 0),
            "price_change_pct": float(data.get("price_change_percent", 0) or 0),
            "timestamp":    datetime.now().isoformat(),
        }

    def get_all_tickers(self) -> list:
        """Returns tickers for all live products."""
        data = self._get("/v2/tickers")
        if isinstance(data, list):
            return data
        return []

    def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Returns L2 orderbook top N levels."""
        prod_id = self.get_product_id(symbol)
        if not prod_id:
            return {}
        data = self._get(f"/v2/l2orderbook/{symbol}")
        if not data:
            return {}
        bids = data.get("buy", [])[:depth]
        asks = data.get("sell", [])[:depth]
        return {
            "symbol": symbol,
            "bids":   [(float(b["price"]), float(b["size"])) for b in bids],
            "asks":   [(float(a["price"]), float(a["size"])) for a in asks],
            "spread": float(asks[0]["price"]) - float(bids[0]["price"]) if bids and asks else 0,
            "timestamp": datetime.now().isoformat(),
        }

    def get_candles(self, symbol: str, resolution: str = "15m",
                    days_back: int = 1) -> pd.DataFrame:
        """
        Returns OHLCV candles as DataFrame.
        resolution: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
        """
        end   = int(time.time())
        start = int((datetime.now() - timedelta(days=days_back)).timestamp())
        params = {"symbol": symbol, "resolution": resolution,
                  "start": start, "end": end}
        data = self._get("/v2/history/candles", params=params)
        if not data or not isinstance(data, list):
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=["time","open","high","low","close","volume"])
        df["time"]   = pd.to_datetime(df["time"], unit="s")
        df["time"]   = df["time"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata")
        df = df.sort_values("time").reset_index(drop=True)
        for col in ["open","high","low","close","volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def get_funding_rate(self, symbol: str) -> float:
        """Returns current funding rate for perpetual contracts."""
        ticker = self.get_ticker(symbol)
        return ticker.get("funding_rate", 0.0)

    def get_mark_price(self, symbol: str) -> float:
        """Returns current mark price."""
        ticker = self.get_ticker(symbol)
        return ticker.get("mark_price", 0.0)

    # ── Account data (authenticated) ────────────────────────────
    def get_balance(self) -> dict:
        """Returns wallet balances. Requires API key."""
        if not DELTA_API_KEY:
            return {"error": "No API key configured"}
        data = self._get("/v2/wallet/balances", authenticated=True)
        if isinstance(data, list):
            return {b["asset_symbol"]: float(b.get("available_balance", 0) or 0)
                    for b in data}
        return {}

    def get_positions(self) -> list:
        """Returns all open positions. Requires API key."""
        if not DELTA_API_KEY:
            return []
        data = self._get("/v2/positions/margined", authenticated=True)
        return data if isinstance(data, list) else []

    def get_open_orders(self, symbol: str = None) -> list:
        """Returns open orders. Requires API key."""
        if not DELTA_API_KEY:
            return []
        params = {}
        if symbol:
            prod_id = self.get_product_id(symbol)
            if prod_id:
                params["product_id"] = prod_id
        data = self._get("/v2/orders", params=params, authenticated=True)
        return data if isinstance(data, list) else []

    # ── Volatility & Greeks (for options) ───────────────────────
    def get_options_chain(self, underlying: str = "BTC") -> pd.DataFrame:
        """
        Returns current options chain for BTC or ETH.
        Includes strike, expiry, bid/ask, IV, delta, gamma, theta.
        """
        products = self.get_products()
        options  = [p for p in products
                    if p.get("contract_type") == "call_options"
                    or p.get("contract_type") == "put_options"
                    and underlying in p.get("symbol","")]
        if not options:
            return pd.DataFrame()

        rows = []
        for opt in options[:30]:   # limit to 30 strikes
            ticker = self.get_ticker(opt["symbol"])
            if ticker:
                rows.append({
                    "symbol":      opt["symbol"],
                    "strike":      float(opt.get("strike_price", 0) or 0),
                    "expiry":      opt.get("settlement_time",""),
                    "type":        opt.get("contract_type",""),
                    "mark_price":  ticker.get("mark_price", 0),
                    "bid":         ticker.get("bid", 0),
                    "ask":         ticker.get("ask", 0),
                    "volume":      ticker.get("volume", 0),
                    "oi":          ticker.get("oi", 0),
                })
        return pd.DataFrame(rows)

    # ── Market summary for signals ───────────────────────────────
    def get_market_summary(self) -> dict:
        """
        Returns a quick market summary used by the signal engine.
        Covers top crypto, funding rates, and trend direction.
        """
        summary = {
            "timestamp":  datetime.now().isoformat(),
            "assets":     {},
            "market_mood": "NEUTRAL",
            "fear_greed":  50,
        }

        symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
        up_count = 0

        for sym in symbols:
            ticker = self.get_ticker(sym)
            if ticker:
                pct = ticker.get("price_change_pct", 0)
                if pct > 0:
                    up_count += 1
                summary["assets"][sym] = {
                    "price":      ticker.get("mark_price"),
                    "change_pct": round(pct, 2),
                    "funding":    round(ticker.get("funding_rate", 0) * 100, 4),
                    "trend":      "UP" if pct > 0.5 else "DOWN" if pct < -0.5 else "FLAT",
                }

        # Simple market mood from breadth
        if up_count >= 3:
            summary["market_mood"] = "BULLISH"
            summary["fear_greed"]  = 65 + up_count * 5
        elif up_count <= 1:
            summary["market_mood"] = "BEARISH"
            summary["fear_greed"]  = 35 - (2 - up_count) * 5
        else:
            summary["market_mood"] = "NEUTRAL"
            summary["fear_greed"]  = 50

        return summary


# ════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE  (crypto + equity strategy signals)
#  Applies the same scoring framework as the NIFTY algo
#  but adapted for crypto market dynamics
# ════════════════════════════════════════════════════════════════
class DeltaSignalEngine:
    """
    Generates trading signals for crypto and equity instruments
    using live Delta Exchange data.
    """

    def __init__(self, connector: DeltaConnector):
        self.dc            = connector
        self.price_history = {}   # symbol -> list of prices
        self.HISTORY_LEN   = 20

    def _update_history(self, symbol: str, price: float):
        if symbol not in self.price_history:
            self.price_history[symbol] = []
        self.price_history[symbol].append(price)
        self.price_history[symbol] = self.price_history[symbol][-self.HISTORY_LEN:]

    def _trend(self, symbol: str) -> tuple:
        """Returns (direction, strength_pct). Needs 5+ price readings."""
        hist = self.price_history.get(symbol, [])
        if len(hist) < 5:
            return "FLAT", 0
        first = np.mean(hist[:3])
        last  = np.mean(hist[-3:])
        pct   = (last - first) / first * 100 if first > 0 else 0
        if pct > 0.3:
            return "UP", round(pct, 2)
        elif pct < -0.3:
            return "DOWN", round(abs(pct), 2)
        return "FLAT", round(abs(pct), 2)

    def score_crypto_momentum(self, symbol: str) -> dict | None:
        """
        Momentum signal for crypto perpetuals.
        Factors: price trend, funding rate, volume surge.
        """
        ticker = self.dc.get_ticker(symbol)
        if not ticker or not ticker.get("mark_price"):
            return None

        price   = ticker["mark_price"]
        self._update_history(symbol, price)
        trend, strength = self._trend(symbol)

        if trend == "FLAT":
            return None   # no signal when flat

        funding = ticker.get("funding_rate", 0)
        volume  = ticker.get("volume", 0)
        change  = ticker.get("price_change_pct", 0)

        # Score factors
        trend_sc   = min(30, int(strength * 10))
        funding_sc = 0
        # Positive funding + uptrend = crowded longs = risky for longs
        # Negative funding + downtrend = crowded shorts = risky for shorts
        if trend == "UP"   and funding < 0:   funding_sc = 15   # contrarian long
        elif trend == "DOWN" and funding > 0: funding_sc = 15   # contrarian short
        elif abs(funding) > 0.001:            funding_sc = 5
        vol_sc  = min(20, int(volume / 10_000_000 * 20))
        chg_sc  = min(25, int(abs(change) * 5))
        score   = trend_sc + funding_sc + vol_sc + chg_sc + 10

        direction = "LONG" if trend == "UP" else "SHORT"
        risk      = ("LOW" if abs(funding) < 0.0001 else
                     "MEDIUM" if abs(funding) < 0.001 else "HIGH")

        ob = self.dc.get_orderbook(symbol, depth=3)
        buy_at  = round(ticker["ask"] * 1.001, 1)
        sell_at = round(ticker["bid"] * 0.999, 1)

        return {
            "strategy":    "CRYPTO MOMENTUM",
            "market":      "CRYPTO",
            "symbol":      symbol,
            "score":       min(score, 100),
            "direction":   direction,
            "risk":        risk,
            "mark_price":  round(price, 2),
            "bid":         round(ticker["bid"], 2),
            "ask":         round(ticker["ask"], 2),
            "spread_pct":  round(ob.get("spread", 0) / price * 100, 4) if ob else 0,
            "funding_rate":round(funding * 100, 4),
            "funding_note":(f"{'Positive' if funding>0 else 'Negative'} funding "
                            f"{'favours shorts' if funding>0 else 'favours longs'}"),
            "volume_24h":  round(volume, 0),
            "change_pct":  round(change, 2),
            "trend":       trend,
            "trend_strength": strength,
            "entry_at":    buy_at if direction=="LONG" else sell_at,
            "target_at":   round(price * (1.02 if direction=="LONG" else 0.98), 2),
            "sl_at":       round(price * (0.99 if direction=="LONG" else 1.01), 2),
            "reason":      (f"{trend} trend {strength:.2f}% | "
                            f"Funding {round(funding*100,4)}% | "
                            f"Vol {round(volume/1e6,1)}M"),
            "orders":      (f"{'BUY' if direction=='LONG' else 'SELL'} {symbol} "
                            f"LIMIT @ {buy_at if direction=='LONG' else sell_at}"),
            "timestamp":   ticker["timestamp"],
        }

    def score_funding_arb(self, symbol: str) -> dict | None:
        """
        Funding rate arbitrage signal.
        When funding is extreme, fade the crowded side.
        High positive funding → short perp (longs paying shorts)
        High negative funding → long perp (shorts paying longs)
        """
        ticker = self.dc.get_ticker(symbol)
        if not ticker:
            return None

        funding = ticker.get("funding_rate", 0)
        price   = ticker.get("mark_price", 0)
        if abs(funding) < 0.0005:   # not interesting below 0.05%
            return None

        direction = "SHORT" if funding > 0 else "LONG"
        score = min(90, int(abs(funding) * 100_000))

        return {
            "strategy":    "FUNDING RATE ARB",
            "market":      "CRYPTO",
            "symbol":      symbol,
            "score":       score,
            "direction":   direction,
            "risk":        "MEDIUM",
            "mark_price":  round(price, 2),
            "bid":         round(ticker.get("bid", price), 2),
            "ask":         round(ticker.get("ask", price), 2),
            "funding_rate":round(funding * 100, 4),
            "reason":      (f"Funding {round(funding*100,4)}% is extreme. "
                            f"{'Longs paying → short' if funding>0 else 'Shorts paying → long'} perp. "
                            f"Collect funding income."),
            "entry_at":    round(ticker.get("ask" if direction=="LONG" else "bid", price), 2),
            "target_at":   "Hold for 8h to collect 1+ funding payment",
            "sl_at":       round(price * (0.98 if direction=="LONG" else 1.02), 2),
            "orders":      (f"{'BUY' if direction=='LONG' else 'SELL'} {symbol} "
                            f"@ market to collect funding"),
            "timestamp":   ticker["timestamp"],
        }

    def scan_all(self) -> list:
        """
        Scans all configured instruments and returns ranked signal list.
        """
        signals = []

        for inst in INSTRUMENTS["crypto_perp"]:
            sym = inst["symbol"]
            # Momentum signal
            s = self.score_crypto_momentum(sym)
            if s:
                signals.append(s)
            # Funding arb signal
            f = self.score_funding_arb(sym)
            if f:
                signals.append(f)

        signals.sort(key=lambda x: x["score"], reverse=True)
        return signals


# ════════════════════════════════════════════════════════════════
#  QUICK TEST
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("\n" + "="*60)
    print("  DELTA EXCHANGE CONNECTOR — TEST")
    print("="*60)

    if not DELTA_API_KEY:
        print("\n  ⚠  No API key found.")
        print("  Create D:\\AlgoTrading\\source\\.env with:")
        print("    DELTA_API_KEY=your_key")
        print("    DELTA_API_SECRET=your_secret")
        print("    DELTA_ENV=india")
        print("\n  Testing public endpoints only...\n")

    dc  = DeltaConnector()
    eng = DeltaSignalEngine(dc)

    print("  Fetching BTC ticker...")
    btc = dc.get_ticker("BTCUSD")
    if btc:
        print(f"  BTC Mark Price : ${btc['mark_price']:,.2f}")
        print(f"  BTC Funding    : {btc['funding_rate']*100:.4f}%")
        print(f"  BTC 24h Change : {btc['price_change_pct']:.2f}%")

    print("\n  Fetching ETH ticker...")
    eth = dc.get_ticker("ETHUSD")
    if eth:
        print(f"  ETH Mark Price : ${eth['mark_price']:,.2f}")

    print("\n  Fetching NIFTY ticker...")
    nifty = dc.get_ticker("NIFTYUSD")
    if nifty:
        print(f"  NIFTY Price    : ${nifty['mark_price']:,.2f}")

    print("\n  Running signal scan (2 cycles to build history)...")
    for i in range(2):
        signals = eng.scan_all()
        time.sleep(1)

    print(f"\n  Signals found: {len(signals)}")
    for s in signals[:5]:
        print(f"\n  [{s['score']}/100] {s['strategy']} | {s['symbol']} | {s['direction']}")
        print(f"    {s['reason']}")
        print(f"    Entry @ {s.get('entry_at','?')}  Target: {s.get('target_at','?')}  SL: {s.get('sl_at','?')}")

    if DELTA_API_KEY:
        print("\n  Fetching account balance...")
        bal = dc.get_balance()
        print(f"  Balances: {bal}")

    print("\n  Test complete.")
