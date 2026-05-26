"""
NIFTY CALENDAR SPREAD ALGO — FINAL CORRECTED
=============================================
Column map confirmed from spread_debug.py output:

MAIN TABLE (rows 3+, header row 2):
  col[0]  = Near Strike       col[1]  = Far Strike
  col[3]  = Near BID          col[4]  = Near ASK
  col[5]  = Near LTP          col[7]  = Far Volume
  col[8]  = Near Volume       col[9]  = Near Delta
  col[11] = Far Delta         col[15] = Far Vega
  col[16] = Near Vega         col[19] = Far Theta
  col[20] = Near Theta        col[23] = Straddle Premium
  col[24] = Far CE Premium    <- key for CE spread calc

SECONDARY TABLE (cols 29-34, two blocks):
  col[29] = CE/PE             col[30] = Strike
  col[31] = Far BID           col[32] = Far ASK
  col[33] = Far LTP           col[34] = Volume

SPOT:  Row 0, col[7]
VIX:   Not in Excel — fetched live from NSE public API

CE SPREAD = col[24] (far premium) - col[4] (near ASK)
PE SPREAD = far_pe_bid (secondary) - near_pe_ask
            where near_pe_ask = straddle(col23) - near_ce_bid(col3)
"""

import time
import shutil
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
#  SETTINGS
# ============================================================
FILEPATH  = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH  = r"C:\AlgoTrading\data\_temp_read.xls"

TARGET    = 4
STOPLOSS  = 3
THRESHOLD = 3
REFRESH   = 3

VIX_PAUSE  = 22
VIX_REDUCE = 19

# ============================================================
#  EXACT COLUMN POSITIONS (verified by spread_debug.py)
# ============================================================
C_NEAR_STRIKE = 0
C_FAR_STRIKE  = 1
C_NEAR_BID    = 3
C_NEAR_ASK    = 4
C_NEAR_LTP    = 5
C_FAR_VOL     = 7
C_NEAR_VOL    = 8
C_NEAR_DELTA  = 9
C_FAR_DELTA   = 11
C_FAR_VEGA    = 15
C_NEAR_VEGA   = 16
C_FAR_THETA   = 19
C_NEAR_THETA  = 20
C_STRADDLE    = 23
C_FAR_PREM    = 24   # far CE premium per strike

C_SEC_TYPE    = 29
C_SEC_STRIKE  = 30
C_SEC_BID     = 31
C_SEC_ASK     = 32
C_SEC_LTP     = 33
C_SEC_VOL     = 34

C_SPOT        = 7    # Row 0 col 7

# ============================================================
#  POSITION STATE
# ============================================================
state = {
    "ce_pos": None,  "ce_entry": None,
    "pe_pos": None,  "pe_entry": None,
    "last_ce": None, "last_pe":  None,
    "spot": None,    "vix": None,
    "atm":  None,
    "near_exp": None, "far_exp": None,
    "fail": 0,       "vix_fail": 0,
}

# ============================================================
#  VIX — live from NSE public API
# ============================================================
_nse_session = requests.Session()
_nse_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com",
    "Accept-Language": "en-US,en;q=0.9",
})

def fetch_vix():
    try:
        _nse_session.get("https://www.nseindia.com", timeout=5)
        r = _nse_session.get(
            "https://www.nseindia.com/api/allIndices", timeout=5)
        for item in r.json().get("data", []):
            if "INDIA VIX" in str(item.get("index", "")).upper():
                return round(float(item["last"]), 2)
    except Exception:
        pass
    return None

# ============================================================
#  SAFE FILE READ
# ============================================================
def safe_read():
    try:
        shutil.copy2(FILEPATH, TEMPPATH)
        return pd.read_excel(TEMPPATH, header=None, engine="xlrd")
    except PermissionError:
        return None
    except Exception as e:
        print(f"  [{ts()}] Read error: {e}")
        return None

# ============================================================
#  METADATA
# ============================================================
def read_metadata(raw):
    meta = {"spot": None, "near_exp": None, "far_exp": None}

    # ── Spot price ───────────────────────────────────────────
    try:
        v = float(raw.iloc[0, C_SPOT])
        if 15000 < v < 35000:
            meta["spot"] = round(v, 2)
    except Exception:
        pass

    # ── Expiry labels ────────────────────────────────────────
    # Scan ALL cells in rows 0-1 for NIFTYDDMMMYYYY labels,
    # parse their dates, then assign near=soonest, far=later.
    # This avoids hardcoding column positions which can shift.
    from datetime import datetime as _dt
    import re

    expiry_dates = []   # list of (datetime, label_string)
    today = _dt.now().date()

    for r in range(min(2, len(raw))):
        for c in range(len(raw.columns)):
            cell = str(raw.iloc[r, c]).strip().upper()
            # Match patterns like NIFTY12MAY2026 or NIFTY24FEB2026
            m = re.search(r'NIFTY(\d{1,2})([A-Z]{3})(\d{4})', cell)
            if m:
                day, mon, yr = m.group(1), m.group(2), m.group(3)
                try:
                    exp_dt = _dt.strptime(
                        f"{day}{mon}{yr}", "%d%b%Y").date()
                    # Only keep future or very recent expiries
                    # (ignore stale dates > 60 days in the past)
                    if (today - exp_dt).days < 60:
                        expiry_dates.append((exp_dt, cell))
                except ValueError:
                    pass

    # Deduplicate and sort ascending (nearest first)
    seen = set()
    unique = []
    for dt, label in sorted(expiry_dates):
        if label not in seen:
            seen.add(label)
            unique.append((dt, label))

    if len(unique) >= 2:
        meta["near_exp"] = unique[0][1]   # soonest = near month
        meta["far_exp"]  = unique[1][1]   # later   = far month
    elif len(unique) == 1:
        meta["near_exp"] = unique[0][1]

    return meta

# ============================================================
#  MAIN TABLE
# ============================================================
def read_main_table(raw):
    rows = []
    for idx in range(3, len(raw)):
        row = raw.iloc[idx]
        try:
            ns = float(row.iloc[C_NEAR_STRIKE])
            if not (21000 < ns < 27000):
                continue
            rows.append({
                "near_strike": int(ns),
                "far_strike":  int(float(row.iloc[C_FAR_STRIKE])),
                "near_bid":    float(row.iloc[C_NEAR_BID]),
                "near_ask":    float(row.iloc[C_NEAR_ASK]),
                "near_ltp":    float(row.iloc[C_NEAR_LTP]),
                "near_vol":    _f(row.iloc[C_NEAR_VOL]),
                "far_vol":     _f(row.iloc[C_FAR_VOL]),
                "near_theta":  _f(row.iloc[C_NEAR_THETA]),
                "far_theta":   _f(row.iloc[C_FAR_THETA]),
                "near_vega":   _f(row.iloc[C_NEAR_VEGA]),
                "far_vega":    _f(row.iloc[C_FAR_VEGA]),
                "straddle":    _f(row.iloc[C_STRADDLE]),
                "far_prem":    _f(row.iloc[C_FAR_PREM]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return pd.DataFrame(rows) if rows else None

# ============================================================
#  SECONDARY TABLE (far CE/PE from both blocks)
# ============================================================
def read_secondary_table(raw):
    ce_rows, pe_rows = [], []
    for idx in range(len(raw)):
        row = raw.iloc[idx]
        try:
            t = str(row.iloc[C_SEC_TYPE]).strip().upper()
            if t not in ("CE", "PE"):
                continue
            s = float(row.iloc[C_SEC_STRIKE])
            if not (21000 < s < 27000):
                continue
            entry = {
                "strike": int(s),
                "bid":    float(row.iloc[C_SEC_BID]),
                "ask":    float(row.iloc[C_SEC_ASK]),
                "vol":    _f(row.iloc[C_SEC_VOL]),
            }
            (ce_rows if t == "CE" else pe_rows).append(entry)
        except (IndexError, ValueError, TypeError):
            continue
    return (pd.DataFrame(ce_rows) if ce_rows else None,
            pd.DataFrame(pe_rows) if pe_rows else None)

def _f(v):
    try:
        f = float(v)
        return np.nan if np.isnan(f) else f
    except Exception:
        return np.nan

# ============================================================
#  ATM DETECTION
# ============================================================
def detect_atm(spot, main_df):
    available = sorted(main_df["near_strike"].dropna().astype(int).unique())
    if not available:
        return None
    if spot and 21000 < spot < 27000:
        atm = int(round(spot / 50.0) * 50)
        return atm if atm in available else min(available, key=lambda s: abs(s - atm))
    valid = main_df.dropna(subset=["near_vol"])
    if not valid.empty:
        return int(valid.loc[valid["near_vol"].idxmax(), "near_strike"])
    return available[len(available) // 2]

# ============================================================
#  SPREAD CALCULATION
# ============================================================
def calc_ce_spread(main_df, strike):
    """
    CE Calendar Spread = Far Premium (col24) minus Near ASK (col4).
    Returns (spread, fair, price_detail_dict) or (None, None, None).
    price_detail carries every exact price needed for order execution.
    """
    row = main_df[main_df["near_strike"] == strike]
    if row.empty:
        return None, None, None
    try:
        far_prem  = float(row["far_prem"].iloc[0])
        near_ask  = float(row["near_ask"].iloc[0])
        near_bid  = float(row["near_bid"].iloc[0])
        near_ltp  = float(row["near_ltp"].iloc[0])
        far_strike = int(row["far_strike"].iloc[0])
        if np.isnan(far_prem) or np.isnan(near_ask):
            return None, None, None
        spread = round(far_prem - near_ask, 2)
        # Far mid-price (best executable price = slightly above far bid)
        far_mid   = round(far_prem + 0.05, 2)   # add 0.05 tick buffer to get filled
        near_mid  = round((near_bid + near_ask) / 2.0, 2)
        ft   = _f(row["far_theta"].iloc[0])
        nt   = _f(row["near_theta"].iloc[0])
        fair = round((ft - nt) * 0.5, 2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
        prices = {
            "near_strike": strike,
            "far_strike":  far_strike,
            "near_bid":    round(near_bid, 2),
            "near_ask":    round(near_ask, 2),
            "near_ltp":    round(near_ltp, 2),
            "far_bid":     round(far_prem, 2),      # col24 = far bid proxy
            "far_ask":     round(far_prem + 0.10, 2),
            "far_ltp":     round(far_prem, 2),
            # Recommended order prices (limit orders)
            "sell_near_at": round(near_bid - 0.05, 2),   # SELL near: just below bid
            "buy_near_at":  round(near_ask + 0.05, 2),   # BUY  near: just above ask
            "buy_far_at":   round(far_prem + 0.05, 2),   # BUY  far : just above bid
            "sell_far_at":  round(far_prem - 0.05, 2),   # SELL far : just below bid
        }
        return spread, fair, prices
    except Exception:
        return None, None, None


def calc_pe_spread(main_df, far_pe_df, strike):
    """
    PE Calendar Spread:
      Near PE ASK  = Straddle (col23) - Near CE BID (col3)
      Far  PE BID  = secondary table col31 for PE rows
    Returns (spread, price_detail_dict) or (None, None).
    """
    row = main_df[main_df["near_strike"] == strike]
    if row.empty:
        return None, None
    try:
        straddle  = float(row["straddle"].iloc[0])
        near_ce   = float(row["near_bid"].iloc[0])
        far_strike = int(row["far_strike"].iloc[0])
        if np.isnan(straddle) or np.isnan(near_ce):
            return None, None
        near_pe_ask = round(straddle - near_ce, 2)
        near_pe_bid = round(near_pe_ask - 0.10, 2)   # bid slightly below ask
    except Exception:
        return None, None

    if far_pe_df is None or far_pe_df.empty:
        return None, None

    pe_row = far_pe_df[far_pe_df["strike"] == strike]
    if pe_row.empty:
        pe_row = far_pe_df.iloc[[0]]
    try:
        far_pe_bid = float(pe_row["bid"].iloc[0])
        far_pe_ask = float(pe_row["ask"].iloc[0])
        if np.isnan(far_pe_bid):
            return None, None
        spread = round(far_pe_bid - near_pe_ask, 2)
        prices = {
            "near_strike":  strike,
            "far_strike":   far_strike,
            "near_pe_bid":  round(near_pe_bid, 2),
            "near_pe_ask":  round(near_pe_ask, 2),
            "far_pe_bid":   round(far_pe_bid, 2),
            "far_pe_ask":   round(far_pe_ask, 2),
            # Recommended limit order prices
            "sell_near_at": round(near_pe_bid - 0.05, 2),
            "buy_near_at":  round(near_pe_ask + 0.05, 2),
            "buy_far_at":   round(far_pe_bid  + 0.05, 2),
            "sell_far_at":  round(far_pe_bid  - 0.05, 2),
        }
        return spread, prices
    except Exception:
        return None, None

# ============================================================
#  SIGNAL ENGINE
# ============================================================
def get_signal(spread, fair, vix=None):
    if vix and vix >= VIX_PAUSE:
        return "BLOCKED"
    threshold = THRESHOLD if not (vix and vix >= VIX_REDUCE) else round(THRESHOLD * 1.5, 1)
    if spread < fair - threshold:
        return "LONG"
    if spread > fair + threshold:
        return "SHORT"
    return "WAIT"


def check_exit(position, entry, current):
    pnl = round((current - entry) if position == "LONG" else (entry - current), 2)
    if pnl >= TARGET:      return "TARGET HIT", pnl
    if pnl <= -STOPLOSS:   return "STOPLOSS HIT", pnl
    return None, pnl

# ============================================================
#  DISPLAY
# ============================================================
def ts():
    return datetime.now().strftime("%H:%M:%S")

def alert(tag, msg, action=""):
    icons = {"ENTRY": ">>>", "EXIT": "<<<", "UPDATE": "~~~",
             "WARN": "!!!", "PANIC": "###"}
    print(f"\n{icons.get(tag,'---')} [{ts()}] {msg}")
    if action:
        print(f"        ACTION ➜ {action}")

def live_line(label, spread, fair, pos=None, entry=None, vix=None):
    mtm = ""
    if pos and entry is not None:
        pnl = round((spread - entry) if pos == "LONG" else (entry - spread), 2)
        mtm = f"  MTM:{pnl:+.2f}pts[{pos}]"
    vt = f"  VIX={vix}" if vix else ""
    print(f"    [{ts()}] {label}  Spread:{spread:+.2f}  Fair:{fair:+.2f}{mtm}{vt}")

def print_banner(s):
    vix = s["vix"]
    if   vix and vix >= VIX_PAUSE:  mode = f"PANIC  — NO NEW ENTRIES  (VIX={vix})"
    elif vix and vix >= VIX_REDUCE: mode = f"CAUTION— REDUCE SIZE     (VIX={vix})"
    else:                           mode = f"NORMAL  (VIX={vix or 'fetching...'})"
    print("\n" + "=" * 66)
    print(f"  NIFTY CALENDAR SPREAD ALGO  |  {ts()}")
    print(f"  Spot        : {s['spot'] or 'reading...'}")
    print(f"  ATM Strike  : {s['atm']  or 'detecting...'}")
    print(f"  Near Expiry : {s['near_exp'] or 'reading...'}")
    print(f"  Far  Expiry : {s['far_exp']  or 'reading...'}")
    print(f"  Mode        : {mode}")
    print(f"  Target={TARGET}pts  SL={STOPLOSS}pts  Threshold={THRESHOLD}pts  Refresh={REFRESH}s")
    print("=" * 66 + "\n")

# ============================================================
#  MAIN LOOP
# ============================================================
print("\n" + "=" * 66)
print("  NIFTY CALENDAR SPREAD — SELF-CONFIGURING LIVE ALGO")
print("=" * 66)
print(f"  File    : {FILEPATH}")
print(f"  Refresh : {REFRESH}s\n")

print(f"  [{ts()}] Fetching India VIX from NSE...")
v = fetch_vix()
if v:
    state["vix"] = v
    print(f"  [{ts()}] India VIX = {v}")
else:
    print(f"  [{ts()}] VIX fetch failed — will retry every 60s. Proceeding.")

prev_banner_key = None
cycle           = 0
vix_countdown   = 0

while True:
    try:
        cycle       += 1
        vix_countdown += 1

        # ── REFRESH VIX every 20 cycles (~60s) ─────────────────
        if vix_countdown >= 20:
            v = fetch_vix()
            if v:
                if v != state["vix"]:
                    alert("UPDATE", f"VIX updated: {state['vix']} → {v}")
                    state["vix"] = v
                state["vix_fail"] = 0
            else:
                state["vix_fail"] += 1
                if state["vix_fail"] == 1:
                    print(f"  [{ts()}] VIX fetch failed "
                          f"(using last known: {state['vix']})")
            vix_countdown = 0

        # ── READ EXCEL ──────────────────────────────────────────
        raw = safe_read()
        if raw is None:
            state["fail"] += 1
            if state["fail"] % 5 == 1:
                print(f"  [{ts()}] Waiting for Excel... "
                      f"attempt {state['fail']}")
            time.sleep(REFRESH)
            continue
        state["fail"] = 0

        # ── METADATA ────────────────────────────────────────────
        meta = read_metadata(raw)
        if meta["spot"]:     state["spot"]     = meta["spot"]
        if meta["near_exp"]: state["near_exp"] = meta["near_exp"]
        if meta["far_exp"]:  state["far_exp"]  = meta["far_exp"]

        # ── PARSE TABLES ────────────────────────────────────────
        main_df            = read_main_table(raw)
        far_ce_df, far_pe_df = read_secondary_table(raw)

        if main_df is None or main_df.empty:
            print(f"  [{ts()}] No option data yet...")
            time.sleep(REFRESH)
            continue

        # ── ATM ─────────────────────────────────────────────────
        atm = detect_atm(state["spot"], main_df)
        if atm and atm != state["atm"]:
            state["atm"] = atm
            alert("UPDATE", f"ATM Strike set: {atm}  "
                            f"Spot={state['spot']}")

        bkey = (state["vix"], state["spot"], state["atm"], state["near_exp"])
        if bkey != prev_banner_key or cycle == 1:
            print_banner(state)
            prev_banner_key = bkey

        strike = state["atm"]
        if not strike:
            print(f"  [{ts()}] Waiting for ATM strike...")
            time.sleep(REFRESH)
            continue

        vix   = state["vix"]
        panic = vix and vix >= VIX_PAUSE
        if panic and cycle % 10 == 1:
            alert("PANIC", f"VIX={vix} — entries blocked.",
                  "Manage existing positions only.")

        # ── CE SPREAD ───────────────────────────────────────────
        ce_spread, ce_fair, ce_px = calc_ce_spread(main_df, strike)

        if ce_spread is not None:
            if ce_spread != state["last_ce"]:
                state["last_ce"] = ce_spread
                live_line(f"CE {strike}", ce_spread, ce_fair,
                          state["ce_pos"], state["ce_entry"], vix)

            if not panic:
                if state["ce_pos"] is None:
                    sig = get_signal(ce_spread, ce_fair, vix)
                    if sig == "LONG":
                        state["ce_pos"]      = "LONG"
                        state["ce_entry"]    = ce_spread
                        tp_price = round(ce_spread + TARGET, 2)
                        sl_price = round(ce_spread - STOPLOSS, 2)
                        print(f"""
┌─────────────────────────────────────────────────────────┐
│  >>> ENTRY SIGNAL — CE LONG CALENDAR  [{ts()}]
│  Strike    : {strike}  |  VIX: {vix}
│  ─────────────────────────────────────────────────────
│  LEG 1 — BUY  Far  CE {ce_px['far_strike']}
│    Far  Bid  : {ce_px['far_bid']}    Far  Ask : {ce_px['far_ask']}
│    ✅ PLACE BUY  LIMIT @ {ce_px['buy_far_at']}
│
│  LEG 2 — SELL Near CE {ce_px['near_strike']}
│    Near Bid  : {ce_px['near_bid']}   Near Ask : {ce_px['near_ask']}
│    ✅ PLACE SELL LIMIT @ {ce_px['sell_near_at']}
│
│  Net Spread : {ce_spread:+.2f} pts  |  Fair Value : {ce_fair:+.2f} pts
│  Target     : Exit when Spread ≥ {tp_price:.2f}  (+{TARGET} pts)
│  Stop Loss  : Exit when Spread ≤ {sl_price:.2f}  (-{STOPLOSS} pts)
└─────────────────────────────────────────────────────────┘""")
                    elif sig == "SHORT":
                        state["ce_pos"]      = "SHORT"
                        state["ce_entry"]    = ce_spread
                        tp_price = round(ce_spread - TARGET, 2)
                        sl_price = round(ce_spread + STOPLOSS, 2)
                        print(f"""
┌─────────────────────────────────────────────────────────┐
│  >>> ENTRY SIGNAL — CE SHORT CALENDAR  [{ts()}]
│  Strike    : {strike}  |  VIX: {vix}
│  ─────────────────────────────────────────────────────
│  LEG 1 — SELL Far  CE {ce_px['far_strike']}
│    Far  Bid  : {ce_px['far_bid']}    Far  Ask : {ce_px['far_ask']}
│    ✅ PLACE SELL LIMIT @ {ce_px['sell_far_at']}
│
│  LEG 2 — BUY  Near CE {ce_px['near_strike']}
│    Near Bid  : {ce_px['near_bid']}   Near Ask : {ce_px['near_ask']}
│    ✅ PLACE BUY  LIMIT @ {ce_px['buy_near_at']}
│
│  Net Spread : {ce_spread:+.2f} pts  |  Fair Value : {ce_fair:+.2f} pts
│  Target     : Exit when Spread ≤ {tp_price:.2f}  (+{TARGET} pts)
│  Stop Loss  : Exit when Spread ≥ {sl_price:.2f}  (-{STOPLOSS} pts)
└─────────────────────────────────────────────────────────┘""")
                else:
                    reason, pnl = check_exit(
                        state["ce_pos"], state["ce_entry"], ce_spread)
                    if reason:
                        action_leg1 = ("SELL Far  CE" if state["ce_pos"] == "LONG"
                                       else "BUY  Far  CE")
                        action_leg2 = ("BUY  Near CE" if state["ce_pos"] == "LONG"
                                       else "SELL Near CE")
                        exit_px1 = (ce_px["sell_far_at"] if state["ce_pos"] == "LONG"
                                    else ce_px["buy_far_at"])
                        exit_px2 = (ce_px["buy_near_at"] if state["ce_pos"] == "LONG"
                                    else ce_px["sell_near_at"])
                        print(f"""
┌─────────────────────────────────────────────────────────┐
│  <<< EXIT SIGNAL — CE {reason}  [{ts()}]
│  Strike    : {strike}  |  Position : {state['ce_pos']}
│  Entry     : {state['ce_entry']:+.2f}  →  Current : {ce_spread:+.2f}
│  P/L       : {pnl:+.2f} pts
│  ─────────────────────────────────────────────────────
│  LEG 1 — {action_leg1} {ce_px['far_strike']}
│    ✅ PLACE LIMIT @ {exit_px1}
│  LEG 2 — {action_leg2} {ce_px['near_strike']}
│    ✅ PLACE LIMIT @ {exit_px2}
└─────────────────────────────────────────────────────────┘""")
                        state["ce_pos"]   = None
                        state["ce_entry"] = None
        else:
            if cycle % 10 == 1:
                print(f"  [{ts()}] CE: waiting for far premium "
                      f"(col24) at strike {strike}...")

        # ── PE SPREAD ───────────────────────────────────────────
        pe_spread, pe_px = calc_pe_spread(main_df, far_pe_df, strike)

        if pe_spread is not None:
            if pe_spread != state["last_pe"]:
                state["last_pe"] = pe_spread
                live_line(f"PE {strike}", pe_spread, 0.0,
                          state["pe_pos"], state["pe_entry"], vix)

            if not panic:
                if state["pe_pos"] is None:
                    sig = get_signal(pe_spread, 0.0, vix)
                    if sig == "LONG":
                        state["pe_pos"]   = "LONG"
                        state["pe_entry"] = pe_spread
                        alert("ENTRY",
                              f"PE LONG CALENDAR  Strike={strike}  "
                              f"Spread={pe_spread:+.2f}  VIX={vix}",
                              "BUY Far PE + SELL Near PE in MultiTrade")
                    elif sig == "SHORT":
                        state["pe_pos"]   = "SHORT"
                        state["pe_entry"] = pe_spread
                        alert("ENTRY",
                              f"PE SHORT CALENDAR  Strike={strike}  "
                              f"Spread={pe_spread:+.2f}  VIX={vix}",
                              "SELL Far PE + BUY Near PE in MultiTrade")
                else:
                    reason, pnl = check_exit(
                        state["pe_pos"], state["pe_entry"], pe_spread)
                    if reason:
                        alert("EXIT",
                              f"PE {reason}  Strike={strike}  P/L={pnl:+.2f}pts",
                              "CLOSE BOTH PE LEGS in MultiTrade")
                        state["pe_pos"]   = None
                        state["pe_entry"] = None
        else:
            if cycle % 10 == 1:
                print(f"  [{ts()}] PE: waiting for far PE data "
                      f"at strike {strike}...")

        if vix and VIX_REDUCE <= vix < VIX_PAUSE and cycle % 20 == 0:
            alert("WARN",
                  f"VIX={vix} caution zone. "
                  f"Threshold widened to {round(THRESHOLD*1.5,1)}pts. "
                  f"Reduce lot size.")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n\n  [{ts()}] Algo stopped (Ctrl+C).")
        print("  Open positions at exit:")
        if state["ce_pos"]:
            print(f"    CE {state['atm']} | {state['ce_pos']} @ {state['ce_entry']}")
        if state["pe_pos"]:
            print(f"    PE {state['atm']} | {state['pe_pos']} @ {state['pe_entry']}")
        if not state["ce_pos"] and not state["pe_pos"]:
            print("    None — all flat.")
        break

    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        time.sleep(REFRESH)