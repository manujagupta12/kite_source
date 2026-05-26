"""
NIFTY CALENDAR SPREAD — CONTINUOUS ANALYSIS ENGINE
===================================================
Why old script was silent for 2 hours:
  - It only fired when spread > fixed threshold (3pts)
  - In calm markets, spreads rarely deviate that much
  - It only watched ONE strike (ATM)

What this version does:
  - Scans ALL available strikes every cycle
  - Scores every opportunity on 6 factors
  - Always prints TOP 3 ranked trades every 60 seconds
  - Fires IMMEDIATE alerts on strong signals (score > 70)
  - Shows EXACT prices, lots, target, SL on every recommendation
  - Tracks P&L toward ₹50K daily target
  - Recovery mode for carried losing positions
  - Risk category on every signal (LOW / MEDIUM / HIGH / EXTREME)

SCORING FACTORS (0-100 total):
  1. Spread vs Fair Value deviation     (0-25 pts)
  2. Theta edge (near decays faster)    (0-20 pts)
  3. Vega differential                  (0-15 pts)
  4. Volume / liquidity                 (0-15 pts)
  5. Delta neutrality                   (0-15 pts)
  6. VIX-adjusted opportunity           (0-10 pts)
"""

import time, shutil, requests, re
import pandas as pd
import numpy as np
from datetime import datetime, date

# ============================================================
#  SETTINGS
# ============================================================
FILEPATH        = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH        = r"C:\AlgoTrading\data\_temp_read.xls"

DAILY_TARGET    = 50000   # ₹ daily profit target
LOT_SIZE        = 25      # NIFTY lot size
MARGIN_PER_LOT  = 80000   # ₹ per calendar spread lot
MAX_MARGIN      = 5000000 # ₹ 50 lakh

TARGET_PTS      = 4       # profit target per spread (points)
STOPLOSS_PTS    = 3       # stop loss per spread (points)
REFRESH         = 3       # seconds between reads
ANALYSIS_EVERY  = 60      # seconds between full ranked analysis prints
IMMEDIATE_SCORE = 70      # score threshold for immediate alert

VIX_LOW         = 15
VIX_MEDIUM      = 19
VIX_HIGH        = 22

# Carried overnight positions — fill these in if you have open positions
CARRIED = {
    "ce_pos":    None,   # "LONG" or "SHORT" or None
    "ce_entry":  None,   # entry spread e.g. -5.20
    "ce_strike": None,   # strike e.g. 23800
    "pe_pos":    None,
    "pe_entry":  None,
    "pe_strike": None,
}

# ============================================================
#  COLUMN MAP (verified by spread_debug.py)
# ============================================================
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

# ============================================================
#  STATE
# ============================================================
state = {
    "ce_pos": None, "ce_entry": None, "ce_strike": None, "ce_lots": 0,
    "pe_pos": None, "pe_entry": None, "pe_strike": None, "pe_lots": 0,
    "carried": CARRIED.copy(),
    "realised_pnl":   0.0,
    "unrealised_pnl": 0.0,
    "target_locked":  False,
    "spot": None, "vix": None, "atm": None,
    "near_exp": None, "far_exp": None,
    "last_analysis_time": 0,
    "last_ce": None, "last_pe": None,
    "fail": 0,
}

# ============================================================
#  HELPERS
# ============================================================
def ts():
    return datetime.now().strftime("%H:%M:%S")

def _f(v):
    try:
        f = float(v)
        return np.nan if (np.isnan(f) or np.isinf(f)) else f
    except Exception:
        return np.nan

def total_pnl():
    return round(state["realised_pnl"] + state["unrealised_pnl"], 2)

def remaining_target():
    return max(0.0, DAILY_TARGET - total_pnl())

def lots_for_target(remaining, pts):
    if pts <= 0:
        return 1
    n = int(np.ceil(remaining / (pts * LOT_SIZE)))
    return min(max(n, 1), int(MAX_MARGIN / MARGIN_PER_LOT))

def risk_label(vix, score):
    if   vix is None or vix < VIX_LOW:    return "🟢 LOW"
    elif vix < VIX_MEDIUM:                return "🟡 MEDIUM"
    elif vix < VIX_HIGH:                  return "🟠 HIGH"
    else:                                 return "🔴 EXTREME"

# ============================================================
#  VIX FETCH
# ============================================================
_sess = requests.Session()
_sess.headers.update({
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":          "application/json",
    "Referer":         "https://www.nseindia.com",
    "Accept-Language": "en-US,en;q=0.9",
})

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

# ============================================================
#  FILE READ
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
    try:
        v = float(raw.iloc[0, C_SPOT])
        if 15000 < v < 35000:
            meta["spot"] = round(v, 2)
    except Exception:
        pass
    today = date.today()
    expiry_dates = []
    for r in range(min(2, len(raw))):
        for c in range(len(raw.columns)):
            cell = str(raw.iloc[r, c]).strip().upper()
            m = re.search(r'NIFTY(\d{1,2})([A-Z]{3})(\d{4})', cell)
            if m:
                try:
                    exp_dt = datetime.strptime(
                        f"{m.group(1)}{m.group(2)}{m.group(3)}", "%d%b%Y").date()
                    if (today - exp_dt).days < 60:
                        expiry_dates.append((exp_dt, cell))
                except ValueError:
                    pass
    seen, unique = set(), []
    for dt, label in sorted(expiry_dates):
        if label not in seen:
            seen.add(label)
            unique.append((dt, label))
    if len(unique) >= 2:
        meta["near_exp"] = unique[0][1]
        meta["far_exp"]  = unique[1][1]
    elif len(unique) == 1:
        meta["near_exp"] = unique[0][1]
    return meta

# ============================================================
#  TABLE READERS
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
                "near_delta":  _f(row.iloc[C_NEAR_DELTA]),
                "far_delta":   _f(row.iloc[C_FAR_DELTA]),
                "straddle":    _f(row.iloc[C_STRADDLE]),
                "far_prem":    _f(row.iloc[C_FAR_PREM]),
            })
        except (IndexError, ValueError, TypeError):
            continue
    return pd.DataFrame(rows) if rows else None

def read_secondary_table(raw):
    ce_rows, pe_rows = [], []
    for idx in range(len(raw)):
        row = raw.iloc[idx]
        try:
            t = str(row.iloc[C_SEC_TYPE]).strip().upper()
            if t not in ("CE","PE"):
                continue
            s = float(row.iloc[C_SEC_STRIKE])
            if not (21000 < s < 27000):
                continue
            entry = {"strike": int(s),
                     "bid": float(row.iloc[C_SEC_BID]),
                     "ask": float(row.iloc[C_SEC_ASK]),
                     "vol": _f(row.iloc[C_SEC_VOL])}
            (ce_rows if t=="CE" else pe_rows).append(entry)
        except (IndexError, ValueError, TypeError):
            continue
    return (pd.DataFrame(ce_rows) if ce_rows else None,
            pd.DataFrame(pe_rows) if pe_rows else None)

# ============================================================
#  ATM DETECTION
# ============================================================
def detect_atm(spot, main_df):
    available = sorted(main_df["near_strike"].dropna().astype(int).unique())
    if not available:
        return None
    if spot and 21000 < spot < 27000:
        atm = int(round(spot / 50.0) * 50)
        return atm if atm in available else min(available, key=lambda s: abs(s-atm))
    valid = main_df.dropna(subset=["near_vol"])
    if not valid.empty:
        return int(valid.loc[valid["near_vol"].idxmax(), "near_strike"])
    return available[len(available)//2]

# ============================================================
#  SPREAD & PRICE DETAIL
# ============================================================
def get_ce_data(main_df, strike):
    """Returns full CE price detail dict for a strike, or None."""
    row = main_df[main_df["near_strike"] == strike]
    if row.empty:
        return None
    try:
        far_prem  = float(row["far_prem"].iloc[0])
        near_ask  = float(row["near_ask"].iloc[0])
        near_bid  = float(row["near_bid"].iloc[0])
        near_ltp  = float(row["near_ltp"].iloc[0])
        far_strike = int(row["far_strike"].iloc[0])
        if np.isnan(far_prem) or np.isnan(near_ask):
            return None
        spread    = round(far_prem - near_ask, 2)
        ft        = _f(row["far_theta"].iloc[0])
        nt        = _f(row["near_theta"].iloc[0])
        fv        = _f(row["far_vega"].iloc[0])
        nv        = _f(row["near_vega"].iloc[0])
        fd        = _f(row["far_delta"].iloc[0])
        nd        = _f(row["near_delta"].iloc[0])
        fvol      = _f(row["far_vol"].iloc[0])
        nvol      = _f(row["near_vol"].iloc[0])
        fair = round((ft-nt)*0.5, 2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
        return {
            "type":         "CE",
            "near_strike":  strike,
            "far_strike":   far_strike,
            "spread":       spread,
            "fair":         fair,
            "deviation":    round(spread - fair, 2),
            "near_bid":     round(near_bid, 2),
            "near_ask":     round(near_ask, 2),
            "near_ltp":     round(near_ltp, 2),
            "far_bid":      round(far_prem, 2),
            "far_ask":      round(far_prem + 0.10, 2),
            "near_theta":   round(nt, 4) if not np.isnan(nt) else None,
            "far_theta":    round(ft, 4) if not np.isnan(ft) else None,
            "theta_edge":   round(abs(nt)-abs(ft), 4) if not (np.isnan(nt) or np.isnan(ft)) else 0,
            "near_vega":    round(nv, 4) if not np.isnan(nv) else None,
            "far_vega":     round(fv, 4) if not np.isnan(fv) else None,
            "vega_diff":    round(fv-nv, 4) if not (np.isnan(fv) or np.isnan(nv)) else 0,
            "near_delta":   round(nd, 4) if not np.isnan(nd) else None,
            "far_delta":    round(fd, 4) if not np.isnan(fd) else None,
            "delta_neutral":round(abs(nd-fd), 4) if not (np.isnan(nd) or np.isnan(fd)) else 1,
            "near_vol":     nvol,
            "far_vol":      fvol,
            "sell_near_at": round(near_bid - 0.05, 2),
            "buy_near_at":  round(near_ask + 0.05, 2),
            "buy_far_at":   round(far_prem + 0.05, 2),
            "sell_far_at":  round(far_prem - 0.05, 2),
        }
    except Exception:
        return None

def get_pe_data(main_df, far_pe_df, strike):
    """Returns full PE price detail dict for a strike, or None."""
    row = main_df[main_df["near_strike"] == strike]
    if row.empty or far_pe_df is None or far_pe_df.empty:
        return None
    try:
        straddle   = float(row["straddle"].iloc[0])
        near_ce    = float(row["near_bid"].iloc[0])
        far_strike = int(row["far_strike"].iloc[0])
        nt = _f(row["near_theta"].iloc[0])
        ft = _f(row["far_theta"].iloc[0])
        nv = _f(row["near_vega"].iloc[0])
        fv = _f(row["far_vega"].iloc[0])
        nvol = _f(row["near_vol"].iloc[0])
        if np.isnan(straddle) or np.isnan(near_ce):
            return None
        near_pe_ask = round(straddle - near_ce, 2)
        near_pe_bid = round(near_pe_ask - 0.10, 2)
    except Exception:
        return None

    pe_row = far_pe_df[far_pe_df["strike"] == strike]
    if pe_row.empty:
        pe_row = far_pe_df.iloc[[0]]
    try:
        far_pe_bid = float(pe_row["bid"].iloc[0])
        far_pe_ask = float(pe_row["ask"].iloc[0])
        if np.isnan(far_pe_bid):
            return None
        spread = round(far_pe_bid - near_pe_ask, 2)
        fair   = round((ft-nt)*0.5, 2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
        return {
            "type":         "PE",
            "near_strike":  strike,
            "far_strike":   far_strike,
            "spread":       spread,
            "fair":         fair,
            "deviation":    round(spread - fair, 2),
            "near_bid":     round(near_pe_bid, 2),
            "near_ask":     round(near_pe_ask, 2),
            "far_bid":      round(far_pe_bid, 2),
            "far_ask":      round(far_pe_ask, 2),
            "near_theta":   round(nt, 4) if not np.isnan(nt) else None,
            "far_theta":    round(ft, 4) if not np.isnan(ft) else None,
            "theta_edge":   round(abs(nt)-abs(ft), 4) if not (np.isnan(nt) or np.isnan(ft)) else 0,
            "near_vega":    round(nv, 4) if not np.isnan(nv) else None,
            "far_vega":     round(fv, 4) if not np.isnan(fv) else None,
            "vega_diff":    round(fv-nv, 4) if not (np.isnan(fv) or np.isnan(nv)) else 0,
            "delta_neutral":0.5,
            "near_vol":     nvol,
            "far_vol":      _f(pe_row["vol"].iloc[0]),
            "sell_near_at": round(near_pe_bid - 0.05, 2),
            "buy_near_at":  round(near_pe_ask + 0.05, 2),
            "buy_far_at":   round(far_pe_bid  + 0.05, 2),
            "sell_far_at":  round(far_pe_bid  - 0.05, 2),
        }
    except Exception:
        return None

# ============================================================
#  OPPORTUNITY SCORER  (0-100)
#
#  This is the core engine. Instead of waiting for a threshold
#  to be crossed, we score every strike and always show the best.
# ============================================================
def score_opportunity(d, vix):
    """
    Score a calendar spread opportunity on 6 factors.
    Returns score (0-100) and detailed breakdown dict.
    """
    score = 0
    breakdown = {}

    # ── Factor 1: Spread vs Fair Value deviation (0-25) ──────
    # Any deviation from fair value is an edge.
    # Negative deviation (spread < fair) = long calendar opportunity
    # Positive deviation (spread > fair) = short calendar opportunity
    dev    = abs(d["deviation"])
    # Even 0.5pt deviation scores something. Max score at 5pt deviation.
    f1     = min(25, int((dev / 5.0) * 25))
    score += f1
    breakdown["spread_vs_fair"] = f1

    # ── Factor 2: Theta Edge (0-20) ──────────────────────────
    # Near month should decay faster than far month.
    # Positive theta_edge means calendar spread profits from time decay.
    te     = d.get("theta_edge", 0) or 0
    f2     = min(20, int((abs(te) / 0.02) * 20)) if te > 0 else 5
    score += f2
    breakdown["theta_edge"] = f2

    # ── Factor 3: Vega Differential (0-15) ───────────────────
    # Far vega > near vega = good for long calendar (benefits from vol rise)
    vd     = d.get("vega_diff", 0) or 0
    f3     = min(15, int((abs(vd) / 20.0) * 15)) if vd > 0 else 3
    score += f3
    breakdown["vega_diff"] = f3

    # ── Factor 4: Liquidity / Volume (0-15) ──────────────────
    nvol   = d.get("near_vol", 0) or 0
    fvol   = d.get("far_vol", 0) or 0
    total_vol = (nvol + fvol)
    f4     = min(15, int((total_vol / 500000) * 15))
    score += f4
    breakdown["liquidity"] = f4

    # ── Factor 5: Delta Neutrality (0-15) ────────────────────
    # Calendar spreads work best when near and far deltas are close
    dn     = d.get("delta_neutral", 1) or 1
    f5     = max(0, 15 - int(dn * 30))
    score += f5
    breakdown["delta_neutral"] = f5

    # ── Factor 6: VIX context (0-10) ─────────────────────────
    if vix is None:
        f6 = 5
    elif vix < VIX_LOW:
        f6 = 10   # low VIX = stable = calendar spreads thrive
    elif vix < VIX_MEDIUM:
        f6 = 8
    elif vix < VIX_HIGH:
        f6 = 5    # elevated but tradeable
    else:
        f6 = 2    # extreme — opportunity exists but risky
    score += f6
    breakdown["vix_context"] = f6

    # ── Direction recommendation ──────────────────────────────
    dev_raw = d["deviation"]
    if dev_raw < -1.0:
        direction = "LONG"    # spread too cheap — buy calendar
        reason    = f"Spread {d['spread']:+.2f} below fair {d['fair']:+.2f} by {abs(dev_raw):.2f}pts"
    elif dev_raw > 1.0:
        direction = "SHORT"   # spread too expensive — sell calendar
        reason    = f"Spread {d['spread']:+.2f} above fair {d['fair']:+.2f} by {dev_raw:.2f}pts"
    elif d.get("theta_edge", 0) > 0:
        direction = "LONG"    # theta supports long calendar even if spread is fair
        reason    = f"Theta edge {d.get('theta_edge',0):.4f} favours long calendar"
    else:
        direction = "LONG"    # default bias
        reason    = "Neutral spread — theta/vega setup favours long calendar"

    return score, direction, reason, breakdown


# ============================================================
#  FULL MARKET ANALYSIS  — scans all strikes, ranks top 3
# ============================================================
def run_full_analysis(main_df, far_pe_df, vix, spot):
    """
    Scans every available strike for CE and PE opportunities.
    Returns list of (score, direction, data_dict, reason, breakdown)
    sorted best-first.
    """
    opportunities = []
    strikes = sorted(main_df["near_strike"].dropna().astype(int).unique())

    for strike in strikes:
        # CE opportunity
        d = get_ce_data(main_df, strike)
        if d:
            sc, direction, reason, breakdown = score_opportunity(d, vix)
            opportunities.append((sc, direction, d, reason, breakdown))

        # PE opportunity (only if secondary table has PE data)
        dp = get_pe_data(main_df, far_pe_df, strike)
        if dp:
            sc, direction, reason, breakdown = score_opportunity(dp, vix)
            opportunities.append((sc, direction, dp, reason, breakdown))

    # Sort by score descending
    opportunities.sort(key=lambda x: x[0], reverse=True)
    return opportunities


# ============================================================
#  PRINT RANKED ANALYSIS
# ============================================================
def print_ranked_analysis(opportunities, vix, top_n=5):
    now = ts()
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  📊 FULL MARKET ANALYSIS  |  {now}  |  VIX={vix}
║  Scanned {len(opportunities)} opportunities across all strikes
╠══════════════════════════════════════════════════════════════════╣""")

    if not opportunities:
        print("║  No opportunities found. Market data may be incomplete.")
        print("╚══════════════════════════════════════════════════════════════════╝")
        return

    for rank, (score, direction, d, reason, breakdown) in \
            enumerate(opportunities[:top_n], 1):

        opt   = d["type"]
        strike = d["near_strike"]
        spread = d["spread"]
        fair   = d["fair"]
        vix_   = vix
        rlabel = risk_label(vix_, score)

        # Lot sizing
        lots   = lots_for_target(remaining_target(), TARGET_PTS)
        tp_pts = round(spread + TARGET_PTS, 2) if direction=="LONG" \
                 else round(spread - TARGET_PTS, 2)
        sl_pts = round(spread - STOPLOSS_PTS, 2) if direction=="LONG" \
                 else round(spread + STOPLOSS_PTS, 2)
        tp_inr = round(TARGET_PTS * LOT_SIZE * lots, 0)
        sl_inr = round(STOPLOSS_PTS * LOT_SIZE * lots, 0)

        # Grade
        if score >= 80:   grade = "⭐⭐⭐ STRONG"
        elif score >= 60: grade = "⭐⭐   GOOD"
        elif score >= 40: grade = "⭐    MODERATE"
        else:             grade = "     WEAK"

        # Order prices
        if direction == "LONG":
            leg1 = f"BUY  Far  {opt} {d['far_strike']}  @ {d['buy_far_at']}"
            leg2 = f"SELL Near {opt} {strike}  @ {d['sell_near_at']}"
        else:
            leg1 = f"SELL Far  {opt} {d['far_strike']}  @ {d['sell_far_at']}"
            leg2 = f"BUY  Near {opt} {strike}  @ {d['buy_near_at']}"

        print(f"""║
║  RANK #{rank}  {grade}  |  Score: {score}/100  |  {rlabel}
║  ─────────────────────────────────────────────────────────────
║  {opt} CALENDAR  Strike={strike}  Direction={direction}
║  WHY: {reason}
║
║  PRICES
║    Near {opt}: Bid={d['near_bid']}  Ask={d['near_ask']}
║    Far  {opt}: Bid={d['far_bid']}  Ask={d['far_ask']}
║    Spread={spread:+.2f}  Fair={fair:+.2f}  Deviation={d['deviation']:+.2f}pts
║
║  GREEKS EDGE
║    Theta  — Near:{d.get('near_theta','N/A')}  Far:{d.get('far_theta','N/A')}  Edge:{d.get('theta_edge',0):.4f}
║    Vega   — Near:{d.get('near_vega','N/A')}  Far:{d.get('far_vega','N/A')}   Diff:{d.get('vega_diff',0):.4f}
║    Delta  — Near:{d.get('near_delta','N/A')}  Far:{d.get('far_delta','N/A')}
║
║  SCORE BREAKDOWN
║    Spread/Fair:{breakdown['spread_vs_fair']}/25  Theta:{breakdown['theta_edge']}/20  \
Vega:{breakdown['vega_diff']}/15  Liquidity:{breakdown['liquidity']}/15  \
Delta:{breakdown['delta_neutral']}/15  VIX:{breakdown['vix_context']}/10
║
║  ORDER INSTRUCTIONS  ({lots} lot{"s" if lots>1 else ""})
║    LEG 1 ➜ {leg1}
║    LEG 2 ➜ {leg2}
║
║  TARGETS & STOPS
║    TARGET  → Exit spread {"≥" if direction=="LONG" else "≤"} {tp_pts:.2f}  \
(+{TARGET_PTS}pts = +₹{tp_inr:,.0f})
║    STOP    → Exit spread {"≤" if direction=="LONG" else "≥"} {sl_pts:.2f}  \
(-{STOPLOSS_PTS}pts = -₹{sl_inr:,.0f})
║    NET P&L POTENTIAL: +₹{tp_inr:,.0f} reward  vs  -₹{sl_inr:,.0f} risk  \
(R:R = {round(tp_inr/sl_inr,1)}:1)""")

    print(f"""║
║  DAILY P&L TRACKER
║    Realised:₹{state['realised_pnl']:+,.0f}  Unrealised:₹{state['unrealised_pnl']:+,.0f}
║    Total Today:₹{total_pnl():+,.0f}  /  Target:₹{DAILY_TARGET:,}
║    {'✅ TARGET HIT — STOP NEW ENTRIES' if state['target_locked'] else f'Remaining: ₹{remaining_target():,.0f}'}
╚══════════════════════════════════════════════════════════════════╝""")


# ============================================================
#  IMMEDIATE ALERT  (for very high score signals)
# ============================================================
def print_immediate_alert(score, direction, d, reason, vix):
    opt    = d["type"]
    strike = d["near_strike"]
    spread = d["spread"]
    fair   = d["fair"]
    lots   = lots_for_target(remaining_target(), TARGET_PTS)
    tp_pts = round(spread + TARGET_PTS if direction=="LONG" else spread - TARGET_PTS, 2)
    sl_pts = round(spread - STOPLOSS_PTS if direction=="LONG" else spread + STOPLOSS_PTS, 2)
    tp_inr = round(TARGET_PTS * LOT_SIZE * lots, 0)
    sl_inr = round(STOPLOSS_PTS * LOT_SIZE * lots, 0)

    if direction == "LONG":
        leg1 = f"BUY  Far  {opt} {d['far_strike']}  LIMIT @ {d['buy_far_at']}"
        leg2 = f"SELL Near {opt} {strike}  LIMIT @ {d['sell_near_at']}"
    else:
        leg1 = f"SELL Far  {opt} {d['far_strike']}  LIMIT @ {d['sell_far_at']}"
        leg2 = f"BUY  Near {opt} {strike}  LIMIT @ {d['buy_near_at']}"

    print(f"""
🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔
  IMMEDIATE SIGNAL  |  Score: {score}/100  |  [{ts()}]
  {opt} {direction} CALENDAR  Strike={strike}  VIX={vix}
  {risk_label(vix, score)}
  ─────────────────────────────────────────────────────────────
  WHY NOW: {reason}
  Spread={spread:+.2f}  Fair={fair:+.2f}  Deviation={d['deviation']:+.2f}pts
  ─────────────────────────────────────────────────────────────
  PLACE THESE ORDERS NOW  ({lots} lots):
    LEG 1 ➜ {leg1}
    LEG 2 ➜ {leg2}
  ─────────────────────────────────────────────────────────────
  TARGET  → {tp_pts:.2f}  (+₹{tp_inr:,.0f})
  STOP    → {sl_pts:.2f}  (-₹{sl_inr:,.0f})
  R:R Ratio = {round(tp_inr/max(sl_inr,1),1)}:1
🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔""")


# ============================================================
#  RECOVERY ADVISOR
# ============================================================
def recovery_advice(opt, pos, entry, current, d, vix, main_df):
    loss_pts = round((entry - current) if pos=="LONG" else (current - entry), 2)
    loss_inr = round(loss_pts * LOT_SIZE, 0)
    strike   = d["near_strike"]

    # Theta recovery time estimate
    te = d.get("theta_edge", 0) or 0
    days_to_recover = round(abs(loss_pts) / abs(te), 1) \
                      if te and abs(te) > 0.001 else "unknown"

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  ⚠  CARRIED POSITION RECOVERY  |  {ts()}
║  {opt} {pos} Calendar  |  Strike: {strike}
║  Entry Spread: {entry:+.2f}  →  Current: {current:+.2f}
║  LOSS: {loss_pts:.2f}pts  =  ₹{abs(loss_inr):,.0f}  |  VIX={vix}
╠══════════════════════════════════════════════════════════════════╣
║
║  OPTION 1 🟢 [LOW RISK] — HOLD & LET THETA WORK
║    Daily theta edge in your favour: {te:.4f} pts/day
║    Estimated recovery time: {days_to_recover} days (if market stays range-bound)
║    Action: No new orders. Monitor every 30 mins.
║    ✅ Best if: Market stable, VIX not rising further
║
║  OPTION 2 🟡 [MEDIUM RISK] — ADD OPPOSITE STRIKE HEDGE
║    Buy a calendar spread at strike {strike+100} to cap further loss
║    LEG ➜ BUY Far {opt} {strike+100}   LIMIT @ {round(d['far_bid']+0.1,2)}
║    LEG ➜ SELL Near {opt} {strike+100}  LIMIT @ {round(d['near_bid']-0.05,2)}
║    Effect: Converts to double calendar. Max loss locked in now.
║    ✅ Best if: You expect market to drift toward {strike+100}
║
║  OPTION 3 🟡 [MEDIUM RISK] — ROLL NEAR LEG
║    Close your near {opt} {strike}, reopen at {strike+50}
║    CLOSE  ➜ BUY  Near {opt} {strike}    LIMIT @ {d['buy_near_at']}
║    REOPEN ➜ SELL Near {opt} {strike+50}  LIMIT @ market
║    Effect: Shifts breakeven by 50pts in your favour
║    ✅ Best if: Spot drifted ~50pts against you
║
║  OPTION 4 🔴 [HIGH RISK] — CUT LOSS & REDEPLOY
║    Close both legs now and enter fresh opportunity
║    LEG 1 ➜ SELL Far  {opt} {d['far_strike']}  LIMIT @ {d['sell_far_at']}
║    LEG 2 ➜ BUY  Near {opt} {strike}  LIMIT @ {d['buy_near_at']}
║    Loss crystallised: ₹{abs(loss_inr):,.0f}
║    Fresh capital freed: ~₹{MARGIN_PER_LOT:,}
║    ✅ Best if: Spread moving further against you, no theta support
║
╚══════════════════════════════════════════════════════════════════╝""")


# ============================================================
#  EXIT DISPLAY
# ============================================================
def show_exit(opt, direction, strike, entry, current, reason, pnl_pts, d, lots):
    pnl_inr = round(pnl_pts * LOT_SIZE * lots, 0)
    emoji   = "✅" if pnl_pts >= 0 else "❌"
    if direction == "LONG":
        l1 = f"SELL Far  {opt} {d['far_strike']}  LIMIT @ {d['sell_far_at']}"
        l2 = f"BUY  Near {opt} {strike}  LIMIT @ {d['buy_near_at']}"
    else:
        l1 = f"BUY  Far  {opt} {d['far_strike']}  LIMIT @ {d['buy_far_at']}"
        l2 = f"SELL Near {opt} {strike}  LIMIT @ {d['sell_near_at']}"
    print(f"""
┌──────────────────────────────────────────────────────────────┐
│  <<< EXIT  {emoji}  {opt} {reason}  [{ts()}]
│  Strike:{strike}  Direction:{direction}  Lots:{lots}
│  Entry:{entry:+.2f} → Current:{current:+.2f}  P&L:{pnl_pts:+.2f}pts = ₹{pnl_inr:+,.0f}
│  LEG 1 ➜ {l1}
│  LEG 2 ➜ {l2}
│  Running Total Today: ₹{state['realised_pnl']+pnl_inr:+,.0f} / ₹{DAILY_TARGET:,}
└──────────────────────────────────────────────────────────────┘""")


# ============================================================
#  LIVE TICK LINE
# ============================================================
def live_line(label, spread, fair, pos=None, entry=None, lots=0, vix=None):
    mtm = ""
    if pos and entry is not None:
        pp  = round((spread-entry) if pos=="LONG" else (entry-spread), 2)
        inr = round(pp * LOT_SIZE * max(lots,1), 0)
        mtm = f"  [{pos}] MTM:{pp:+.2f}pts ₹{inr:+,.0f}"
    print(f"  [{ts()}] {label}  Spread:{spread:+.2f}  Fair:{fair:+.2f}{mtm}"
          f"{'  VIX='+str(vix) if vix else ''}")


# ============================================================
#  STARTUP BANNER
# ============================================================
def print_banner():
    s = state
    vix = s["vix"]
    mode = ("🔴 EXTREME" if vix and vix >= VIX_HIGH else
            "🟠 HIGH"    if vix and vix >= VIX_MEDIUM else
            "🟡 MEDIUM"  if vix and vix >= VIX_LOW else
            "🟢 LOW")
    print(f"""
{'='*66}
  NIFTY CALENDAR SPREAD ALGO  |  {ts()}
  Spot: {s['spot'] or '...'}  ATM: {s['atm'] or '...'}
  Near: {s['near_exp'] or '...'}  Far: {s['far_exp'] or '...'}
  VIX : {vix or '...'}  Mode: {mode}
  Daily P&L: ₹{total_pnl():+,.0f} / ₹{DAILY_TARGET:,}  Remaining: ₹{remaining_target():,.0f}
  {'✅ TARGET LOCKED — NO NEW ENTRIES' if s['target_locked'] else 'Target not yet hit'}
{'='*66}
""")


# ============================================================
#  MAIN LOOP
# ============================================================
print(f"""
{'='*66}
  NIFTY CALENDAR — CONTINUOUS ANALYSIS ENGINE
  Full market scan every {ANALYSIS_EVERY}s  |  Immediate alerts score>{IMMEDIATE_SCORE}
  All strikes analysed  |  Top 5 ranked every cycle
{'='*66}
  File: {FILEPATH}
""")

if any(CARRIED.values()):
    print("  ⚠  Carried positions loaded. Recovery mode active.\n")

print(f"  [{ts()}] Fetching VIX...")
v = fetch_vix()
state["vix"] = v
print(f"  [{ts()}] VIX = {v}\n" if v else f"  [{ts()}] VIX unavailable\n")

prev_banner_key    = None
cycle              = 0
vix_countdown      = 0
last_analysis_ts   = 0
last_top_score     = 0
alerted_strikes    = set()   # avoid re-alerting same strike in same session

while True:
    try:
        cycle        += 1
        vix_countdown += 1

        # ── VIX REFRESH every ~60s ──────────────────────────────
        if vix_countdown >= 20:
            v = fetch_vix()
            if v and v != state["vix"]:
                print(f"\n  [{ts()}] VIX: {state['vix']} → {v}")
                state["vix"] = v
            vix_countdown = 0

        # ── READ EXCEL ──────────────────────────────────────────
        raw = safe_read()
        if raw is None:
            state["fail"] += 1
            if state["fail"] % 5 == 1:
                print(f"  [{ts()}] Waiting for file... {state['fail']}")
            time.sleep(REFRESH)
            continue
        state["fail"] = 0

        # ── METADATA ────────────────────────────────────────────
        meta = read_metadata(raw)
        if meta["spot"]:     state["spot"]     = meta["spot"]
        if meta["near_exp"]: state["near_exp"] = meta["near_exp"]
        if meta["far_exp"]:  state["far_exp"]  = meta["far_exp"]

        main_df             = read_main_table(raw)
        far_ce_df, far_pe_df = read_secondary_table(raw)

        if main_df is None or main_df.empty:
            print(f"  [{ts()}] No data yet...")
            time.sleep(REFRESH)
            continue

        atm = detect_atm(state["spot"], main_df)
        if atm and atm != state["atm"]:
            state["atm"] = atm
            print(f"\n  [{ts()}] ATM Strike: {atm}  Spot={state['spot']}")

        vix    = state["vix"]
        strike = state["atm"]
        if not strike:
            time.sleep(REFRESH)
            continue

        # ── BANNER on config change ──────────────────────────────
        bkey = (vix, state["spot"], state["atm"], state["near_exp"])
        if bkey != prev_banner_key or cycle == 1:
            print_banner()
            prev_banner_key = bkey

        # ── FULL MARKET ANALYSIS every ANALYSIS_EVERY seconds ───
        now_epoch = time.time()
        do_analysis = (now_epoch - last_analysis_ts) >= ANALYSIS_EVERY

        if do_analysis:
            opps = run_full_analysis(main_df, far_pe_df, vix, state["spot"])
            print_ranked_analysis(opps, vix, top_n=5)
            last_analysis_ts = now_epoch

            # Immediate alert if top opportunity scores > IMMEDIATE_SCORE
            if opps:
                top_score, top_dir, top_d, top_reason, _ = opps[0]
                alert_key = f"{top_d['type']}_{top_d['near_strike']}_{top_dir}"
                if (top_score >= IMMEDIATE_SCORE
                        and alert_key not in alerted_strikes
                        and not state["target_locked"]):
                    print_immediate_alert(top_score, top_dir, top_d,
                                          top_reason, vix)
                    alerted_strikes.add(alert_key)

        # ── LIVE CE TICK ────────────────────────────────────────
        ce_d = get_ce_data(main_df, strike)
        if ce_d:
            ce_spread = ce_d["spread"]
            ce_fair   = ce_d["fair"]

            if ce_spread != state["last_ce"]:
                state["last_ce"] = ce_spread
                live_line(f"CE {strike}", ce_spread, ce_fair,
                          state["ce_pos"], state["ce_entry"],
                          state["ce_lots"], vix)

            # Update unrealised P&L for active CE position
            if state["ce_pos"] and state["ce_entry"] is not None:
                pp = (ce_spread-state["ce_entry"]) if state["ce_pos"]=="LONG" \
                     else (state["ce_entry"]-ce_spread)
                state["unrealised_pnl"] = round(
                    pp * LOT_SIZE * state["ce_lots"], 2)

                # Check exit
                if not state["target_locked"]:
                    pnl_pts = round(pp, 2)
                    if pnl_pts >= TARGET_PTS:
                        show_exit("CE", state["ce_pos"], strike,
                                  state["ce_entry"], ce_spread,
                                  "TARGET HIT", pnl_pts, ce_d, state["ce_lots"])
                        state["realised_pnl"] += round(
                            pnl_pts*LOT_SIZE*state["ce_lots"], 0)
                        state["unrealised_pnl"] = 0
                        state["ce_pos"]   = None
                        state["ce_entry"] = None
                        state["ce_lots"]  = 0
                        if total_pnl() >= DAILY_TARGET:
                            state["target_locked"] = True
                            print(f"\n  🎯 DAILY TARGET HIT!  "
                                  f"₹{total_pnl():,.0f}  NO NEW ENTRIES.\n")
                    elif pnl_pts <= -STOPLOSS_PTS:
                        show_exit("CE", state["ce_pos"], strike,
                                  state["ce_entry"], ce_spread,
                                  "STOPLOSS HIT", pnl_pts, ce_d, state["ce_lots"])
                        state["realised_pnl"] += round(
                            pnl_pts*LOT_SIZE*state["ce_lots"], 0)
                        state["unrealised_pnl"] = 0
                        state["ce_pos"]   = None
                        state["ce_entry"] = None
                        state["ce_lots"]  = 0

            # Recovery for carried CE
            c = state["carried"]
            if c["ce_pos"] and c["ce_entry"] is not None:
                cs = c["ce_strike"] or strike
                cd = get_ce_data(main_df, cs)
                if cd and cycle % 20 == 1:
                    recovery_advice("CE", c["ce_pos"], c["ce_entry"],
                                    cd["spread"], cd, vix, main_df)

        # ── LIVE PE TICK ────────────────────────────────────────
        pe_d = get_pe_data(main_df, far_pe_df, strike)
        if pe_d:
            pe_spread = pe_d["spread"]
            if pe_spread != state["last_pe"]:
                state["last_pe"] = pe_spread
                live_line(f"PE {strike}", pe_spread, pe_d["fair"],
                          state["pe_pos"], state["pe_entry"],
                          state["pe_lots"], vix)

            if state["pe_pos"] and state["pe_entry"] is not None:
                pp = (pe_spread-state["pe_entry"]) if state["pe_pos"]=="LONG" \
                     else (state["pe_entry"]-pe_spread)
                pnl_pts = round(pp, 2)
                if pnl_pts >= TARGET_PTS:
                    show_exit("PE", state["pe_pos"], strike,
                              state["pe_entry"], pe_spread,
                              "TARGET HIT", pnl_pts, pe_d, state["pe_lots"])
                    state["realised_pnl"] += round(
                        pnl_pts*LOT_SIZE*state["pe_lots"], 0)
                    state["pe_pos"]   = None
                    state["pe_entry"] = None
                    state["pe_lots"]  = 0
                    if total_pnl() >= DAILY_TARGET:
                        state["target_locked"] = True
                        print(f"\n  🎯 TARGET HIT!  ₹{total_pnl():,.0f}\n")
                elif pnl_pts <= -STOPLOSS_PTS:
                    show_exit("PE", state["pe_pos"], strike,
                              state["pe_entry"], pe_spread,
                              "STOPLOSS HIT", pnl_pts, pe_d, state["pe_lots"])
                    state["realised_pnl"] += round(
                        pnl_pts*LOT_SIZE*state["pe_lots"], 0)
                    state["pe_pos"]   = None
                    state["pe_entry"] = None
                    state["pe_lots"]  = 0

        # ── P&L SUMMARY every 10 cycles ─────────────────────────
        if cycle % 10 == 0:
            print(f"  [{ts()}] P&L  Realised:₹{state['realised_pnl']:+,.0f}  "
                  f"Unrealised:₹{state['unrealised_pnl']:+,.0f}  "
                  f"Total:₹{total_pnl():+,.0f}  "
                  f"Remaining:₹{remaining_target():,.0f}")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n  [{ts()}] Stopped.")
        print(f"  FINAL P&L: ₹{total_pnl():+,.0f} / ₹{DAILY_TARGET:,}")
        break
    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        time.sleep(REFRESH)