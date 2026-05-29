"""
Multitrade_loader.py — DEPRECATED SHIM
=======================================
This file is kept for backward compatibility ONLY.
All logic has been migrated to data_provider.py.

This shim re-exports the full public API of the old loader
so that any script still importing from multitrade_loader
continues to work without changes.

XLS dependency removed. No more:
  - C:\\AlgoTrading\\data\\multitrade_feed.xls
  - _temp_read.xls permission errors
  - BOF record corruption errors
  - 3-5s refresh lag

Now powered by NSE Direct API (free) + Dhan WebSocket (optional).
Refresh rate: 2s (vs 3-5s XLS). Zero file I/O. Zero disk locks.
"""

import warnings
warnings.warn(
    "multitrade_loader is deprecated. Import from algo.data_provider instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from data_provider
from algo.data_provider import (
    get_instruments,
    get_ce,
    get_pe,
    get_atm_strike,
    get_spread,
    get_straddle_premium,
    get_pcr,
    summary,
    get_historical_options,
    get_historical_futures,
)

# Legacy constants (kept for any script that references them)
XLS_PATH  = "REMOVED — now using NSE Direct API"
TEMP_PATH = "REMOVED — now using NSE Direct API"

__all__ = [
    "get_instruments",
    "get_ce",
    "get_pe",
    "get_atm_strike",
    "get_spread",
    "get_straddle_premium",
    "get_pcr",
    "summary",
    "get_historical_options",
    "get_historical_futures",
    "XLS_PATH",
    "TEMP_PATH",
]
