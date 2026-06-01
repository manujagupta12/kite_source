"""
PCR (Put-Call Ratio) Strategy — algo/pcr_strategy.py
Based on OI (Open Interest) PCR data from NSE.

Strategy Logic (from PCR spec):
  PCR < 0.6   → Overbought / Greed  → Bearish reversal expected  → SHORT signal
  PCR > 1.3   → Oversold  / Fear    → Bullish reversal expected  → LONG signal
  0.85–1.15   → Neutral             → Trend continuation         → HOLD

Run standalone:  python pcr_strategy.py
Or import:       from pcr_strategy import PCRStrategy
"""

import time
import logging
import requests
from datetime import datetime
from typing import Optional, Dict, Any

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("PCR")

# ─────────────────────────────────────────────────────────────
# Constants / Thresholds
# ─────────────────────────────────────────────────────────────
PCR_OVERSOLD_THRESHOLD  = 1.30   # Fear zone  → expect bullish reversal
PCR_OVERBOUGHT_THRESHOLD = 0.60  # Greed zone → expect bearish reversal
PCR_NEUTRAL_LOW  = 0.85
PCR_NEUTRAL_HIGH = 1.15

INSTRUMENTS = ["NIFTY", "BANKNIFTY", "FINNIFTY"]

# ─────────────────────────────────────────────────────────────
# NSE OI Fetcher
# ─────────────────────────────────────────────────────────────
class NseOiFetcher:
    """Fetches OI PCR data from NSE options chain API."""

    BASE = "https://www.nseindia.com"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com",
    }

    def __init__(self):
        self._sess = requests.Session()
        self._sess.headers.update(self.HEADERS)
        self._last_refresh = 0.0
        self._fail_count = 0
        self._backoff_until = 0.0
        self._prime()

    def _prime(self):
        """Prime NSE session cookie."""
        try:
            self._sess.get(self.BASE, timeout=6)
            self._last_refresh = time.time()
        except Exception as e:
            log.warning(f"[NSE] Session prime failed: {e}")

    def _ensure_session(self):
        if time.time() - self._last_refresh > 90:
            self._prime()

    def fetch_oi_pcr(self, symbol: str = "NIFTY") -> Optional[Dict[str, Any]]:
        """Fetch OI-based PCR with circuit breaker to prevent log spam."""        # Circuit breaker
        if self._fail_count >= 3:
            if time.time() < self._backoff_until:
                return None
            self._fail_count = 0
        self._ensure_session()
        url = f"{self.BASE}/api/option-chain-indices?symbol={symbol}"
        try:
            r = self._sess.get(url, timeout=8)
            data = r.json()
            records = data.get("records", {})
            filtered = data.get("filtered", {})

            # Use filtered OI sums (ATM ± 10 strikes — more relevant)
            f_ce_oi  = float(filtered.get("CE", {}).get("totOI",  0) or 0)
            f_pe_oi  = float(filtered.get("PE", {}).get("totOI",  0) or 0)
            f_ce_vol = float(filtered.get("CE", {}).get("totVol", 0) or 0)
            f_pe_vol = float(filtered.get("PE", {}).get("totVol", 0) or 0)

            # Fall back to total if filtered is zero
            if f_ce_oi == 0:
                raw = records.get("data", [])
                for row in raw:
                    f_ce_oi  += float((row.get("CE") or {}).get("openInterest", 0) or 0)
                    f_pe_oi  += float((row.get("PE") or {}).get("openInterest", 0) or 0)
                    f_ce_vol += float((row.get("CE") or {}).get("totalTradedVolume", 0) or 0)
                    f_pe_vol += float((row.get("PE") or {}).get("totalTradedVolume", 0) or 0)

            pcr_oi  = round(f_pe_oi  / f_ce_oi,  4) if f_ce_oi  > 0 else None
            pcr_vol = round(f_pe_vol / f_ce_vol, 4) if f_ce_vol > 0 else None
            spot    = float(records.get("underlyingValue", 0) or 0)

            return {
                "symbol":        symbol,
                "spot":          round(spot, 2),
                "pcr_oi":        pcr_oi,
                "pcr_volume":    pcr_vol,
                "total_put_oi":  int(f_pe_oi),
                "total_call_oi": int(f_ce_oi),
                "total_put_vol": int(f_pe_vol),
                "total_call_vol":int(f_ce_vol),
                "timestamp":     datetime.now().isoformat(),
                "source":        "nse_live",
            }
        except Exception as e:
            self._fail_count += 1
            if self._fail_count == 1:
                log.warning(f"[PCR] NSE OI unavailable for {symbol} — will retry in 5 min")
            if self._fail_count >= 3:
                self._backoff_until = time.time() + 300
            return None


# ─────────────────────────────────────────────────────────────
# PCR Signal Engine
# ─────────────────────────────────────────────────────────────
class PCRStrategy:
    """
    Generates contrarian F&O signals based on OI PCR extremes.

    Rules:
      PCR_OI < 0.60  → SELL SHORT signal  (overbought / greed)        score ∝ distance from 0.6
      PCR_OI > 1.30  → BUY LONG  signal  (oversold  / fear)          score ∝ distance from 1.3
      0.85–1.15      → NEUTRAL — no signal generated
      Transition bands (0.6–0.85 and 1.15–1.30) → WATCH signals only

    Also checks MaxPain alignment when provided.
    Never use in isolation — always requires candlestick / VWAP confirmation.
    """

    def __init__(self, fetcher: NseOiFetcher = None):
        self._fetcher = fetcher or NseOiFetcher()
        self._history: list = []   # rolling last 20 readings per symbol

    def _interpret(self, pcr: float, symbol: str) -> Dict[str, Any]:
        """Convert raw PCR value to structured signal dict."""
        if pcr is None:
            return {"zone": "UNKNOWN", "direction": "WAIT", "score": 0, "tag": "NO DATA"}

        # Overbought — greed — expect BEARISH reversal
        if pcr < PCR_OVERBOUGHT_THRESHOLD:
            distance = PCR_OVERBOUGHT_THRESHOLD - pcr          # how deep into greed
            score    = min(92, 62 + int(distance * 100))
            return {
                "zone":      "OVERBOUGHT",
                "direction": "SHORT",
                "signal":    "BEARISH REVERSAL EXPECTED",
                "tag":       "GREED EXTREME",
                "score":     score,
                "reason":    (
                    f"PCR_OI {pcr:.3f} < {PCR_OVERBOUGHT_THRESHOLD} — "
                    f"heavy call buying detected. Contrarian SHORT setup. "
                    f"Confirm with bearish candle + VWAP rejection."
                ),
            }

        # Oversold — fear — expect BULLISH reversal
        if pcr > PCR_OVERSOLD_THRESHOLD:
            distance = pcr - PCR_OVERSOLD_THRESHOLD
            score    = min(92, 62 + int(distance * 60))
            return {
                "zone":      "OVERSOLD",
                "direction": "LONG",
                "signal":    "BULLISH REVERSAL EXPECTED",
                "tag":       "FEAR EXTREME",
                "score":     score,
                "reason":    (
                    f"PCR_OI {pcr:.3f} > {PCR_OVERSOLD_THRESHOLD} — "
                    f"heavy put buying detected. Contrarian LONG setup. "
                    f"Confirm with bullish candle reversal at support."
                ),
            }

        # Neutral range — trend continuation, no entry signal
        if PCR_NEUTRAL_LOW <= pcr <= PCR_NEUTRAL_HIGH:
            return {
                "zone":      "NEUTRAL",
                "direction": "WAIT",
                "signal":    "NO EXTREME — TREND CONTINUES",
                "tag":       "NEUTRAL",
                "score":     0,
                "reason":    (
                    f"PCR_OI {pcr:.3f} in neutral band ({PCR_NEUTRAL_LOW}–{PCR_NEUTRAL_HIGH}). "
                    f"No reversal signal. Prevailing trend likely continues."
                ),
            }

        # Transition watch zones (0.60–0.85 bearish watch, 1.15–1.30 bullish watch)
        if pcr < PCR_NEUTRAL_LOW:  # 0.60 < pcr < 0.85
            return {
                "zone":      "BEARISH_WATCH",
                "direction": "WATCH_SHORT",
                "signal":    "APPROACHING OVERBOUGHT",
                "tag":       "WATCH",
                "score":     45,
                "reason":    (
                    f"PCR_OI {pcr:.3f} in bearish watch zone. "
                    f"Approaching overbought — monitor for breakdown below {PCR_OVERBOUGHT_THRESHOLD}."
                ),
            }
        else:  # 1.15 < pcr < 1.30
            return {
                "zone":      "BULLISH_WATCH",
                "direction": "WATCH_LONG",
                "signal":    "APPROACHING OVERSOLD",
                "tag":       "WATCH",
                "score":     45,
                "reason":    (
                    f"PCR_OI {pcr:.3f} in bullish watch zone. "
                    f"Approaching oversold — monitor for breakout above {PCR_OVERSOLD_THRESHOLD}."
                ),
            }

    def generate_signal(self, symbol: str = "NIFTY",
                        max_pain: Optional[float] = None,
                        vix: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """
        Fetch live OI and generate a structured signal.
        Returns None if direction is WAIT (neutral band).
        """
        oi_data = self._fetcher.fetch_oi_pcr(symbol)
        if not oi_data:
            log.warning(f"[PCR] No OI data for {symbol}")
            return None

        pcr    = oi_data["pcr_oi"]
        interp = self._interpret(pcr, symbol)

        # Skip neutral / watch zones
        if interp["score"] < 50:
            log.info(f"[PCR] {symbol} PCR={pcr} → {interp['zone']} — no signal")
            return None

        # Max Pain alignment bonus
        max_pain_note = ""
        if max_pain and oi_data["spot"]:
            distance_pct = abs(oi_data["spot"] - max_pain) / max_pain * 100
            if distance_pct < 0.5:
                interp["score"] = min(95, interp["score"] + 8)
                max_pain_note = f" | Max Pain alignment @ {max_pain:.0f} (+8 score bonus)"

        risk = "LOW" if interp["score"] >= 80 else "MEDIUM"

        signal = {
            "timestamp":     datetime.now().isoformat(),
            "source":        "pcr_strategy",
            "market":        "FO",
            "strategy":      "S5 PCR CONTRARIAN",
            "score":         interp["score"],
            "direction":     interp["direction"],
            "instrument":    symbol,
            "symbol":        symbol,
            "zone":          interp["zone"],
            "tag":           interp["tag"],
            "signal":        interp["signal"],
            "pcr_oi":        pcr,
            "pcr_volume":    oi_data["pcr_volume"],
            "total_put_oi":  oi_data["total_put_oi"],
            "total_call_oi": oi_data["total_call_oi"],
            "spot":          oi_data["spot"],
            "max_pain":      max_pain,
            "vix":           vix,
            "risk":          risk,
            "reason":        interp["reason"] + max_pain_note,
            "action":        (
                f"{interp['direction']} {symbol} — "
                f"PCR={pcr:.3f} ({interp['tag']}) | "
                f"Put OI {oi_data['total_put_oi']:,} vs Call OI {oi_data['total_call_oi']:,}"
            ),
            "event_type":    "signal",
            "regime":        "FEAR" if pcr > 1 else "GREED",
            "target_pts":    None,
            "sl_pts":        None,
            "lots_suggested": 1,
            "near_strike":   None,
            "far_strike":    None,
        }

        # Keep rolling history
        self._history.append({"ts": datetime.now().isoformat(), "pcr": pcr, "symbol": symbol})
        self._history = self._history[-40:]

        log.info(
            f"[PCR] {symbol} PCR={pcr:.3f} zone={interp['zone']} "
            f"dir={interp['direction']} score={interp['score']}"
        )
        return signal

    def generate_all(self, vix: Optional[float] = None) -> list:
        """Generate PCR signals for all configured instruments."""
        signals = []
        for sym in INSTRUMENTS:
            try:
                sig = self.generate_signal(sym, vix=vix)
                if sig:
                    signals.append(sig)
                time.sleep(0.8)   # gentle NSE rate-limit spacing
            except Exception as e:
                log.error(f"[PCR] {sym}: {e}")
        return signals


# ─────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    strategy = PCRStrategy()
    print("\n" + "═" * 62)
    print("  PCR CONTRARIAN STRATEGY — LIVE OI SCAN")
    print("═" * 62)
    signals = strategy.generate_all()
    if signals:
        for sig in signals:
            print(f"\n  {sig['symbol']:12} | PCR={sig['pcr_oi']:.3f} | Zone={sig['zone']}")
            print(f"  Direction : {sig['direction']}")
            print(f"  Score     : {sig['score']}")
            print(f"  Reason    : {sig['reason'][:100]}")
            print(f"  Action    : {sig['action'][:100]}")
    else:
        print("  No extreme PCR signals at this time — market in neutral zone.")
    print("\n" + "═" * 62)
                                                                                                                                                                                                                                                                                                                                                            