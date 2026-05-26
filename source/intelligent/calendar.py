"""
CALENDAR SPREAD ALGO  —  calendar.py
=====================================
Run this file for the CALENDAR SPREAD strategy only.

  python calendar.py           — normal run
  python calendar.py --input   — open trade input mode first

Trade logging:
  Press Ctrl+C at any time → trade input menu appears
  OR run:  python calendar.py --input

Logs saved to: C:\AlgoTrading\logs\trades_YYYYMMDD.csv
"""

import sys, time, shutil, requests, re, threading
import pandas as pd
import numpy as np
from datetime import datetime, date

# ── Import shared logger ─────────────────────────────────────────
sys.path.insert(0, r"C:\AlgoTrading\scripts")
try:
    import trade_logger as logger
except ImportError:
    print("  ERROR: trade_logger.py not found in C:\\AlgoTrading\\scripts\\")
    print("  Copy trade_logger.py there first.")
    sys.exit(1)

# ── Import regime engine ─────────────────────────────────────────
try:
    from regime_engine import (RegimeEngine, print_regime_report,
                                regime_summary_line, filter_by_regime)
    _regime_engine    = RegimeEngine()
    _regime_available = True
except ImportError:
    _regime_available = False
    _regime_engine    = None
    print("  WARNING: regime_engine.py not found — basic mode active.")

# ════════════════════════════════════════════════════════════════
#  SETTINGS  — only file paths and algo risk params here.
#  Margin and lot sizing are dynamic (user-input at startup).
# ════════════════════════════════════════════════════════════════
FILEPATH        = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH        = r"C:\AlgoTrading\data\_temp_read.xls"

TARGET_PTS      = 4      # profit target per spread (points)
STOPLOSS_PTS    = 3      # stop loss per spread (points)
REFRESH         = 3      # seconds between reads
ANALYSIS_EVERY  = 60     # seconds between full ranked analysis
IMMEDIATE_SCORE = 70     # score threshold for immediate alert

VIX_LOW         = 13
VIX_MEDIUM      = 16
VIX_HIGH        = 19
VIX_EXTREME     = 22

# Carried overnight positions
CARRIED = {
    "ce_pos":    None,
    "ce_entry":  None,
    "ce_strike": None,
    "pe_pos":    None,
    "pe_entry":  None,
    "pe_strike": None,
}

# ── Import dynamic position sizer ────────────────────────────────
try:
    from position_sizer import PositionSizer
    _sizer_available = True
except ImportError:
    _sizer_available = False
    print("  WARNING: position_sizer.py not found — using fixed 1 lot.")

# ════════════════════════════════════════════════════════════════
#  COLUMN MAP  (verified from MultiTrade Excel)
# ════════════════════════════════════════════════════════════════
C_NEAR_STRIKE = 0;  C_FAR_STRIKE  = 1
C_NEAR_BID    = 3;  C_NEAR_ASK    = 4;  C_NEAR_LTP    = 5
C_FAR_VOL     = 7;  C_NEAR_VOL    = 8
C_NEAR_DELTA  = 9;  C_FAR_DELTA   = 11
C_FAR_VEGA    = 15; C_NEAR_VEGA   = 16
C_FAR_THETA   = 19; C_NEAR_THETA  = 20
C_STRADDLE    = 23; C_FAR_PREM    = 24
C_SEC_TYPE    = 29; C_SEC_STRIKE  = 30
C_SEC_BID     = 31; C_SEC_ASK     = 32
C_SEC_LTP     = 33; C_SEC_VOL     = 34
C_SPOT        = 7

# ════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════
state = {
    "last_ce": None, "last_pe": None,
    "spot": None,    "vix": None,    "atm": None,
    "near_exp": None, "far_exp": None,
    "fail": 0,       "vix_countdown": 0,
    "last_analysis": 0,
    "alerted": set(),
    "input_mode": False,
}

# ════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════
def ts():
    return datetime.now().strftime("%H:%M:%S")

def _f(v):
    try:
        f = float(v)
        return np.nan if (np.isnan(f) or np.isinf(f)) else f
    except Exception:
        return np.nan

def risk_icon(vix):
    if   vix is None or vix < VIX_LOW:    return "🟢 LOW"
    elif vix < VIX_MEDIUM:                return "🟡 MEDIUM"
    elif vix < VIX_HIGH:                  return "🟠 HIGH"
    elif vix < VIX_EXTREME:               return "🔴 VERY HIGH"
    else:                                 return "💀 EXTREME"

def get_lots(strategy="S1 CALENDAR", score=70, vix=None, regime_key=None,
             instrument="NIFTY"):
    """Wrapper: uses dynamic sizer if available, otherwise 1 lot."""
    if _sizer_available and _sizer:
        return _sizer.get_lots(strategy, score, vix or 15, regime_key, instrument)
    return 1

# ════════════════════════════════════════════════════════════════
#  VIX
# ════════════════════════════════════════════════════════════════
_sess = requests.Session()
_sess.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                       "Referer": "https://www.nseindia.com"})

def fetch_vix():
    try:
        _sess.get("https://www.nseindia.com", timeout=5)
        r = _sess.get("https://www.nseindia.com/api/allIndices", timeout=5)
        for item in r.json().get("data", []):
            if "INDIA VIX" in str(item.get("index","")).upper():
                return round(float(item["last"]), 2)
    except Exception:
        pass
    return None

# ════════════════════════════════════════════════════════════════
#  FILE & DATA
# ════════════════════════════════════════════════════════════════
def safe_read():
    try:
        shutil.copy2(FILEPATH, TEMPPATH)
        return pd.read_excel(TEMPPATH, header=None, engine="xlrd")
    except PermissionError:
        return None
    except Exception as e:
        print(f"  [{ts()}] Read error: {e}")
        return None

def read_metadata(raw):
    meta = {"spot": None, "near_exp": None, "far_exp": None}
    try:
        v = float(raw.iloc[0, C_SPOT])
        if 15000 < v < 35000: meta["spot"] = round(v, 2)
    except Exception: pass
    today = date.today()
    expiry_dates = []
    for r in range(min(2, len(raw))):
        for c in range(len(raw.columns)):
            m = re.search(r'NIFTY(\d{1,2})([A-Z]{3})(\d{4})',
                          str(raw.iloc[r, c]).upper())
            if m:
                try:
                    exp_dt = datetime.strptime(
                        f"{m.group(1)}{m.group(2)}{m.group(3)}", "%d%b%Y").date()
                    if (today - exp_dt).days < 60:
                        expiry_dates.append((exp_dt, str(raw.iloc[r,c]).strip()))
                except ValueError: pass
    seen, unique = set(), []
    for dt, label in sorted(expiry_dates):
        if label not in seen:
            seen.add(label); unique.append((dt, label))
    if len(unique) >= 2:
        meta["near_exp"] = unique[0][1]; meta["far_exp"] = unique[1][1]
    elif len(unique) == 1:
        meta["near_exp"] = unique[0][1]
    return meta

def read_main_table(raw):
    rows = []
    for idx in range(3, len(raw)):
        row = raw.iloc[idx]
        try:
            ns = float(row.iloc[C_NEAR_STRIKE])
            if not (21000 < ns < 27000): continue
            rows.append({
                "near_strike": int(ns), "far_strike": int(float(row.iloc[C_FAR_STRIKE])),
                "near_bid": float(row.iloc[C_NEAR_BID]), "near_ask": float(row.iloc[C_NEAR_ASK]),
                "near_ltp": float(row.iloc[C_NEAR_LTP]),
                "near_vol": _f(row.iloc[C_NEAR_VOL]),  "far_vol":  _f(row.iloc[C_FAR_VOL]),
                "near_theta": _f(row.iloc[C_NEAR_THETA]), "far_theta": _f(row.iloc[C_FAR_THETA]),
                "near_vega":  _f(row.iloc[C_NEAR_VEGA]),  "far_vega":  _f(row.iloc[C_FAR_VEGA]),
                "near_delta": _f(row.iloc[C_NEAR_DELTA]),  "far_delta": _f(row.iloc[C_FAR_DELTA]),
                "straddle": _f(row.iloc[C_STRADDLE]),  "far_prem": _f(row.iloc[C_FAR_PREM]),
            })
        except (IndexError, ValueError, TypeError): continue
    return pd.DataFrame(rows) if rows else None

def read_secondary_table(raw):
    ce_rows, pe_rows = [], []
    for idx in range(len(raw)):
        row = raw.iloc[idx]
        try:
            t = str(row.iloc[C_SEC_TYPE]).strip().upper()
            if t not in ("CE","PE"): continue
            s = float(row.iloc[C_SEC_STRIKE])
            if not (21000 < s < 27000): continue
            entry = {"strike": int(s), "bid": float(row.iloc[C_SEC_BID]),
                     "ask": float(row.iloc[C_SEC_ASK]), "vol": _f(row.iloc[C_SEC_VOL])}
            (ce_rows if t=="CE" else pe_rows).append(entry)
        except (IndexError, ValueError, TypeError): continue
    return (pd.DataFrame(ce_rows) if ce_rows else None,
            pd.DataFrame(pe_rows) if pe_rows else None)

def detect_atm(spot, main_df):
    available = sorted(main_df["near_strike"].dropna().astype(int).unique())
    if not available: return None
    if spot and 21000 < spot < 27000:
        atm = int(round(spot / 50.0) * 50)
        return atm if atm in available else min(available, key=lambda s: abs(s-atm))
    valid = main_df.dropna(subset=["near_vol"])
    if not valid.empty: return int(valid.loc[valid["near_vol"].idxmax(), "near_strike"])
    return available[len(available)//2]

# ════════════════════════════════════════════════════════════════
#  CALENDAR SPREAD ENGINE
# ════════════════════════════════════════════════════════════════
def get_ce_data(main_df, strike):
    row = main_df[main_df["near_strike"] == strike]
    if row.empty: return None
    try:
        far_prem = float(row["far_prem"].iloc[0]); near_ask = float(row["near_ask"].iloc[0])
        near_bid = float(row["near_bid"].iloc[0]); far_strike = int(row["far_strike"].iloc[0])
        if np.isnan(far_prem) or np.isnan(near_ask): return None
        spread = round(far_prem - near_ask, 2)
        ft = _f(row["far_theta"].iloc[0]); nt = _f(row["near_theta"].iloc[0])
        fv = _f(row["far_vega"].iloc[0]);  nv = _f(row["near_vega"].iloc[0])
        fd = _f(row["far_delta"].iloc[0]); nd = _f(row["near_delta"].iloc[0])
        nvol = _f(row["near_vol"].iloc[0]); fvol = _f(row["far_vol"].iloc[0])
        fair = round((ft-nt)*0.5, 2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
        te   = round(abs(nt)-abs(ft), 4) if not (np.isnan(nt) or np.isnan(ft)) else 0
        vd   = round(fv-nv, 4) if not (np.isnan(fv) or np.isnan(nv)) else 0
        dev  = round(spread - fair, 2)

        sc  = min(25, int((abs(dev)/5.0)*25))
        sc += min(20, int((abs(te)/0.02)*20)) if te > 0 else 5
        sc += min(15, int((abs(vd)/20.0)*15)) if vd > 0 else 3
        sc += min(15, int(((nvol or 0)+(fvol or 0))/500000*15))
        dn   = abs(nd-fd) if not (np.isnan(nd) or np.isnan(fd)) else 1
        sc += max(0, 15-int(dn*30))
        vix  = state["vix"]
        sc += (10 if (vix or 99) < VIX_LOW else 8 if (vix or 99) < VIX_MEDIUM
               else 5 if (vix or 99) < VIX_HIGH else 2)

        direction = "LONG" if dev < -1.0 else "SHORT" if dev > 1.0 else "LONG"
        return {
            "near_strike": strike, "far_strike": far_strike,
            "spread": spread, "fair": fair, "deviation": dev,
            "score": sc, "direction": direction,
            "near_bid": round(near_bid,2), "near_ask": round(near_ask,2),
            "far_bid": round(far_prem,2),  "far_ask": round(far_prem+0.10,2),
            "near_ltp": float(row["near_ltp"].iloc[0]),
            "sell_near_at": round(near_bid-0.05,2), "buy_near_at": round(near_ask+0.05,2),
            "buy_far_at":   round(far_prem+0.05,2), "sell_far_at": round(far_prem-0.05,2),
            "theta_edge": te, "vega_diff": vd,
            "near_theta": round(nt,4) if not np.isnan(nt) else None,
            "far_theta":  round(ft,4) if not np.isnan(ft) else None,
            "near_vega":  round(nv,4) if not np.isnan(nv) else None,
            "far_vega":   round(fv,4) if not np.isnan(fv) else None,
        }
    except Exception: return None

def scan_all_strikes(main_df):
    results = []
    atm = state["atm"]
    if not atm: return results
    strikes = sorted(main_df["near_strike"].dropna().astype(int).unique())
    strikes = [s for s in strikes if abs(s - atm) <= 300]
    for strike in strikes:
        d = get_ce_data(main_df, strike)
        if d: results.append(d)
    results.sort(key=lambda x: x["score"], reverse=True)
    return results

# ════════════════════════════════════════════════════════════════
#  DISPLAY
# ════════════════════════════════════════════════════════════════
def grade(score):
    if score >= 85: return "⭐⭐⭐ STRONG"
    elif score >= 70: return "⭐⭐   GOOD"
    elif score >= 50: return "⭐    MODERATE"
    else: return "     WEAK"

def print_analysis(results, vix, regime_key=None, top_n=5):
    realised, open_c, _ = logger.get_daily_pnl()
    deployed = _sizer.deployed_margin if (_sizer_available and _sizer) else 0
    free     = (_sizer.available_margin - deployed) if (_sizer_available and _sizer) else 0
    avail    = _sizer.available_margin if (_sizer_available and _sizer) else 0

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  CALENDAR SPREAD ANALYSIS  |  {ts()}  |  VIX={vix} {risk_icon(vix)}
║  Scanned {len(results)} strikes  |  Top {min(top_n,len(results))} shown
║  Today's P&L: ₹{realised:+,.0f}  |  Open: {open_c}
║  Available: ₹{avail:,.0f}  Deployed: ₹{deployed:,.0f}  Free: ₹{free:,.0f}
║  Lot sizing is DYNAMIC — varies per signal quality, VIX, regime
╠══════════════════════════════════════════════════════════════════╣""")

    for rank, d in enumerate(results[:top_n], 1):
        sc   = d["score"]
        dirn = d["direction"]

        # Dynamic lots for this specific signal
        lots = get_lots("S1 CALENDAR", sc, vix, regime_key, "NIFTY")
        lots = max(lots, 1)

        tp     = round(d["spread"] + TARGET_PTS if dirn=="LONG" else d["spread"] - TARGET_PTS, 2)
        sl     = round(d["spread"] - STOPLOSS_PTS if dirn=="LONG" else d["spread"] + STOPLOSS_PTS, 2)
        tp_inr = round(TARGET_PTS * 25 * lots, 0)
        sl_inr = round(STOPLOSS_PTS * 25 * lots, 0)
        margin = lots * 80000

        if dirn == "LONG":
            l1 = f"BUY  Far  CE {d['far_strike']}  LIMIT @ {d['buy_far_at']}"
            l2 = f"SELL Near CE {d['near_strike']}  LIMIT @ {d['sell_near_at']}"
        else:
            l1 = f"SELL Far  CE {d['far_strike']}  LIMIT @ {d['sell_far_at']}"
            l2 = f"BUY  Near CE {d['near_strike']}  LIMIT @ {d['buy_near_at']}"

        # Sizing rationale
        if lots >= 10:   size_note = "Large — strong signal, favourable conditions"
        elif lots >= 5:  size_note = "Medium — good signal quality"
        elif lots >= 2:  size_note = "Small — moderate signal or elevated risk"
        else:            size_note = "Minimum — weak signal or high VIX/risk"

        print(f"""║
║  RANK #{rank}  {grade(sc)}  Score:{sc}/100
║  Direction:{dirn}  Strike:{d['near_strike']}/{d['far_strike']}
║  Spread:{d['spread']:+.2f}  Fair:{d['fair']:+.2f}  Dev:{d['deviation']:+.2f}pts
║  ThetaEdge:{d['theta_edge']:.4f}  VegaDiff:{d['vega_diff']:.4f}
║  Near: Bid={d['near_bid']}  Ask={d['near_ask']}
║  Far : Bid={d['far_bid']}   Ask={d['far_ask']}
║  ────────────────────────────────────────────────────────────
║  RECOMMENDED LOTS : {lots} lot{"s" if lots!=1 else ""}  (₹{margin:,.0f} margin)  [{size_note}]
║  LEG 1 ➜ {l1}
║  LEG 2 ➜ {l2}
║  TARGET → {tp:.2f}  (+₹{tp_inr:,.0f})   STOP → {sl:.2f}  (-₹{sl_inr:,.0f})
║  → Ctrl+C to log this trade with your actual filled lots""")

    print(f"""║
╚══════════════════════════════════════════════════════════════════╝""")


def print_immediate_alert(d, vix, regime_key=None):
    sc   = d["score"]
    lots = get_lots("S1 CALENDAR", sc, vix, regime_key, "NIFTY")
    lots = max(lots, 1)
    dirn = d["direction"]
    tp   = round(d["spread"] + TARGET_PTS if dirn=="LONG" else d["spread"] - TARGET_PTS, 2)
    sl   = round(d["spread"] - STOPLOSS_PTS if dirn=="LONG" else d["spread"] + STOPLOSS_PTS, 2)
    margin = lots * 80000

    if dirn == "LONG":
        l1 = f"BUY  Far  CE {d['far_strike']}  LIMIT @ {d['buy_far_at']}"
        l2 = f"SELL Near CE {d['near_strike']}  LIMIT @ {d['sell_near_at']}"
    else:
        l1 = f"SELL Far  CE {d['far_strike']}  LIMIT @ {d['sell_far_at']}"
        l2 = f"BUY  Near CE {d['near_strike']}  LIMIT @ {d['buy_near_at']}"

    # Show full sizing breakdown for immediate alerts
    if _sizer_available and _sizer:
        _sizer.explain_lots("S1 CALENDAR", sc, vix or 15, regime_key, "NIFTY", lots)

    print(f"""
🔔 HIGH SCORE SIGNAL  Score:{sc}/100  [{ts()}]  VIX={vix}  {risk_icon(vix)}
   CE {dirn} CALENDAR  Strike:{d['near_strike']}/{d['far_strike']}
   Spread:{d['spread']:+.2f}  Fair:{d['fair']:+.2f}  Dev:{d['deviation']:+.2f}
   LOTS: {lots}  (₹{margin:,.0f} margin)
   LEG 1 ➜ {l1}
   LEG 2 ➜ {l2}
   TARGET:{tp:.2f}  STOP:{sl:.2f}
   → Ctrl+C to log this trade
🔔""")



def recovery_advice(d, vix):
    cp = CARRIED
    if not cp["ce_pos"]: return
    strike  = cp["ce_strike"]
    entry   = cp["ce_entry"]
    current = d["spread"] if d else entry
    loss    = round((entry-current) if cp["ce_pos"]=="LONG" else (current-entry), 2)
    if loss <= 0: return
    loss_inr = round(loss * LOT_SIZE, 0)
    te = d.get("theta_edge",0) if d else 0

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  ⚠  RECOVERY  |  CE {cp['ce_pos']}  Strike:{strike}
║  Entry:{entry:+.2f}  Current:{current:+.2f}  Loss:{loss:.2f}pts ₹{abs(loss_inr):,.0f}
╠══════════════════════════════════════════════════════════════════╣
║  [1] 🟢 HOLD  — Theta edge {te:.4f}/day. Days to recover: {"~"+str(round(loss/abs(te),1)) if te else "unknown"}
║  [2] 🟡 ROLL  — Close near CE {strike}, sell near CE {strike+50}
║  [3] 🔴 CUT   — SELL Far CE {d['far_strike'] if d else ''} @ {d['sell_far_at'] if d else 'market'}
║                 BUY  Near CE {strike} @ {d['buy_near_at'] if d else 'market'}
║  → Press Ctrl+C → 'close' to log recovery exit
╚══════════════════════════════════════════════════════════════════╝""")

# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  CALENDAR SPREAD ALGO  |  {ts()}
║  Dynamic sizing — lots vary by score, VIX, regime, and margin
║  Press Ctrl+C at any time to log trades / update margin / check P&L
╚══════════════════════════════════════════════════════════════════╝
  File: {FILEPATH}
  Logs: C:\\AlgoTrading\\logs\\
""")

logger.load_today()
realised, open_c, total = logger.get_daily_pnl()
print(f"  Today's P&L loaded: ₹{realised:+,.0f}  ({total} trades)")

# Initialise dynamic position sizer (asks margin from user)
_sizer = None
if _sizer_available:
    _sizer = PositionSizer()
else:
    print("  Running with fixed 1 lot — add position_sizer.py for dynamic sizing.")

if "--input" in sys.argv:
    logger.interactive_input()

print(f"\n  [{ts()}] Fetching VIX...")
v = fetch_vix()
state["vix"] = v
print(f"  [{ts()}] VIX = {v}\n" if v else f"  [{ts()}] VIX unavailable\n")

cycle = 0; vix_countdown = 0; last_analysis = 0

while True:
    try:
        cycle += 1; vix_countdown += 1

        if vix_countdown >= 20:
            v = fetch_vix()
            if v and v != state["vix"]:
                print(f"\n  [{ts()}] VIX: {state['vix']} → {v}")
                state["vix"] = v
            vix_countdown = 0

        raw = safe_read()
        if raw is None:
            state["fail"] += 1
            if state["fail"] % 5 == 1:
                print(f"  [{ts()}] Waiting for file... {state['fail']}")
            time.sleep(REFRESH); continue
        state["fail"] = 0

        meta = read_metadata(raw)
        if meta["spot"]:     state["spot"]     = meta["spot"]
        if meta["near_exp"]: state["near_exp"] = meta["near_exp"]
        if meta["far_exp"]:  state["far_exp"]  = meta["far_exp"]

        main_df              = read_main_table(raw)
        _, far_pe_df         = read_secondary_table(raw)
        if main_df is None or main_df.empty:
            print(f"  [{ts()}] No data yet..."); time.sleep(REFRESH); continue

        atm = detect_atm(state["spot"], main_df)
        if atm and atm != state["atm"]:
            state["atm"] = atm
            print(f"\n  [{ts()}] ATM: {atm}  Spot: {state['spot']}")

        vix = state["vix"]; strike = state["atm"]
        if not strike: time.sleep(REFRESH); continue

        # ── REGIME DETECTION every cycle ────────────────────────
        regime = None
        if _regime_available and _regime_engine:
            regime = _regime_engine.detect(
                main_df, vix, state["spot"], state["near_exp"], strike)

        # Print full regime report when regime changes
        if regime and _regime_engine.changed():
            print_regime_report(regime)
            _regime_engine.mark_printed()
            # Hard stop for extreme panic / dead market
            if regime["key"] == "R8_EXTREME_PANIC":
                print(f"\n  [{ts()}] 💀 EXTREME PANIC — NO NEW SIGNALS GENERATED")
            elif regime["key"] == "R1_DEAD":
                dead_conf = regime.get("dead_confidence", 0)
                print(f"\n  [{ts()}] 🔴 DEAD MARKET DETECTED (conf:{dead_conf}%) "
                      f"— Calendar spread edge is NEGATIVE. Reducing analysis.")

        # ── FULL ANALYSIS every ANALYSIS_EVERY seconds ──────────
        now = time.time()
        if (now - last_analysis) >= ANALYSIS_EVERY:
            results = scan_all_strikes(main_df)

            # Apply regime filter — blocks/penalises wrong strategies
            if regime and _regime_available:
                results = filter_by_regime(results, regime)

            # Show regime size warning before analysis
            if regime:
                mult = regime.get("size_mult", 1.0)
                if mult < 1.0:
                    print(f"\n  [{ts()}] ⚠  REGIME SIZE OVERRIDE: "
                          f"Use only {mult*100:.0f}% of normal lot size  "
                          f"({regime['name']})")

            print_analysis(results, vix)
            last_analysis = now

            # Only alert on unblocked opportunities
            unblocked = [r for r in results if not r.get("regime_blocked", False)]
            if unblocked:
                top = unblocked[0]
                ak  = f"CE_{top['near_strike']}_{top['direction']}"
                if top["score"] >= IMMEDIATE_SCORE and ak not in state["alerted"]:
                    # Apply regime size to lots advice
                    mult = regime.get("size_mult", 1.0) if regime else 1.0
                    if mult == 0.0:
                        print(f"  [{ts()}] Signal suppressed — regime blocks all entries")
                    else:
                        print_immediate_alert(top, vix)
                        state["alerted"].add(ak)

        # Recovery check
        if CARRIED["ce_pos"] and cycle % 20 == 1:
            d = get_ce_data(main_df, CARRIED["ce_strike"] or strike)
            recovery_advice(d, vix)

        # Live tick
        row = main_df[main_df["near_strike"] == strike]
        if not row.empty:
            try:
                ce_sp = round(float(row["far_prem"].iloc[0]) -
                              float(row["near_ask"].iloc[0]), 2)
                if ce_sp != state["last_ce"]:
                    state["last_ce"] = ce_sp
                    ft = _f(row["far_theta"].iloc[0]); nt = _f(row["near_theta"].iloc[0])
                    fair = round((ft-nt)*0.5,2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
                    realised, open_c, _ = logger.get_daily_pnl()
                    regime_line = regime_summary_line(regime) if regime and _regime_available else ""
                    print(f"  [{ts()}]  ATM:{strike}  CE-Spread:{ce_sp:+.2f}  "
                          f"Fair:{fair:+.2f}  VIX:{vix}  "
                          f"P&L:₹{realised:+,.0f}  {regime_line}")
            except Exception: pass

        if cycle % 10 == 0:
            realised, open_c, _ = logger.get_daily_pnl()
            if _sizer_available and _sizer:
                _sizer.show_status()
            else:
                print(f"  [{ts()}]  P&L:₹{realised:+,.0f}  Open:{open_c}")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n\n  [{ts()}] Paused — what would you like to do?")
        print("    [1] Log a trade entry")
        print("    [2] Close a trade / log exit")
        print("    [3] Update available margin")
        print("    [4] Show today's P&L summary")
        print("    [5] Resume algo")
        try:
            choice = input("  Choice: ").strip()
        except EOFError:
            choice = "5"
        if choice == "1":
            logger.interactive_input("Enter Trade")
        elif choice == "2":
            logger.interactive_input("Close Trade")
        elif choice == "3" and _sizer_available and _sizer:
            _sizer.update_margin()
        elif choice == "4":
            logger.print_daily_summary()
            if _sizer_available and _sizer:
                _sizer.show_status()
        print(f"\n  [{ts()}] Resuming...\n")

    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        time.sleep(REFRESH)
