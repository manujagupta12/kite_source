"""
Calendaralgofinal.py
====================
Live BANKNIFTY/NIFTY calendar spread signals.

Migrated: MultiTrade XLS removed. Now uses NSE Direct API via data_provider.
  - No more XLS file dependency
  - No more BOF/permission errors
  - Refresh: 2s (vs 3-5s XLS)
  - far_bid / far_ask always populated (KeyError fixed)

All existing features preserved:
  - Trade logging
  - Paper trading mode
  - Subscription model (strike/expiry gating)
  - VIX-based regime switching
  - Backend signal push to dashboard

Run:
    python Calendaralgofinal.py
    python Calendaralgofinal.py --paper       # paper trading mode
    python Calendaralgofinal.py --symbol NIFTY
"""

import sys
import os
import time
import argparse
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# ── Data provider (replaces multitrade_loader + XLS) ─────────
try:
    import algo.data_provider as loader
    print("  [OK] data_provider imported (NSE Direct API)")
except ImportError:
    try:
        import data_provider as loader
        print("  [OK] data_provider imported")
    except ImportError as e:
        print(f"  [ERROR] Cannot import data_provider: {e}")
        sys.exit(1)

# ── CLI args ──────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Calendar Spread Algo")
parser.add_argument("--paper",  action="store_true",  help="Paper trading mode")
parser.add_argument("--symbol", default="BANKNIFTY",  help="Index symbol")
parser.add_argument("--target", type=float, default=8,  help="Target pts")
parser.add_argument("--sl",     type=float, default=6,  help="Stop loss pts")
parser.add_argument("--thresh", type=float, default=5,  help="Entry threshold pts")
parser.add_argument("--refresh",type=float, default=2,  help="Refresh interval sec")
args = parser.parse_args()

# ── Settings ──────────────────────────────────────────────────
SYMBOL      = args.symbol
PAPER_MODE  = args.paper
TARGET      = args.target
STOPLOSS    = args.sl
THRESHOLD   = args.thresh
REFRESH     = args.refresh
VIX_PAUSE   = 22
VIX_CAUTION = 19

# ── Subscription model — gated symbols/strikes ────────────────
# Add symbols here to restrict which instruments the algo trades
SUBSCRIBED_SYMBOLS = {"BANKNIFTY", "NIFTY"}       # allowed symbols
MAX_LOTS_PER_TRADE = 3                             # subscription lot cap

def is_subscribed(symbol: str) -> bool:
    return symbol.upper() in SUBSCRIBED_SYMBOLS

# ── Trade logger (preserved from original) ───────────────────
LOG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "trade_logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, f"calendar_{datetime.now().strftime('%Y%m%d')}.csv")

def log_trade(event: str, opt_type: str, strike: int, spread: float,
              pnl: float = 0.0, pos: str = "", expiry: str = ""):
    """Append trade event to daily CSV log."""
    try:
        header = not os.path.exists(LOG_FILE)
        with open(LOG_FILE, "a") as f:
            if header:
                f.write("timestamp,symbol,event,type,strike,spread,pnl,position,expiry,mode\n")
            f.write(
                f"{datetime.now().isoformat()},{SYMBOL},{event},{opt_type},"
                f"{strike},{spread:.2f},{pnl:.2f},{pos},{expiry},"
                f"{'PAPER' if PAPER_MODE else 'LIVE'}\n"
            )
    except Exception as e:
        print(f"  [LOG] Write error: {e}")

# ── Backend push ──────────────────────────────────────────────
BACKEND_URL  = "http://localhost:8000"
FO_INGEST_EP = f"{BACKEND_URL}/signals/fo_ingest"
_api_sess    = requests.Session()
_api_sess.headers.update({"Content-Type": "application/json"})
_api_ok      = False

def _push(payload: dict, event_type: str = "signal") -> bool:
    global _api_ok
    try:
        data = {
            "strategy":       payload.get("strategy",    "S1 CALENDAR"),
            "instrument":     payload.get("instrument",  SYMBOL),
            "direction":      payload.get("direction",   "WAIT"),
            "near_strike":    payload.get("near_strike"),
            "far_strike":     payload.get("far_strike"),
            "spread":         payload.get("spread"),
            "fair_value":     payload.get("fair_value"),
            "deviation":      payload.get("deviation"),
            "score":          payload.get("score", 70),
            "vix":            payload.get("vix"),
            "regime":         payload.get("regime", "PAPER" if PAPER_MODE else "LIVE"),
            "risk":           payload.get("risk",   "MEDIUM"),
            "source":         payload.get("source", "calendar_algo_nse"),
            "action":         payload.get("action", ""),
            "reason":         payload.get("reason", ""),
            "orders":         payload.get("orders", ""),
            "target_pts":     payload.get("target_pts"),
            "sl_pts":         payload.get("sl_pts"),
            "lots_suggested": payload.get("lots_suggested", 1),
            "near_bid":       payload.get("near_bid"),
            "near_ask":       payload.get("near_ask"),
            "far_bid":        payload.get("far_bid"),
            "buy_far_at":     payload.get("buy_far_at"),
            "sell_near_at":   payload.get("sell_near_at"),
            "event_type":     event_type,
        }
        r = _api_sess.post(FO_INGEST_EP, json=data, timeout=2)
        if r.status_code == 200:
            if not _api_ok:
                print(f"  [{ts()}] [API] Connected — signals live on dashboard")
                _api_ok = True
            return True
        return False
    except requests.exceptions.ConnectionError:
        if _api_ok:
            print(f"  [{ts()}] [API] Backend disconnected")
            _api_ok = False
        return False
    except Exception:
        return False

# ── Position state ────────────────────────────────────────────
state = {
    "ce_pos":   None, "ce_entry": None, "ce_expiry": "",
    "pe_pos":   None, "pe_entry": None, "pe_expiry": "",
    "last_ce":  None, "last_pe":  None,
    "atm":      None, "vix":      None,
    "fail":     0,    "vix_fail": 0,
    "paper_pnl": 0.0,
}

# ── VIX fetcher ───────────────────────────────────────────────
_vix_sess = requests.Session()
_vix_sess.headers.update({
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":      "application/json",
    "Referer":     "https://www.nseindia.com",
})

def fetch_vix() -> float | None:
    """Fetch India VIX from NSE. Uses same session as data_provider if available."""
    try:
        return loader._get_fetcher().get_vix()
    except Exception:
        pass
    try:
        _vix_sess.get("https://www.nseindia.com", timeout=5)
        r = _vix_sess.get("https://www.nseindia.com/api/allIndices", timeout=5)
        for item in r.json().get("data", []):
            if "INDIA VIX" in str(item.get("index", "")).upper():
                return round(float(item["last"]), 2)
    except Exception:
        pass
    return None

# ── Signal logic ──────────────────────────────────────────────
def get_signal(spread: float, fair: float, vix: float | None) -> str:
    if vix and vix >= VIX_PAUSE:
        return "BLOCKED"
    thr = THRESHOLD if not (vix and vix >= VIX_CAUTION) else round(THRESHOLD * 1.5, 1)
    if spread < fair - thr: return "LONG"
    if spread > fair + thr: return "SHORT"
    return "WAIT"

def check_exit(pos: str, entry: float, current: float) -> tuple:
    pnl = round((current - entry) if pos == "LONG" else (entry - current), 2)
    if pnl >= TARGET:    return "TARGET HIT",  pnl
    if pnl <= -STOPLOSS: return "STOPLOSS HIT", pnl
    return None, pnl

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def print_banner(atm: int, vix: float | None):
    mode_label = "📄 PAPER" if PAPER_MODE else "🟢 LIVE"
    if   vix and vix >= VIX_PAUSE:   regime = f"🔴 PANIC    (VIX={vix})"
    elif vix and vix >= VIX_CAUTION: regime = f"🟡 CAUTION  (VIX={vix})"
    else:                             regime = f"🟢 NORMAL   (VIX={vix or 'fetching...'})"
    print("\n" + "=" * 62)
    print(f"  {SYMBOL} CALENDAR SPREAD  |  {ts()}  |  {mode_label}")
    print(f"  ATM Strike  : {atm}")
    print(f"  Mode        : {regime}")
    print(f"  Target={TARGET}pts  SL={STOPLOSS}pts  Threshold={THRESHOLD}pts  Refresh={REFRESH}s")
    print(f"  Data Source : NSE Direct API (free)")
    print("=" * 62 + "\n")

def live_line(label: str, spread: float, fair: float,
              pos=None, entry=None, vix=None):
    mtm = ""
    if pos and entry is not None:
        pnl = round((spread - entry) if pos == "LONG" else (entry - spread), 2)
        mtm = f"  MTM:{pnl:+.2f}pts [{pos}]"
    dev = round(spread - fair, 2)
    paper_tag = " [PAPER]" if PAPER_MODE else ""
    print(f"  [{ts()}] {label}  Spread:{spread:+.2f}  Fair:{fair:+.2f}  Dev:{dev:+.2f}{mtm}{paper_tag}")

# ── Startup ───────────────────────────────────────────────────
print("\n" + "=" * 62)
print("  BANKNIFTY CALENDAR SPREAD — LIVE ALGO")
print("  Data: NSE Direct API + Bhavcopy History")
print("=" * 62)
print(f"  Symbol   : {SYMBOL}")
print(f"  Mode     : {'PAPER TRADING' if PAPER_MODE else 'LIVE'}")
print(f"  Refresh  : {REFRESH}s")
print(f"  Log file : {LOG_FILE}")
print(f"  Dashboard: {BACKEND_URL}")
print()

if not is_subscribed(SYMBOL):
    print(f"  [SUBSCRIPTION] {SYMBOL} not in subscribed symbols: {SUBSCRIBED_SYMBOLS}")
    sys.exit(1)

_push({"strategy": "S1 CALENDAR", "instrument": SYMBOL,
       "direction": "WAIT", "action": "Calendar algo started (NSE Direct API)",
       "reason": "Connected"}, event_type="signal")

print(f"  [{ts()}] Fetching VIX...")
v = fetch_vix()
if v:
    state["vix"] = v
    print(f"  [{ts()}] India VIX = {v}")
else:
    print(f"  [{ts()}] VIX unavailable — will retry.")

prev_atm      = None
cycle         = 0
vix_countdown = 0

# ── Main loop ─────────────────────────────────────────────────
while True:
    try:
        cycle         += 1
        vix_countdown += 1

        # Refresh VIX every 20 cycles
        if vix_countdown >= 20:
            v = fetch_vix()
            if v:
                if v != state["vix"]:
                    print(f"\n  [{ts()}] VIX updated: {state['vix']} → {v}")
                state["vix"] = v
                state["vix_fail"] = 0
            else:
                state["vix_fail"] += 1
            vix_countdown = 0

        # ── Fetch live data (NSE API — no XLS) ───────────────
        df = loader.get_instruments(SYMBOL)
        if df is None or df.empty:
            state["fail"] += 1
            if state["fail"] % 5 == 1:
                print(f"  [{ts()}] Waiting for NSE data (attempt {state['fail']})...")
            time.sleep(REFRESH)
            continue
        state["fail"] = 0

        atm = loader.get_atm_strike(df)
        if not atm:
            time.sleep(REFRESH)
            continue

        if atm != prev_atm:
            state["atm"] = atm
            prev_atm = atm
            print_banner(atm, state["vix"])

        vix   = state["vix"]
        panic = bool(vix and vix >= VIX_PAUSE)

        # ── CE spread ─────────────────────────────────────────
        ce = loader.get_spread(df, atm, "CE", SYMBOL)
        if ce is not None:
            spread = ce["spread"]
            fair   = ce["fair"]
            dev    = ce["deviation"]

            if spread != state["last_ce"]:
                state["last_ce"] = spread
                live_line(f"CE {atm}", spread, fair, state["ce_pos"], state["ce_entry"], vix)
                _push({
                    "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                    "direction": state["ce_pos"] or "WAIT",
                    "near_strike": atm, "far_strike": ce.get("strike", atm),
                    "spread": spread, "fair_value": fair, "deviation": dev,
                    "score": min(95, max(40, 50 + int(abs(dev) * 8))),
                    "vix": vix, "source": "calendar_algo_nse",
                    "action": f"CE {atm} Spread:{spread:+.2f} Dev:{dev:+.2f}",
                    "reason": f"Live CE tick | Dev {dev:+.2f}pts",
                    "near_bid": ce.get("bid"), "near_ask": ce.get("ask"),
                    "far_bid":  ce.get("far_bid"),    # always populated now
                    "buy_far_at": ce.get("buy_far_at"),
                    "sell_near_at": ce.get("sell_near_at"),
                    "target_pts": TARGET, "sl_pts": STOPLOSS,
                }, event_type="tick")

            if not panic:
                if state["ce_pos"] is None:
                    sig = get_signal(spread, fair, vix)
                    if sig in ("LONG", "SHORT"):
                        state["ce_pos"]    = sig
                        state["ce_entry"]  = spread
                        state["ce_expiry"] = ce.get("near_expiry", "")
                        action_str = (
                            f"{sig} CE Calendar @ {atm} | "
                            f"BUY Far@{ce.get('buy_far_at')} "
                            f"SELL Near@{ce.get('sell_near_at')}"
                        )
                        print(f"\n  [{ts()}] >>> CE {sig} ENTRY @ {spread:+.2f} "
                              f"(Dev {dev:+.2f})"
                              f"{'  [PAPER]' if PAPER_MODE else ''}")
                        log_trade("ENTRY", "CE", atm, spread, expiry=state["ce_expiry"])
                        _push({
                            "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                            "direction": sig,
                            "near_strike": atm, "far_strike": ce.get("strike", atm),
                            "spread": spread, "fair_value": fair, "deviation": dev,
                            "score": min(95, max(65, 65 + int(abs(dev) * 8))),
                            "vix": vix, "source": "calendar_algo_nse",
                            "action": action_str,
                            "reason": f"CE Dev {dev:+.2f}pts > threshold | VIX {vix}",
                            "orders": (
                                f"BUY Far CE {ce.get('strike', atm)} @ {ce.get('buy_far_at')}\n"
                                f"SELL Near CE {atm} @ {ce.get('sell_near_at')}"
                            ),
                            "near_bid": ce.get("bid"), "near_ask": ce.get("ask"),
                            "far_bid":  ce.get("far_bid"),
                            "buy_far_at":   ce.get("buy_far_at"),
                            "sell_near_at": ce.get("sell_near_at"),
                            "target_pts": TARGET, "sl_pts": STOPLOSS,
                            "lots_suggested": min(1, MAX_LOTS_PER_TRADE),
                        }, event_type="entry")
                else:
                    reason, pnl = check_exit(state["ce_pos"], state["ce_entry"], spread)
                    if reason:
                        if PAPER_MODE:
                            state["paper_pnl"] += pnl
                            print(f"\n  [{ts()}] <<< CE EXIT {reason} | PnL {pnl:+.2f}pts"
                                  f"  Total Paper PnL: {state['paper_pnl']:+.2f}pts")
                        else:
                            print(f"\n  [{ts()}] <<< CE EXIT {reason} | PnL {pnl:+.2f}pts")
                        log_trade("EXIT", "CE", atm, spread, pnl=pnl,
                                  pos=state["ce_pos"], expiry=state["ce_expiry"])
                        _push({
                            "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                            "direction": f"EXIT {state['ce_pos']}",
                            "near_strike": atm, "spread": spread,
                            "fair_value": fair, "deviation": dev, "score": 80,
                            "vix": vix, "source": "calendar_algo_nse",
                            "action": f"CE EXIT ({reason}) PnL:{pnl:+.2f}pts",
                            "reason": f"{reason} | Entry:{state['ce_entry']:+.2f} Exit:{spread:+.2f} = {pnl:+.2f}pts",
                            "target_pts": pnl if pnl > 0 else None,
                            "sl_pts": abs(pnl) if pnl < 0 else None,
                        }, event_type="exit")
                        state["ce_pos"]    = None
                        state["ce_entry"]  = None
                        state["ce_expiry"] = ""

        # ── PE spread ─────────────────────────────────────────
        pe = loader.get_spread(df, atm, "PE", SYMBOL)
        if pe is not None:
            pe_spread = pe["spread"]
            pe_fair   = pe["fair"]
            pe_dev    = pe["deviation"]

            if pe_spread != state["last_pe"]:
                state["last_pe"] = pe_spread
                live_line(f"PE {atm}", pe_spread, pe_fair,
                          state["pe_pos"], state["pe_entry"], vix)
                _push({
                    "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                    "direction": state["pe_pos"] or "WAIT",
                    "near_strike": atm, "far_strike": pe.get("strike", atm),
                    "spread": pe_spread, "fair_value": pe_fair, "deviation": pe_dev,
                    "score": min(95, max(40, 50 + int(abs(pe_dev) * 8))),
                    "vix": vix, "source": "calendar_algo_nse",
                    "action": f"PE {atm} Spread:{pe_spread:+.2f} Dev:{pe_dev:+.2f}",
                    "reason": f"Live PE tick | Dev {pe_dev:+.2f}pts",
                    "near_bid": pe.get("bid"), "near_ask": pe.get("ask"),
                    "far_bid":  pe.get("far_bid"),
                    "buy_far_at":   pe.get("buy_far_at"),
                    "sell_near_at": pe.get("sell_near_at"),
                    "target_pts": TARGET, "sl_pts": STOPLOSS,
                }, event_type="tick")

            if not panic:
                if state["pe_pos"] is None:
                    sig = get_signal(pe_spread, pe_fair, vix)
                    if sig in ("LONG", "SHORT"):
                        state["pe_pos"]    = sig
                        state["pe_entry"]  = pe_spread
                        state["pe_expiry"] = pe.get("near_expiry", "")
                        action_str = (
                            f"{sig} PE Calendar @ {atm} | "
                            f"BUY Far@{pe.get('buy_far_at')} "
                            f"SELL Near@{pe.get('sell_near_at')}"
                        )
                        print(f"\n  [{ts()}] >>> PE {sig} ENTRY @ {pe_spread:+.2f} "
                              f"(Dev {pe_dev:+.2f})"
                              f"{'  [PAPER]' if PAPER_MODE else ''}")
                        log_trade("ENTRY", "PE", atm, pe_spread, expiry=state["pe_expiry"])
                        _push({
                            "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                            "direction": sig,
                            "near_strike": atm, "far_strike": pe.get("strike", atm),
                            "spread": pe_spread, "fair_value": pe_fair, "deviation": pe_dev,
                            "score": min(95, max(65, 65 + int(abs(pe_dev) * 8))),
                            "vix": vix, "source": "calendar_algo_nse",
                            "action": action_str,
                            "reason": f"PE Dev {pe_dev:+.2f}pts > threshold | VIX {vix}",
                            "orders": (
                                f"BUY Far PE {pe.get('strike', atm)} @ {pe.get('buy_far_at')}\n"
                                f"SELL Near PE {atm} @ {pe.get('sell_near_at')}"
                            ),
                            "near_bid": pe.get("bid"), "near_ask": pe.get("ask"),
                            "far_bid":  pe.get("far_bid"),
                            "buy_far_at":   pe.get("buy_far_at"),
                            "sell_near_at": pe.get("sell_near_at"),
                            "target_pts": TARGET, "sl_pts": STOPLOSS,
                            "lots_suggested": min(1, MAX_LOTS_PER_TRADE),
                        }, event_type="entry")
                else:
                    reason, pnl = check_exit(state["pe_pos"], state["pe_entry"], pe_spread)
                    if reason:
                        if PAPER_MODE:
                            state["paper_pnl"] += pnl
                            print(f"\n  [{ts()}] <<< PE EXIT {reason} | PnL {pnl:+.2f}pts"
                                  f"  Total Paper PnL: {state['paper_pnl']:+.2f}pts")
                        else:
                            print(f"\n  [{ts()}] <<< PE EXIT {reason} | PnL {pnl:+.2f}pts")
                        log_trade("EXIT", "PE", atm, pe_spread, pnl=pnl,
                                  pos=state["pe_pos"], expiry=state["pe_expiry"])
                        _push({
                            "strategy": "S1 CALENDAR", "instrument": SYMBOL,
                            "direction": f"EXIT {state['pe_pos']}",
                            "near_strike": atm, "spread": pe_spread,
                            "fair_value": pe_fair, "deviation": pe_dev, "score": 80,
                            "vix": vix, "source": "calendar_algo_nse",
                            "action": f"PE EXIT ({reason}) PnL:{pnl:+.2f}pts",
                            "reason": f"{reason} | Entry:{state['pe_entry']:+.2f} Exit:{pe_spread:+.2f} = {pnl:+.2f}pts",
                            "target_pts": pnl if pnl > 0 else None,
                            "sl_pts": abs(pnl) if pnl < 0 else None,
                        }, event_type="exit")
                        state["pe_pos"]    = None
                        state["pe_entry"]  = None
                        state["pe_expiry"] = ""

        if vix and VIX_CAUTION <= vix < VIX_PAUSE and cycle % 20 == 0:
            print(f"\n  [{ts()}] VIX={vix} CAUTION — threshold widened to {round(THRESHOLD*1.5,1)}pts")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n\n  [{ts()}] Algo stopped.")
        if PAPER_MODE:
            print(f"    Total Paper PnL: {state['paper_pnl']:+.2f}pts")
        if state["ce_pos"]: print(f"    CE {state['atm']} | {state['ce_pos']} @ {state['ce_entry']}")
        if state["pe_pos"]: print(f"    PE {state['atm']} | {state['pe_pos']} @ {state['pe_entry']}")
        if not state["ce_pos"] and not state["pe_pos"]: print("    No open positions.")
        break

    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        import traceback; traceback.print_exc()
        time.sleep(REFRESH)
