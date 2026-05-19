"""
CALENDAR SPREAD ALGO — Calendaralgofinal.py
============================================
Live BANKNIFTY/NIFTY calendar spread signals from MultiTrade XLS.

  python Calendaralgofinal.py

- Reads multitrade_feed.xls via multitrade_loader.py (header=9, all strikes)
- Fetches India VIX live from NSE public API
- CE + PE dual calendar spread alerts
- Entry / Exit / P&L with exact limit order prices
- VIX-based panic mode and caution mode
- Ctrl+C to stop cleanly
"""

import sys
import os
import time
import requests
import numpy as np
from datetime import datetime

# ── Import shared loader (same directory) ─────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import multitrade_loader as loader
    print("  [OK] multitrade_loader imported")
except ImportError as e:
    print(f"  [ERROR] multitrade_loader.py not found: {e}")
    print("  Make sure multitrade_loader.py is in the same folder as this script.")
    sys.exit(1)

# ════════════════════════════════════════════════════════════════
#  SETTINGS — adjust these to your trading style
# ════════════════════════════════════════════════════════════════
XLS_PATH   = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMP_PATH  = r"C:\AlgoTrading\data\_temp_read.xls"

TARGET     = 8      # profit target in spread points (BANKNIFTY = 8, NIFTY = 4)
STOPLOSS   = 6      # stop loss in spread points
THRESHOLD  = 5      # minimum deviation from fair value to trigger signal
REFRESH    = 3      # seconds between XLS reads

VIX_PAUSE  = 22     # above this VIX — block all new entries
VIX_CAUTION= 19     # above this VIX — widen threshold

# ════════════════════════════════════════════════════════════════
#  POSITION STATE
# ════════════════════════════════════════════════════════════════
state = {
    "ce_pos":    None,   # "LONG" / "SHORT" / None
    "ce_entry":  None,   # entry spread value
    "pe_pos":    None,
    "pe_entry":  None,
    "last_ce":   None,   # last printed CE spread (to avoid duplicate prints)
    "last_pe":   None,
    "atm":       None,   # current ATM strike
    "vix":       None,
    "fail":      0,
    "vix_fail":  0,
}

# ════════════════════════════════════════════════════════════════
#  VIX — live from NSE public API (no key needed)
# ════════════════════════════════════════════════════════════════
_sess = requests.Session()
_sess.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":          "application/json",
    "Referer":         "https://www.nseindia.com",
    "Accept-Language": "en-US,en;q=0.9",
})

def fetch_vix() -> float | None:
    try:
        _sess.get("https://www.nseindia.com", timeout=5)
        r = _sess.get("https://www.nseindia.com/api/allIndices", timeout=5)
        for item in r.json().get("data", []):
            if "INDIA VIX" in str(item.get("index", "")).upper():
                return round(float(item["last"]), 2)
    except Exception:
        pass
    return None

# ════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE
# ════════════════════════════════════════════════════════════════
def get_signal(spread: float, fair: float, vix: float | None) -> str:
    """Returns LONG / SHORT / WAIT / BLOCKED."""
    if vix and vix >= VIX_PAUSE:
        return "BLOCKED"
    threshold = THRESHOLD if not (vix and vix >= VIX_CAUTION) else round(THRESHOLD * 1.5, 1)
    if spread < fair - threshold:
        return "LONG"
    if spread > fair + threshold:
        return "SHORT"
    return "WAIT"


def check_exit(position: str, entry: float, current: float):
    """Returns (reason_str | None, pnl_pts)."""
    pnl = round((current - entry) if position == "LONG" else (entry - current), 2)
    if pnl >= TARGET:    return "TARGET HIT", pnl
    if pnl <= -STOPLOSS: return "STOPLOSS HIT", pnl
    return None, pnl

# ════════════════════════════════════════════════════════════════
#  DISPLAY HELPERS
# ════════════════════════════════════════════════════════════════
def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def print_banner(atm, vix):
    if   vix and vix >= VIX_PAUSE:   mode = f"🔴 PANIC    — NO NEW ENTRIES  (VIX={vix})"
    elif vix and vix >= VIX_CAUTION: mode = f"🟡 CAUTION  — REDUCE SIZE     (VIX={vix})"
    else:                             mode = f"🟢 NORMAL   (VIX={vix or 'fetching...'})"
    print("\n" + "═" * 66)
    print(f"  BANKNIFTY CALENDAR SPREAD ALGO  |  {ts()}")
    print(f"  ATM Strike  : {atm or 'detecting...'}")
    print(f"  Mode        : {mode}")
    print(f"  Target={TARGET}pts  SL={STOPLOSS}pts  Threshold={THRESHOLD}pts  Refresh={REFRESH}s")
    print("═" * 66 + "\n")


def live_line(label, spread, fair, pos=None, entry=None, vix=None):
    mtm = ""
    if pos and entry is not None:
        pnl = round((spread - entry) if pos == "LONG" else (entry - spread), 2)
        mtm = f"  MTM:{pnl:+.2f}pts [{pos}]"
    vt = f"  VIX={vix}" if vix else ""
    dev = round(spread - fair, 2) if fair else 0
    print(f"  [{ts()}] {label}  Spread:{spread:+.2f}  Fair:{fair:+.2f}  Dev:{dev:+.2f}{mtm}{vt}")


def print_entry_ce(sig, strike, far_strike, spread, fair, vix, px):
    tp = round(spread + TARGET, 2)  if sig == "LONG" else round(spread - TARGET, 2)
    sl = round(spread - STOPLOSS, 2) if sig == "LONG" else round(spread + STOPLOSS, 2)

    if sig == "LONG":
        leg1 = f"BUY  Far  CE {far_strike}"
        leg2 = f"SELL Near CE {strike}"
        px1_label = "BUY  LIMIT"
        px2_label = "SELL LIMIT"
        px1 = px["buy_far_at"]
        px2 = px["sell_near_at"]
        exit_cond = f"Exit when Spread ≥ {tp:.2f}  (Target +{TARGET}pts)"
        sl_cond   = f"Exit when Spread ≤ {sl:.2f}  (SL -{STOPLOSS}pts)"
    else:
        leg1 = f"SELL Far  CE {far_strike}"
        leg2 = f"BUY  Near CE {strike}"
        px1_label = "SELL LIMIT"
        px2_label = "BUY  LIMIT"
        px1 = px["sell_far_at"]
        px2 = px["buy_near_at"]
        exit_cond = f"Exit when Spread ≤ {tp:.2f}  (Target +{TARGET}pts)"
        sl_cond   = f"Exit when Spread ≥ {sl:.2f}  (SL -{STOPLOSS}pts)"

    print(f"""
┌─────────────────────────────────────────────────────────────┐
│  >>> CE {sig} CALENDAR ENTRY  [{ts()}]
│  Strike : {strike}  |  VIX : {vix}
│  ─────────────────────────────────────────────────────────
│  {leg1}
│    Bid : {px['far_bid']}   Ask : {px['far_ask']}
│    ✅ {px1_label} @ {px1}
│
│  {leg2}
│    Bid : {px['bid']}   Ask : {px['ask']}
│    ✅ {px2_label} @ {px2}
│
│  Net Spread : {spread:+.2f}pts  |  Fair : {fair:+.2f}pts  |  Dev : {round(spread-fair,2):+.2f}pts
│  {exit_cond}
│  {sl_cond}
└─────────────────────────────────────────────────────────────┘""")


def print_exit_ce(reason, pos, strike, far_strike, entry, current, pnl, px):
    if pos == "LONG":
        leg1 = f"SELL Far  CE {far_strike}  ✅ LIMIT @ {px['sell_far_at']}"
        leg2 = f"BUY  Near CE {strike}  ✅ LIMIT @ {px['buy_near_at']}"
    else:
        leg1 = f"BUY  Far  CE {far_strike}  ✅ LIMIT @ {px['buy_far_at']}"
        leg2 = f"SELL Near CE {strike}  ✅ LIMIT @ {px['sell_near_at']}"

    pnl_emoji = "✅" if pnl > 0 else "❌"
    print(f"""
┌─────────────────────────────────────────────────────────────┐
│  <<< CE EXIT — {reason}  [{ts()}]
│  Position : {pos}  |  Entry : {entry:+.2f}  →  Now : {current:+.2f}
│  P/L      : {pnl:+.2f} pts  {pnl_emoji}
│  ─────────────────────────────────────────────────────────
│  {leg1}
│  {leg2}
└─────────────────────────────────────────────────────────────┘""")


# ════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ════════════════════════════════════════════════════════════════
print("\n" + "═" * 66)
print("  BANKNIFTY CALENDAR SPREAD — LIVE ALGO")
print("═" * 66)
print(f"  XLS : {XLS_PATH}")
print(f"  Refresh : {REFRESH}s\n")

print(f"  [{ts()}] Fetching India VIX...")
v = fetch_vix()
if v:
    state["vix"] = v
    print(f"  [{ts()}] India VIX = {v}")
else:
    print(f"  [{ts()}] VIX unavailable — will retry. Continuing.")

prev_atm = None
cycle = 0
vix_countdown = 0

while True:
    try:
        cycle += 1
        vix_countdown += 1

        # ── Refresh VIX every 20 cycles (~60s) ──────────────────
        if vix_countdown >= 20:
            v = fetch_vix()
            if v:
                if v != state["vix"]:
                    print(f"\n  [{ts()}] VIX updated: {state['vix']} → {v}")
                state["vix"] = v
                state["vix_fail"] = 0
            else:
                state["vix_fail"] += 1
                if state["vix_fail"] == 1:
                    print(f"  [{ts()}] VIX fetch failed (last: {state['vix']})")
            vix_countdown = 0

        # ── Read XLS ─────────────────────────────────────────────
        df = loader.get_instruments(XLS_PATH, TEMP_PATH)

        if df is None or df.empty:
            state["fail"] += 1
            if state["fail"] % 5 == 1:
                print(f"  [{ts()}] Waiting for XLS data... "
                      f"(attempt {state['fail']}) — is MultiTrade open?")
            time.sleep(REFRESH)
            continue
        state["fail"] = 0

        # ── Detect ATM ───────────────────────────────────────────
        atm = loader.get_atm_strike(df)
        if not atm:
            print(f"  [{ts()}] Could not detect ATM strike")
            time.sleep(REFRESH)
            continue

        if atm != prev_atm:
            state["atm"] = atm
            prev_atm = atm
            vix = state["vix"]
            print_banner(atm, vix)
            info = loader.summary(df)
            print(f"  [{ts()}] XLS loaded: {info['total']} instruments "
                  f"({info['ce']} CE / {info['pe']} PE)  "
                  f"Strike range: {info['strike_range']}")

        vix   = state["vix"]
        panic = bool(vix and vix >= VIX_PAUSE)

        if panic and cycle % 10 == 1:
            print(f"\n  [{ts()}] 🔴 VIX={vix} — PANIC MODE. No new entries.")

        # ── CE CALENDAR SPREAD ───────────────────────────────────
        ce = loader.get_spread(df, atm, "CE")

        if ce is not None:
            spread = ce["spread"]
            fair   = ce["fair"]

            # Print live tick only when spread changes
            if spread != state["last_ce"]:
                state["last_ce"] = spread
                live_line(f"CE {atm}", spread, fair,
                          state["ce_pos"], state["ce_entry"], vix)

            if not panic:
                if state["ce_pos"] is None:
                    # Look for entry
                    sig = get_signal(spread, fair, vix)
                    if sig in ("LONG", "SHORT"):
                        state["ce_pos"]   = sig
                        state["ce_entry"] = spread
                        print_entry_ce(sig, atm, ce.get("strike", atm),
                                       spread, fair, vix, ce)
                else:
                    # Check exit
                    reason, pnl = check_exit(state["ce_pos"], state["ce_entry"], spread)
                    if reason:
                        print_exit_ce(reason, state["ce_pos"], atm,
                                      ce.get("strike", atm),
                                      state["ce_entry"], spread, pnl, ce)
                        state["ce_pos"]   = None
                        state["ce_entry"] = None
        else:
            if cycle % 10 == 1:
                print(f"  [{ts()}] CE spread not available at strike {atm}")

        # ── PE CALENDAR SPREAD ───────────────────────────────────
        pe = loader.get_spread(df, atm, "PE")

        if pe is not None:
            pe_spread = pe["spread"]
            pe_fair   = pe["fair"]

            if pe_spread != state["last_pe"]:
                state["last_pe"] = pe_spread
                live_line(f"PE {atm}", pe_spread, pe_fair,
                          state["pe_pos"], state["pe_entry"], vix)

            if not panic:
                if state["pe_pos"] is None:
                    sig = get_signal(pe_spread, pe_fair, vix)
                    if sig == "LONG":
                        state["pe_pos"]   = "LONG"
                        state["pe_entry"] = pe_spread
                        print(f"""
┌─────────────────────────────────────────────────────────────┐
│  >>> PE LONG CALENDAR ENTRY  [{ts()}]
│  Strike : {atm}  |  VIX : {vix}
│  Spread : {pe_spread:+.2f}pts  Fair : {pe_fair:+.2f}pts  Dev : {round(pe_spread-pe_fair,2):+.2f}pts
│  ✅ BUY Far PE @ {pe['buy_far_at']}
│  ✅ SELL Near PE @ {pe['sell_near_at']}
│  Target : {round(pe_spread+TARGET,2):.2f}  SL : {round(pe_spread-STOPLOSS,2):.2f}
└─────────────────────────────────────────────────────────────┘""")
                    elif sig == "SHORT":
                        state["pe_pos"]   = "SHORT"
                        state["pe_entry"] = pe_spread
                        print(f"""
┌─────────────────────────────────────────────────────────────┐
│  >>> PE SHORT CALENDAR ENTRY  [{ts()}]
│  Strike : {atm}  |  VIX : {vix}
│  Spread : {pe_spread:+.2f}pts  Fair : {pe_fair:+.2f}pts  Dev : {round(pe_spread-pe_fair,2):+.2f}pts
│  ✅ SELL Far PE @ {pe['sell_far_at']}
│  ✅ BUY Near PE @ {pe['buy_near_at']}
│  Target : {round(pe_spread-TARGET,2):.2f}  SL : {round(pe_spread+STOPLOSS,2):.2f}
└─────────────────────────────────────────────────────────────┘""")
                else:
                    reason, pnl = check_exit(state["pe_pos"], state["pe_entry"], pe_spread)
                    if reason:
                        emoji = "✅" if pnl > 0 else "❌"
                        print(f"""
┌─────────────────────────────────────────────────────────────┐
│  <<< PE EXIT — {reason}  [{ts()}]
│  Position : {state['pe_pos']}  Entry : {state['pe_entry']:+.2f} → Now : {pe_spread:+.2f}
│  P/L : {pnl:+.2f}pts  {emoji}
│  ✅ CLOSE BOTH PE LEGS in MultiTrade
└─────────────────────────────────────────────────────────────┘""")
                        state["pe_pos"]   = None
                        state["pe_entry"] = None
        else:
            if cycle % 10 == 1:
                print(f"  [{ts()}] PE spread not available at strike {atm}")

        # ── VIX caution reminder every 20 cycles ────────────────
        if vix and VIX_CAUTION <= vix < VIX_PAUSE and cycle % 20 == 0:
            print(f"\n  [{ts()}] 🟡 VIX={vix} — CAUTION. "
                  f"Threshold widened to {round(THRESHOLD*1.5,1)}pts. Reduce size.")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n\n  [{ts()}] Algo stopped.")
        print("  Open positions:")
        if state["ce_pos"]:
            print(f"    CE {state['atm']} | {state['ce_pos']} @ {state['ce_entry']}")
        if state["pe_pos"]:
            print(f"    PE {state['atm']} | {state['pe_pos']} @ {state['pe_entry']}")
        if not state["ce_pos"] and not state["pe_pos"]:
            print("    None — flat.")
        break

    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        import traceback
        traceback.print_exc()
        time.sleep(REFRESH)