"""
dhan_ticker.py
==============
Dhan WebSocket real-time tick store.
Free with a Dhan account (https://dhanhq.co).

Provides sub-second live quotes: LTP, bid, ask, OI, volume.
Runs in a background thread — non-blocking.
Falls back gracefully if Dhan credentials not set.

Setup:
    1. Open Dhan account (free)
    2. Generate API credentials at https://dhanhq.co/api/
    3. Set env vars: DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
    4. pip install dhanhq

Usage:
    from algo.dhan_ticker import get_tick_store, start_dhan_ticker
    tick_store = get_tick_store()
    start_dhan_ticker([1333, 260105])   # instrument tokens
    tick = tick_store.get(260105)       # BANKNIFTY spot token
"""

import os
import threading
import time
from typing import Optional

# ── Thread-safe in-memory tick store ──────────────────────────

class TickStore:
    """
    Shared in-memory store for live ticks.
    Written by WebSocket callback thread.
    Read by algo threads.
    """
    def __init__(self):
        self._data: dict = {}
        self._lock = threading.RLock()
        self._last_update: float = 0

    def update(self, ticks: list):
        with self._lock:
            for tick in ticks:
                token = tick.get("security_id") or tick.get("instrument_token")
                if token:
                    self._data[int(token)] = tick
            self._last_update = time.time()

    def get(self, token: int) -> dict:
        with self._lock:
            return dict(self._data.get(int(token), {}))

    def get_ltp(self, token: int) -> Optional[float]:
        tick = self.get(token)
        return tick.get("last_price") or tick.get("ltp")

    def get_bid_ask(self, token: int) -> tuple:
        """Returns (bid, ask) tuple. Falls back to (ltp, ltp) if depth unavailable."""
        tick = self.get(token)
        bid = tick.get("best_bid_price") or tick.get("last_price", 0)
        ask = tick.get("best_ask_price") or tick.get("last_price", 0)
        return float(bid), float(ask)

    def get_oi(self, token: int) -> int:
        return int(self.get(token).get("open_interest", 0))

    def staleness_seconds(self) -> float:
        """Seconds since last tick update. Useful for health checks."""
        if self._last_update == 0:
            return float("inf")
        return time.time() - self._last_update

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)


# ── Singleton tick store ───────────────────────────────────────
_tick_store = TickStore()
_ticker_running = False

def get_tick_store() -> TickStore:
    return _tick_store


# ── Dhan security IDs for NSE indices (exchange = IDX_I) ──────
# These give real-time index prices via WebSocket — no REST call needed
INDEX_TOKENS = {
    13:  "NIFTY",        # NIFTY 50
    25:  "BANKNIFTY",    # NIFTY BANK
    27:  "FINNIFTY",     # NIFTY FIN SERVICE
    12:  "VIX",          # INDIA VIX
}

def get_index_prices() -> dict:
    """
    Return live index prices from the Dhan tick store.
    Format: {name: {label, ltp, change_pct, change, _src}}
    Called by main.py _fetch_live_indices() as the primary source.
    """
    result = {}
    for token, name in INDEX_TOKENS.items():
        tick = _tick_store.get(token)
        ltp  = tick.get("last_price") or tick.get("ltp") or 0
        if ltp:
            prev  = tick.get("prev_close") or tick.get("close") or ltp
            ch    = round(float(ltp) - float(prev), 2)
            chp   = round((ch / float(prev) * 100) if prev else 0, 2)
            result[name] = {
                "label":      name,
                "ltp":        round(float(ltp), 2),
                "change_pct": chp,
                "change":     ch,
                "high":       float(tick.get("high", ltp) or ltp),
                "low":        float(tick.get("low",  ltp) or ltp),
                "_ts":        int(time.time()),
                "_src":       "dhan_ws",
            }
    return result


def start_dhan_ticker(instrument_tokens: list[int]) -> bool:
    """
    Start Dhan WebSocket ticker in a background daemon thread.
    Automatically includes NSE index tokens (NIFTY, BANKNIFTY, FINNIFTY, VIX).

    Args:
        instrument_tokens: additional Dhan security_id integers (F&O tokens)

    Returns:
        True if started, False if credentials missing or dhanhq not installed.
    """
    global _ticker_running

    client_id    = os.environ.get("DHAN_CLIENT_ID", "")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")

    if not client_id or not access_token:
        print("[DhanTicker] DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set — "
              "running without Dhan live ticks (NSE polling only)")
        return False

    try:
        from dhanhq import marketfeed
    except ImportError:
        print("[DhanTicker] dhanhq not installed — pip install dhanhq")
        return False

    def _run():
        global _ticker_running
        try:
            from dhanhq import DhanContext
            from dhanhq.marketfeed import MarketFeed

            dhan_context = DhanContext(client_id, access_token)

            # F&O tokens (BANKNIFTY futures/options)
            fno_subs = [
                (MarketFeed.NSE_FNO, str(token), MarketFeed.Full)
                for token in instrument_tokens
            ]

            # Index tokens for NIFTY/BANKNIFTY/FINNIFTY/VIX real-time prices
            idx_subs = []
            for t in INDEX_TOKENS.keys():
                try:
                    idx_subs.append((MarketFeed.IDX_I, str(t), MarketFeed.Full))
                except AttributeError:
                    pass  # IDX_I not in this version of dhanhq — indices via NSE fallback

            subscriptions = fno_subs + idx_subs

            def _on_ticks(data):
                try:
                    ticks = data if isinstance(data, list) else [data]
                    _tick_store.update(ticks)
                except Exception:
                    pass

            feed = MarketFeed(
                dhan_context,
                subscriptions,
                version="v2",
                on_ticks=_on_ticks,
            )
            _ticker_running = True
            print(f"[DhanTicker] Connected — {len(fno_subs)} F&O + {len(idx_subs)} index instruments")
            feed.run_forever()
        except Exception as e:
            print(f"[DhanTicker] Error: {e}")
            _ticker_running = False

    t = threading.Thread(target=_run, daemon=True, name="DhanTicker")
    t.start()
    return True


def is_running() -> bool:
    return _ticker_running


if __name__ == "__main__":
    print("[TEST] DhanTicker (requires DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN)")
    store = get_tick_store()
    started = start_dhan_ticker([260105])
    if started:
        import time as _time
        print("  Ticker started. Waiting 5s...")
        _time.sleep(5)
        prices = get_index_prices()
        print(f"  Index prices: {prices}")
    else:
        print("  Skipped — set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to test")
