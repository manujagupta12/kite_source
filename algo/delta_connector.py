"""
delta_connector.py
==================
Delta Exchange India REST + WebSocket client — GOLD only.

Setup (2 min):
  1. Login at https://india.delta.exchange
  2. Profile → API Keys → Create Key
     Permissions: Read + Trade
  3. Add to .env:
       DELTA_API_KEY=your_api_key
       DELTA_API_SECRET=your_api_secret

Gold instruments on Delta Exchange India:
  XAUUSDT   — Gold Perpetual (continuous, most liquid)
  XAUUSDT-C — Gold Call Options
  XAUUSDT-P — Gold Put Options
"""

import os
import time
import hmac
import hashlib
import json
import requests
import threading
from typing import Optional, Dict, List
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────
BASE_URL     = "https://api.india.delta.exchange"
WS_URL       = "wss://socket.india.delta.exchange"
GOLD_SYMBOL  = "XAUUSDT"        # Gold Perpetual
TIMEOUT      = 10               # seconds per request

# Only these product types are allowed — no crypto
_ALLOWED_UNDERLYING = {"XAU", "XAG", "XAUUSD", "GOLD"}

_session = requests.Session()
_session.headers.update({
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "AlgoTrade-Gold/1.0",
})


# ── Auth helpers ──────────────────────────────────────────────

def _get_creds() -> tuple:
    """Return (api_key, api_secret) from env or raise."""
    key    = os.environ.get("DELTA_API_KEY",    "").strip()
    secret = os.environ.get("DELTA_API_SECRET", "").strip()
    return key, secret


def _sign(method: str, path: str, payload: str = "") -> dict:
    """
    Generate Delta Exchange HMAC-SHA256 signature headers.
    Delta signature: HMAC-SHA256(api_secret, method + timestamp + path + body)
    """
    key, secret = _get_creds()
    if not key or not secret:
        return {}
    ts  = str(int(time.time()))
    msg = method.upper() + ts + path + payload
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "api-key":   key,
        "timestamp": ts,
        "signature": sig,
    }


def is_configured() -> bool:
    """Returns True if Delta API credentials are set."""
    k, s = _get_creds()
    return bool(k and s)


# ── REST helpers ──────────────────────────────────────────────

def _get(path: str, params: dict = None) -> dict:
    """Unauthenticated GET — for public market data."""
    try:
        r = _session.get(
            BASE_URL + path,
            params=params or {},
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result", r.json())
        print(f"[Delta] GET {path} → {r.status_code}: {r.text[:100]}")
        return {}
    except Exception as e:
        print(f"[Delta] GET error {path}: {e}")
        return {}


def _auth_get(path: str, params: dict = None) -> dict:
    """Authenticated GET."""
    qs = ""
    if params:
        qs = "?" + "&".join(f"{k}={v}" for k, v in params.items())
    headers = _sign("GET", path + qs)
    if not headers:
        return {}
    try:
        r = _session.get(
            BASE_URL + path,
            params=params or {},
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result", r.json())
        print(f"[Delta] AUTH GET {path} → {r.status_code}: {r.text[:150]}")
        return {}
    except Exception as e:
        print(f"[Delta] AUTH GET error {path}: {e}")
        return {}


def _auth_post(path: str, body: dict) -> dict:
    """Authenticated POST."""
    payload = json.dumps(body)
    headers = _sign("POST", path, payload)
    if not headers:
        return {}
    try:
        r = _session.post(
            BASE_URL + path,
            data=payload,
            headers=headers,
            timeout=TIMEOUT,
        )
        if r.status_code in (200, 201):
            return r.json().get("result", r.json())
        print(f"[Delta] POST {path} → {r.status_code}: {r.text[:200]}")
        return {}
    except Exception as e:
        print(f"[Delta] POST error {path}: {e}")
        return {}


# ── Public Market Data ────────────────────────────────────────

def get_gold_ticker() -> dict:
    """
    Fetch live Gold ticker from Delta Exchange.
    Returns: {symbol, mark_price, close, open, high, low, volume,
              change_24h, change_pct_24h, funding_rate, oi}
    """
    data = _get(f"/v2/tickers/{GOLD_SYMBOL}")
    if not data:
        return {}
    try:
        mark  = float(data.get("mark_price") or data.get("close") or 0)
        close = float(data.get("close") or mark)
        open_ = float(data.get("open") or close)
        high  = float(data.get("high") or close)
        low   = float(data.get("low")  or close)
        vol   = float(data.get("volume") or 0)
        ch    = round(close - open_, 2)
        chp   = round((ch / open_ * 100) if open_ else 0, 3)
        return {
            "symbol":       GOLD_SYMBOL,
            "mark_price":   round(mark, 2),
            "ltp":          round(close, 2),
            "open":         round(open_, 2),
            "high":         round(high, 2),
            "low":          round(low,  2),
            "volume":       round(vol,  2),
            "change":       ch,
            "change_pct":   chp,
            "funding_rate": float(data.get("funding_rate") or 0),
            "oi":           float(data.get("oi_value") or data.get("open_interest") or 0),
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "source":       "delta_exchange",
        }
    except Exception as e:
        print(f"[Delta] Ticker parse error: {e}")
        return {}


def get_ohlcv(symbol: str = GOLD_SYMBOL,
              resolution: str = "5m",
              limit: int = 100) -> list:
    """
    Fetch OHLCV candles for Gold.
    resolution: 1m, 5m, 15m, 30m, 1h, 4h, 1d
    Returns list of {time, open, high, low, close, volume} dicts.
    """
    # Delta Exchange India /v2/history/candles uses integer minutes for resolution
    # NOT string "5m" — sending a string causes HTTP 400
    res_mins_map = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "1d": 1440}
    res_mins = res_mins_map.get(resolution, 5)
    now   = int(time.time())
    start = now - (res_mins * 60 * limit)

    data = _get(f"/v2/history/candles", params={
        "symbol":     symbol,
        "resolution": res_mins,   # integer minutes: 1, 5, 15, 30, 60, 240, 1440
        "start":      start,
        "end":        now,
    })
    if not data:
        return []
    candles = data if isinstance(data, list) else data.get("candles", [])
    result = []
    for c in candles:
        try:
            result.append({
                "time":   int(c.get("time") or c.get("t") or 0),
                "open":   float(c.get("open")  or c.get("o") or 0),
                "high":   float(c.get("high")  or c.get("h") or 0),
                "low":    float(c.get("low")   or c.get("l") or 0),
                "close":  float(c.get("close") or c.get("c") or 0),
                "volume": float(c.get("volume") or c.get("v") or 0),
            })
        except Exception:
            continue
    return sorted(result, key=lambda x: x["time"])


def get_orderbook(symbol: str = GOLD_SYMBOL, depth: int = 5) -> dict:
    """
    Fetch order book for Gold.
    Returns {bids: [[price, qty], ...], asks: [[price, qty], ...], spread}
    """
    data = _get(f"/v2/l2orderbook/{symbol}")
    if not data:
        return {}
    try:
        bids = [[float(b["price"]), float(b["size"])]
                for b in (data.get("buy") or data.get("bids") or [])[:depth]]
        asks = [[float(a["price"]), float(a["size"])]
                for a in (data.get("sell") or data.get("asks") or [])[:depth]]
        spread = round(asks[0][0] - bids[0][0], 2) if bids and asks else 0
        return {"bids": bids, "asks": asks, "spread": spread,
                "best_bid": bids[0][0] if bids else 0,
                "best_ask": asks[0][0] if asks else 0}
    except Exception as e:
        print(f"[Delta] Orderbook parse error: {e}")
        return {}


# ── Authenticated Trading ─────────────────────────────────────

def get_account() -> dict:
    """Fetch account balance from Delta Exchange."""
    if not is_configured():
        return {}
    data = _auth_get("/v2/wallet/balances")
    if not data:
        return {}
    balances = data if isinstance(data, list) else [data]
    usdt = next((b for b in balances if b.get("asset_symbol") == "USDT"), {})
    return {
        "balance_usdt":    float(usdt.get("balance", 0) or 0),
        "available_usdt":  float(usdt.get("available_balance", 0) or 0),
        "margin_used":     float(usdt.get("position_margin", 0) or 0),
    }


def get_positions() -> list:
    """Fetch open positions. Returns only Gold positions."""
    if not is_configured():
        return []
    data = _auth_get("/v2/positions/margined")
    positions = data if isinstance(data, list) else data.get("positions", [])
    return [p for p in positions
            if "XAU" in str(p.get("product_symbol", "")).upper()
            or "GOLD" in str(p.get("product_symbol", "")).upper()]


def place_order(side: str, size: float, order_type: str = "limit",
                price: float = None, reduce_only: bool = False) -> dict:
    """
    Place a Gold order on Delta Exchange.
    side: 'buy' | 'sell'
    size: quantity in contracts (1 contract = 1 unit Gold)
    order_type: 'limit' | 'market'
    price: required for limit orders
    """
    if not is_configured():
        return {"error": "Delta API credentials not configured"}

    body = {
        "product_symbol": GOLD_SYMBOL,
        "side":           side.lower(),
        "size":           int(size),
        "order_type":     order_type,
        "reduce_only":    reduce_only,
    }
    if order_type == "limit" and price:
        body["limit_price"] = str(round(price, 2))

    result = _auth_post("/v2/orders", body)
    if result:
        print(f"[Delta] Order placed: {side.upper()} {size} {GOLD_SYMBOL} @ "
              f"{'market' if order_type == 'market' else price}")
    return result


def cancel_order(order_id: str) -> dict:
    """Cancel an open order."""
    if not is_configured():
        return {}
    return _auth_post(f"/v2/orders/{order_id}/cancel", {})


def get_open_orders() -> list:
    """Fetch open orders for Gold."""
    if not is_configured():
        return []
    data = _auth_get("/v2/orders", params={"product_symbol": GOLD_SYMBOL, "state": "open"})
    return data if isinstance(data, list) else data.get("result", [])


if __name__ == "__main__":
    print("[Delta] Connection test\n")
    ticker = get_gold_ticker()
    if ticker:
        print(f"  GOLD:  ${ticker['ltp']:,.2f}  ({ticker['change_pct']:+.3f}%)")
        print(f"  High: ${ticker['high']:,.2f}  Low: ${ticker['low']:,.2f}")
        print(f"  OI:   {ticker['oi']:,.0f}  Funding: {ticker['funding_rate']:.4f}%")
    else:
        print("  Could not fetch ticker (check internet connection)")
    if is_configured():
        acc = get_account()
        print(f"\n  Account: ${acc.get('available_usdt', 0):,.2f} USDT available")
    else:
        print("\n  Not configured (add DELTA_API_KEY + DELTA_API_SECRET to .env)")
