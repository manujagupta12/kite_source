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


def start_dhan_ticker(instrument_tokens: list[int]) -> bool:
    """
    Start Dhan WebSocket ticker in a background daemon thread.

    Args:
        instrument_tokens: list of Dhan security_id integers

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
            # DhanContext is the correct object for DhanFeed v2
            dhan_context = DhanContext(client_id, access_token)

            # Subscription: (exchange_segment, security_id, feed_type)
            # NSE_FNO=2, Full=21 are class attrs on DhanFeed in dhanhq>=2.0
            NSE_FNO = getattr(marketfeed, 'NSE_FNO', None) or getattr(marketfeed.DhanFeed, 'NSE_FNO', 2)
            FULL    = getattr(marketfeed, 'Full',    None) or getattr(marketfeed.DhanFeed, 'Full',    21)
            subscriptions = [
                (NSE_FNO, str(token), FULL)
                for token in instrument_tokens
            ]

            def _on_ticks(data):
                try:
                    ticks = data if isinstance(data, list) else [data]
                    _tick_store.update(ticks)
                except Exception:
                    pass

            feed = marketfeed.DhanFeed(
                dhan_context,
                subscriptions,
                version="v2",
                on_ticks=_on_ticks,
            )
            _ticker_running = True
            print(f"[DhanTicker] Connected — subscribed to {len(subscriptions)} instruments")
            feed.run_forever()
        except Exception as e:
            print(f"[DhanTicker] Error: {e}")
            _ticker_running = False

    t = threading.Thread(target=_run, daemon=True, name="DhanTicker")
    t.start()
    return True


def is_running() -> bool:
    return _ticker_running


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("[TEST] DhanTicker (requires DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN)")
    store = get_tick_store()
    started = start_dhan_ticker([260105])  # BANKNIFTY spot
    if started:
        print("  Ticker started. Waiting 5s for ticks...")
        time.sleep(5)
        tick = store.get(260105)
        print(f"  BANKNIFTY tick: {tick}")
    else:
        print("  Skipped — set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to test")
