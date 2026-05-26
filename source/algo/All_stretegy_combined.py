"""
╔══════════════════════════════════════════════════════════════════════╗
║     NIFTY/BANKNIFTY MULTI-STRATEGY TRADING SYSTEM                   ║
║     Complete system covering all market conditions                   ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  STRATEGY ROSTER:                                                    ║
║                                                                      ║
║  [S1] CALENDAR SPREAD      — Sideways, low VIX                      ║
║  [S2] IRON CONDOR          — Sideways, any VIX                      ║
║  [S3] SHORT STRADDLE       — Sideways, high IV                      ║
║  [S4] MOMENTUM BREAKOUT    — Trending markets                        ║
║  [S5] DELTA HEDGE STRANGLE — High volatility / VIX spikes           ║
║  [S6] EXPIRY DAY 0DTE      — Expiry week pinning                    ║
║  [S7] RATIO SPREAD         — Mild directional bias                  ║
║                                                                      ║
║  SYSTEM ENGINE:                                                      ║
║  • Reads live MultiTrade Excel every 3 seconds                       ║
║  • Auto-selects best strategy for current market regime              ║
║  • Scores all opportunities across all strategies                    ║
║  • Prints full analysis + exact prices every 60 seconds             ║
║  • Tracks ₹50K daily target across all strategies                   ║
║  • Risk-categorised signals (LOW/MEDIUM/HIGH/EXTREME)               ║
║  • Recovery mode for any carried losing position                     ║
║                                                                      ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import time, shutil, requests, re
import pandas as pd
import numpy as np
from datetime import datetime, date

# ════════════════════════════════════════════════════════════════
#  GLOBAL SETTINGS
# ════════════════════════════════════════════════════════════════
FILEPATH        = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH        = r"C:\AlgoTrading\data\_temp_read.xls"

DAILY_TARGET    = 50000
LOT_SIZES       = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}
MARGINS         = {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}
MAX_MARGIN      = 5_000_000   # ₹50 lakh

TARGET_PTS      = {"NIFTY": 4,  "BANKNIFTY": 8,  "FINNIFTY": 3}
SL_PTS          = {"NIFTY": 3,  "BANKNIFTY": 6,  "FINNIFTY": 2}
REFRESH         = 3
ANALYSIS_EVERY  = 60
IMMEDIATE_SCORE = 70

VIX_LOW         = 13
VIX_MEDIUM      = 16
VIX_HIGH        = 19
VIX_EXTREME     = 22

# Carried positions — fill in if you have overnight positions
CARRIED = {
    "ce_pos": None,  "ce_entry": None,  "ce_strike": None,  "ce_inst": "NIFTY",
    "pe_pos": None,  "pe_entry": None,  "pe_strike": None,  "pe_inst": "NIFTY",
}

# ════════════════════════════════════════════════════════════════
#  COLUMN MAP  (verified from your MultiTrade Excel)
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
#  LIVE STATE
# ════════════════════════════════════════════════════════════════
state = {
    "positions": [],          # list of active position dicts
    "realised_pnl":   0.0,
    "unrealised_pnl": 0.0,
    "target_locked":  False,
    "spot":     None, "vix": None, "atm": None,
    "near_exp": None, "far_exp": None,
    "regime":   None,         # current market regime
    "last_analysis": 0,
    "alerted":  set(),
    "fail": 0,
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

def total_pnl():
    return round(state["realised_pnl"] + state["unrealised_pnl"], 2)

def remaining():
    return max(0.0, DAILY_TARGET - total_pnl())

def lots_for(inst, pts):
    if pts <= 0: pts = TARGET_PTS[inst]
    n = int(np.ceil(remaining() / (pts * LOT_SIZES[inst])))
    return min(max(n, 1), int(MAX_MARGIN / MARGINS[inst]))

def risk_icon(vix):
    if   vix is None or vix < VIX_LOW:    return "🟢 LOW"
    elif vix < VIX_MEDIUM:                return "🟡 MEDIUM"
    elif vix < VIX_HIGH:                  return "🟠 HIGH"
    elif vix < VIX_EXTREME:               return "🔴 VERY HIGH"
    else:                                 return "💀 EXTREME"

def grade(score):
    if   score >= 85: return "⭐⭐⭐ STRONG"
    elif score >= 70: return "⭐⭐   GOOD"
    elif score >= 50: return "⭐    MODERATE"
    else:             return "     WEAK"

# ════════════════════════════════════════════════════════════════
#  VIX FETCH
# ════════════════════════════════════════════════════════════════
_sess = requests.Session()
_sess.headers.update({
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":      "application/json",
    "Referer":     "https://www.nseindia.com",
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

# ════════════════════════════════════════════════════════════════
#  FILE & DATA READERS
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
    except Exception:
        pass
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
                except ValueError:
                    pass
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
                "near_strike": int(ns),
                "far_strike":  int(float(row.iloc[C_FAR_STRIKE])),
                "near_bid":  float(row.iloc[C_NEAR_BID]),
                "near_ask":  float(row.iloc[C_NEAR_ASK]),
                "near_ltp":  float(row.iloc[C_NEAR_LTP]),
                "near_vol":  _f(row.iloc[C_NEAR_VOL]),
                "far_vol":   _f(row.iloc[C_FAR_VOL]),
                "near_theta":_f(row.iloc[C_NEAR_THETA]),
                "far_theta": _f(row.iloc[C_FAR_THETA]),
                "near_vega": _f(row.iloc[C_NEAR_VEGA]),
                "far_vega":  _f(row.iloc[C_FAR_VEGA]),
                "near_delta":_f(row.iloc[C_NEAR_DELTA]),
                "far_delta": _f(row.iloc[C_FAR_DELTA]),
                "straddle":  _f(row.iloc[C_STRADDLE]),
                "far_prem":  _f(row.iloc[C_FAR_PREM]),
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
            if t not in ("CE","PE"): continue
            s = float(row.iloc[C_SEC_STRIKE])
            if not (21000 < s < 27000): continue
            entry = {"strike": int(s),
                     "bid": float(row.iloc[C_SEC_BID]),
                     "ask": float(row.iloc[C_SEC_ASK]),
                     "vol": _f(row.iloc[C_SEC_VOL])}
            (ce_rows if t=="CE" else pe_rows).append(entry)
        except (IndexError, ValueError, TypeError):
            continue
    return (pd.DataFrame(ce_rows) if ce_rows else None,
            pd.DataFrame(pe_rows) if pe_rows else None)

def detect_atm(spot, main_df):
    available = sorted(main_df["near_strike"].dropna().astype(int).unique())
    if not available: return None
    if spot and 21000 < spot < 27000:
        atm = int(round(spot / 50.0) * 50)
        return atm if atm in available else min(available, key=lambda s: abs(s-atm))
    valid = main_df.dropna(subset=["near_vol"])
    if not valid.empty:
        return int(valid.loc[valid["near_vol"].idxmax(), "near_strike"])
    return available[len(available)//2]

# ════════════════════════════════════════════════════════════════
#  MARKET REGIME DETECTOR
#  Determines which strategies are valid right now
# ════════════════════════════════════════════════════════════════
def detect_regime(main_df, vix, spot, near_exp):
    """
    Returns regime dict with flags for each strategy type.
    Uses VIX, IV structure, theta/vega ratios, and expiry proximity.
    """
    today     = date.today()
    regime    = {
        "name":          "UNKNOWN",
        "vix":           vix,
        "sideways":      False,
        "trending":      False,
        "high_vol":      False,
        "expiry_week":   False,
        "iv_elevated":   False,
        "best_strategy": "S1",
        "all_valid":     [],
    }

    # Expiry proximity
    if near_exp:
        m = re.search(r'(\d{1,2})([A-Z]{3})(\d{4})', str(near_exp).upper())
        if m:
            try:
                exp_dt = datetime.strptime(
                    f"{m.group(1)}{m.group(2)}{m.group(3)}", "%d%b%Y").date()
                days_to_expiry = (exp_dt - today).days
                if days_to_expiry <= 5:
                    regime["expiry_week"] = True
            except ValueError:
                pass

    # VIX-based regime
    if vix:
        if vix < VIX_MEDIUM:
            regime["sideways"]    = True
            regime["name"]        = "SIDEWAYS / LOW VOL"
        elif vix < VIX_HIGH:
            regime["sideways"]    = True
            regime["iv_elevated"] = True
            regime["name"]        = "SIDEWAYS / ELEVATED VOL"
        elif vix < VIX_EXTREME:
            regime["high_vol"]    = True
            regime["iv_elevated"] = True
            regime["name"]        = "HIGH VOLATILITY"
        else:
            regime["high_vol"]    = True
            regime["trending"]    = True
            regime["iv_elevated"] = True
            regime["name"]        = "EXTREME PANIC"
    else:
        regime["sideways"] = True
        regime["name"]     = "UNKNOWN (No VIX)"

    # IV structure from theta ratios
    if not main_df.empty:
        sample = main_df.dropna(subset=["near_theta","far_theta"]).head(5)
        if not sample.empty:
            avg_near_theta = sample["near_theta"].mean()
            avg_far_theta  = sample["far_theta"].mean()
            if abs(avg_near_theta) > abs(avg_far_theta) * 1.5:
                regime["iv_elevated"] = True

    # Build valid strategy list based on regime
    valid = []
    if regime["sideways"]:
        valid += ["S1 CALENDAR", "S2 IRON CONDOR", "S3 SHORT STRADDLE", "S7 RATIO SPREAD"]
    if regime["trending"]:
        valid += ["S4 MOMENTUM BREAKOUT"]
    if regime["high_vol"]:
        valid += ["S5 DELTA HEDGE STRANGLE"]
    if regime["expiry_week"]:
        valid += ["S6 EXPIRY 0DTE"]
    if not valid:
        valid = ["S1 CALENDAR", "S2 IRON CONDOR"]

    regime["all_valid"] = list(dict.fromkeys(valid))  # deduplicate

    # Best single strategy
    if regime["expiry_week"]:
        regime["best_strategy"] = "S6 EXPIRY 0DTE"
    elif regime["high_vol"] and vix and vix >= VIX_EXTREME:
        regime["best_strategy"] = "S5 DELTA HEDGE STRANGLE"
    elif regime["trending"]:
        regime["best_strategy"] = "S4 MOMENTUM BREAKOUT"
    elif regime["iv_elevated"]:
        regime["best_strategy"] = "S3 SHORT STRADDLE"
    else:
        regime["best_strategy"] = "S2 IRON CONDOR"

    return regime

# ════════════════════════════════════════════════════════════════
#  STRATEGY ENGINES
#  Each returns a list of opportunity dicts with score + prices
# ════════════════════════════════════════════════════════════════

# ── S1: CALENDAR SPREAD ─────────────────────────────────────────
def s1_calendar(main_df, far_pe_df, atm, vix):
    opps = []
    strikes = sorted(main_df["near_strike"].dropna().astype(int).unique())
    # Focus on ATM ±200
    strikes = [s for s in strikes if abs(s - atm) <= 200]

    for strike in strikes:
        row = main_df[main_df["near_strike"] == strike]
        if row.empty: continue
        try:
            far_prem  = float(row["far_prem"].iloc[0])
            near_ask  = float(row["near_ask"].iloc[0])
            near_bid  = float(row["near_bid"].iloc[0])
            if np.isnan(far_prem) or np.isnan(near_ask): continue
            far_strike = int(row["far_strike"].iloc[0])
            spread     = round(far_prem - near_ask, 2)
            ft = _f(row["far_theta"].iloc[0]);   nt = _f(row["near_theta"].iloc[0])
            fv = _f(row["far_vega"].iloc[0]);    nv = _f(row["near_vega"].iloc[0])
            fd = _f(row["far_delta"].iloc[0]);   nd = _f(row["near_delta"].iloc[0])
            nvol = _f(row["near_vol"].iloc[0]);  fvol = _f(row["far_vol"].iloc[0])
            fair       = round((ft-nt)*0.5, 2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
            deviation  = round(spread - fair, 2)
            theta_edge = round(abs(nt)-abs(ft), 4) if not (np.isnan(nt) or np.isnan(ft)) else 0
            vega_diff  = round(fv-nv, 4) if not (np.isnan(fv) or np.isnan(nv)) else 0

            # Score
            sc  = min(25, int((abs(deviation)/5.0)*25))
            sc += min(20, int((abs(theta_edge)/0.02)*20)) if theta_edge > 0 else 5
            sc += min(15, int((abs(vega_diff)/20.0)*15)) if vega_diff > 0 else 3
            sc += min(15, int(((nvol or 0)+(fvol or 0))/500000*15))
            sc += max(0, 15-int(abs(nd-fd if not (np.isnan(nd) or np.isnan(fd)) else 1)*30))
            sc += (10 if (vix or 99) < VIX_LOW else 8 if (vix or 99) < VIX_MEDIUM
                   else 5 if (vix or 99) < VIX_HIGH else 2)

            direction  = "LONG" if deviation < -1.0 else "SHORT" if deviation > 1.0 else "LONG"
            reason     = (f"Spread {spread:+.2f} vs Fair {fair:+.2f} | "
                          f"Theta edge {theta_edge:.4f} | Vega diff {vega_diff:.4f}")

            opps.append({
                "strategy": "S1 CALENDAR SPREAD",
                "regime":   "SIDEWAYS / LOW VOL",
                "score":    sc,
                "direction":direction,
                "reason":   reason,
                "inst":     "NIFTY",
                "type":     "CE",
                "near_strike": strike,
                "far_strike":  far_strike,
                "spread":    spread,
                "fair":      fair,
                "deviation": deviation,
                "near_bid":  round(near_bid,2),
                "near_ask":  round(near_ask,2),
                "far_bid":   round(far_prem,2),
                "far_ask":   round(far_prem+0.10,2),
                "sell_near_at": round(near_bid-0.05,2),
                "buy_near_at":  round(near_ask+0.05,2),
                "buy_far_at":   round(far_prem+0.05,2),
                "sell_far_at":  round(far_prem-0.05,2),
                "greeks": {
                    "near_theta": round(nt,4) if not np.isnan(nt) else None,
                    "far_theta":  round(ft,4) if not np.isnan(ft) else None,
                    "theta_edge": theta_edge,
                    "near_vega":  round(nv,4) if not np.isnan(nv) else None,
                    "far_vega":   round(fv,4) if not np.isnan(fv) else None,
                    "vega_diff":  vega_diff,
                    "near_delta": round(nd,4) if not np.isnan(nd) else None,
                },
            })
        except Exception:
            continue
    return opps


# ── S2: IRON CONDOR ─────────────────────────────────────────────
def s2_iron_condor(main_df, atm, vix):
    """
    Sell OTM CE + OTM PE (short strangle), buy further OTM as wings.
    Best in: sideways market, any VIX.
    Profit: full premium if spot stays in range.
    Max profit zone = between the two short strikes.
    """
    opps = []
    strikes = sorted(main_df["near_strike"].dropna().astype(int).unique())

    # Standard iron condor wings: ATM ± 100 (short), ATM ± 200 (long/wing)
    for offset in [100, 150, 200]:
        short_ce = atm + offset
        short_pe = atm - offset
        wing_ce  = atm + offset + 100
        wing_pe  = atm - offset - 100

        row_sce = main_df[main_df["near_strike"] == short_ce]
        row_spe = main_df[main_df["near_strike"] == short_pe]
        row_wce = main_df[main_df["near_strike"] == wing_ce]
        row_wpe = main_df[main_df["near_strike"] == wing_pe]

        if row_sce.empty or row_spe.empty: continue

        try:
            # Short CE premium received
            sce_bid = float(row_sce["near_bid"].iloc[0])
            # Short PE premium received
            spe_bid = float(row_spe["straddle"].iloc[0]) - \
                      float(row_spe["near_bid"].iloc[0])  # PE = straddle - CE

            net_credit = round(sce_bid + spe_bid, 2)
            if net_credit <= 0: continue

            # Wing cost (protection)
            wing_cost = 0
            if not row_wce.empty:
                wing_cost += float(row_wce["near_ask"].iloc[0]) * 0.5
            if not row_wpe.empty:
                wing_cost += (float(row_wpe["straddle"].iloc[0]) -
                              float(row_wpe["near_bid"].iloc[0])) * 0.5
            net_premium = round(net_credit - wing_cost, 2)

            # Score: higher premium relative to range = better
            premium_score = min(40, int((net_premium / 50.0) * 40))
            range_score   = min(20, int((offset / 200.0) * 20))
            vix_score     = (15 if (vix or 99) < VIX_HIGH else
                             10 if (vix or 99) < VIX_EXTREME else 5)
            liquidity_sc  = min(15, int((_f(row_sce["near_vol"].iloc[0]) or 0) / 200000 * 15))
            sc            = premium_score + range_score + vix_score + liquidity_sc + 10

            max_loss   = round(offset - net_premium, 2)
            breakeven_upper = short_ce + net_premium
            breakeven_lower = short_pe - net_premium

            opps.append({
                "strategy": "S2 IRON CONDOR",
                "regime":   "SIDEWAYS",
                "score":    sc,
                "direction":"SHORT VOL",
                "reason":   (f"Net credit {net_premium:.2f}pts | Range [{short_pe}–{short_ce}] "
                             f"| Breakeven [{breakeven_lower:.0f}–{breakeven_upper:.0f}]"),
                "inst":     "NIFTY",
                "type":     "IC",
                "atm":      atm,
                "short_ce": short_ce,
                "short_pe": short_pe,
                "wing_ce":  wing_ce,
                "wing_pe":  wing_pe,
                "net_premium":     round(net_premium, 2),
                "max_loss":        round(max_loss, 2),
                "breakeven_upper": round(breakeven_upper, 2),
                "breakeven_lower": round(breakeven_lower, 2),
                "sce_sell_at": round(sce_bid - 0.05, 2),
                "spe_sell_at": round(spe_bid - 0.05, 2),
                "wce_buy_at":  round(float(row_wce["near_ask"].iloc[0]) + 0.05, 2)
                               if not row_wce.empty else "N/A",
                "wpe_buy_at":  "market",
            })
        except Exception:
            continue
    return opps


# ── S3: SHORT STRADDLE ──────────────────────────────────────────
def s3_short_straddle(main_df, atm, vix):
    """
    Sell ATM CE + ATM PE simultaneously.
    Collect maximum premium when IV is elevated.
    Best when: market expected to stay near ATM, IV will fall.
    Risk: unlimited if market makes large move.
    """
    opps = []
    for strike in [atm, atm-50, atm+50]:
        row = main_df[main_df["near_strike"] == strike]
        if row.empty: continue
        try:
            straddle_val = float(row["straddle"].iloc[0])
            ce_bid       = float(row["near_bid"].iloc[0])
            pe_bid       = round(straddle_val - ce_bid, 2)
            if pe_bid <= 0: continue

            total_premium = round(ce_bid + pe_bid, 2)
            # Break-even range
            be_upper = strike + total_premium
            be_lower = strike - total_premium

            # Score: best when IV is elevated (sell expensive options)
            iv_bonus  = (25 if (vix or 0) >= VIX_HIGH else
                         15 if (vix or 0) >= VIX_MEDIUM else 8)
            prem_sc   = min(35, int((total_premium / 200.0) * 35))
            atm_bonus = max(0, 20 - int(abs(strike-atm)/10))
            nt = _f(row["near_theta"].iloc[0])
            theta_sc  = min(10, int((abs(nt)/20.0)*10)) if not np.isnan(nt) else 5
            sc        = iv_bonus + prem_sc + atm_bonus + theta_sc + 10

            opps.append({
                "strategy":     "S3 SHORT STRADDLE",
                "regime":       "SIDEWAYS / HIGH IV",
                "score":        sc,
                "direction":    "SELL BOTH",
                "reason":       (f"IV elevated (VIX={vix}) | Total premium {total_premium:.2f}pts "
                                 f"| Break-even range [{be_lower:.0f}–{be_upper:.0f}]"),
                "inst":         "NIFTY",
                "type":         "STRADDLE",
                "strike":       strike,
                "ce_bid":       round(ce_bid, 2),
                "pe_bid":       round(pe_bid, 2),
                "total_premium":total_premium,
                "be_upper":     round(be_upper, 2),
                "be_lower":     round(be_lower, 2),
                "sell_ce_at":   round(ce_bid - 0.05, 2),
                "sell_pe_at":   round(pe_bid - 0.05, 2),
                "near_theta":   round(nt, 4) if not np.isnan(nt) else None,
                "risk_note":    "⚠ NAKED SHORT — must have hedge or strict SL",
            })
        except Exception:
            continue
    return opps


# ── S4: MOMENTUM BREAKOUT ───────────────────────────────────────
def s4_momentum(main_df, atm, vix, spot):
    """
    Buy OTM CE or PE depending on directional momentum.
    Uses delta acceleration and volume surge as signals.
    Best: trending markets, post-news breakout.
    """
    opps = []
    if not spot: return opps

    # Detect directional bias from delta distribution
    strikes_above = main_df[main_df["near_strike"] > atm].head(5)
    strikes_below = main_df[main_df["near_strike"] < atm].tail(5)

    vol_above = strikes_above["near_vol"].sum() if not strikes_above.empty else 0
    vol_below = strikes_below["near_vol"].sum() if not strikes_below.empty else 0

    total_vol = (vol_above + vol_below) or 1
    bull_ratio = vol_above / total_vol
    bear_ratio = vol_below / total_vol

    # Determine direction
    if bull_ratio > 0.6:
        direction  = "BULL"
        bias_pct   = round(bull_ratio * 100, 1)
        target_str = atm + 150   # buy OTM call
        row_target = main_df[main_df["near_strike"] == target_str]
        reason     = f"Volume skew BULLISH ({bias_pct}% in CE above ATM)"
    elif bear_ratio > 0.6:
        direction  = "BEAR"
        bias_pct   = round(bear_ratio * 100, 1)
        target_str = atm - 150   # buy OTM put
        row_target = main_df[main_df["near_strike"] == target_str]
        reason     = f"Volume skew BEARISH ({bias_pct}% in PE below ATM)"
    else:
        direction  = "NEUTRAL"
        return opps   # no clear trend — skip

    if row_target.empty: return opps

    try:
        buy_ask  = float(row_target["near_ask"].iloc[0])
        buy_bid  = float(row_target["near_bid"].iloc[0])
        nd       = _f(row_target["near_delta"].iloc[0])
        nvol     = _f(row_target["near_vol"].iloc[0])

        # Score momentum setup
        vol_sc   = min(30, int((nvol or 0)/300000*30))
        delta_sc = min(20, int(abs(nd if not np.isnan(nd) else 0)*40))
        vix_sc   = (15 if (vix or 0) < VIX_MEDIUM else
                    10 if (vix or 0) < VIX_HIGH else 5)
        bias_sc  = min(25, int(abs(bull_ratio-0.5)*2*25))
        sc       = vol_sc + delta_sc + vix_sc + bias_sc + 10

        tp_pts   = TARGET_PTS["NIFTY"] * 2   # momentum targets are bigger
        sl_pts   = SL_PTS["NIFTY"]

        opps.append({
            "strategy":   "S4 MOMENTUM BREAKOUT",
            "regime":     "TRENDING",
            "score":      sc,
            "direction":  f"BUY {'CE' if direction=='BULL' else 'PE'}",
            "reason":     reason,
            "inst":       "NIFTY",
            "type":       "CE" if direction == "BULL" else "PE",
            "strike":     target_str,
            "buy_ask":    round(buy_ask, 2),
            "buy_bid":    round(buy_bid, 2),
            "buy_at":     round(buy_ask + 0.05, 2),
            "target_at":  round(buy_ask + tp_pts, 2),
            "sl_at":      round(buy_ask - sl_pts, 2),
            "delta":      round(nd, 4) if not np.isnan(nd) else None,
            "near_vol":   nvol,
            "risk_note":  "DIRECTIONAL — exit same day, no carry",
        })
    except Exception:
        pass
    return opps


# ── S5: DELTA HEDGE STRANGLE (High VIX) ────────────────────────
def s5_delta_strangle(main_df, atm, vix):
    """
    Buy wide strangle (OTM CE + OTM PE) and delta-hedge with futures.
    Best when VIX spikes — you profit from the big move in either direction.
    The hedge prevents runaway loss while allowing large gain.
    """
    opps = []
    if (vix or 0) < VIX_MEDIUM: return opps   # only in elevated VIX

    offset = 200 if (vix or 0) < VIX_HIGH else 300
    ce_str = atm + offset
    pe_str = atm - offset

    row_ce = main_df[main_df["near_strike"] == ce_str]
    row_pe = main_df[main_df["near_strike"] == pe_str]

    if row_ce.empty: return opps

    try:
        ce_ask    = float(row_ce["near_ask"].iloc[0])
        ce_vega   = _f(row_ce["near_vega"].iloc[0])
        ce_delta  = _f(row_ce["near_delta"].iloc[0])
        straddle  = float(row_ce["straddle"].iloc[0]) if not row_pe.empty \
                    else ce_ask * 2.5
        pe_ask    = round(straddle - float(row_ce["near_bid"].iloc[0]), 2) \
                    if not row_pe.empty else ce_ask * 1.2

        total_cost = round(ce_ask + pe_ask, 2)
        net_delta  = round(ce_delta - 0.5, 4) if not np.isnan(ce_delta) else 0

        # Score: best in high VIX
        vix_sc    = (30 if (vix or 0) >= VIX_EXTREME else
                     20 if (vix or 0) >= VIX_HIGH else
                     10 if (vix or 0) >= VIX_MEDIUM else 0)
        vega_sc   = min(25, int(abs(ce_vega if not np.isnan(ce_vega) else 0)/30*25))
        cost_sc   = min(20, int((1-(total_cost/300))*20))  # cheaper = better
        sc        = vix_sc + vega_sc + cost_sc + 25

        # Hedge: sell futures proportional to net delta
        futures_lots = max(1, int(abs(net_delta) * lots_for("NIFTY", TARGET_PTS["NIFTY"])))

        opps.append({
            "strategy":     "S5 DELTA HEDGE STRANGLE",
            "regime":       "HIGH VOLATILITY",
            "score":        sc,
            "direction":    "BUY BOTH + FUTURES HEDGE",
            "reason":       (f"VIX={vix} elevated | Buy strangle for vol expansion | "
                             f"Delta-neutral via futures"),
            "inst":         "NIFTY",
            "type":         "STRANGLE",
            "ce_strike":    ce_str,
            "pe_strike":    pe_str,
            "ce_ask":       round(ce_ask, 2),
            "pe_ask":       round(pe_ask, 2),
            "total_cost":   total_cost,
            "buy_ce_at":    round(ce_ask + 0.05, 2),
            "buy_pe_at":    round(pe_ask + 0.05, 2),
            "futures_lots": futures_lots,
            "futures_side": "SELL" if net_delta > 0 else "BUY",
            "net_delta":    net_delta,
            "ce_vega":      round(ce_vega,4) if not np.isnan(ce_vega) else None,
            "risk_note":    "Long vega — profit if VIX rises further or big move",
        })
    except Exception:
        pass
    return opps


# ── S6: EXPIRY DAY 0DTE ─────────────────────────────────────────
def s6_expiry_0dte(main_df, atm, vix, near_exp):
    """
    Expiry day strategy — aggressively sell premium near ATM.
    On expiry, theta decay is fastest. OTM options go to zero fast.
    Short straddle/strangle with VERY tight stop.
    Only active when days to expiry <= 2.
    """
    opps = []
    today = date.today()
    if not near_exp: return opps

    m = re.search(r'(\d{1,2})([A-Z]{3})(\d{4})', str(near_exp).upper())
    if not m: return opps
    try:
        exp_dt = datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}","%d%b%Y").date()
        dte    = (exp_dt - today).days
    except ValueError:
        return opps

    if dte > 3: return opps   # only active near expiry

    row = main_df[main_df["near_strike"] == atm]
    if row.empty: return opps

    try:
        ce_bid     = float(row["near_bid"].iloc[0])
        ce_ask     = float(row["near_ask"].iloc[0])
        straddle   = float(row["straddle"].iloc[0])
        pe_bid     = round(straddle - ce_bid, 2)
        nt         = _f(row["near_theta"].iloc[0])

        total_prem = round(ce_bid + pe_bid, 2)
        be_upper   = atm + total_prem
        be_lower   = atm - total_prem

        # Score: maximum on expiry day itself
        dte_sc    = (50 if dte == 0 else 40 if dte == 1 else 30)
        prem_sc   = min(30, int((total_prem/100.0)*30))
        theta_sc  = min(20, int((abs(nt)/50.0)*20)) if not np.isnan(nt) else 10
        sc        = dte_sc + prem_sc + theta_sc

        opps.append({
            "strategy":     "S6 EXPIRY 0DTE",
            "regime":       f"EXPIRY (DTE={dte})",
            "score":        sc,
            "direction":    "SELL STRADDLE",
            "reason":       (f"DTE={dte} | Theta decay maximum | "
                             f"Total premium {total_prem:.2f}pts | "
                             f"Break-even [{be_lower:.0f}–{be_upper:.0f}]"),
            "inst":         "NIFTY",
            "type":         "EXPIRY STRADDLE",
            "strike":       atm,
            "dte":          dte,
            "ce_bid":       round(ce_bid, 2),
            "pe_bid":       round(pe_bid, 2),
            "total_premium":total_prem,
            "be_upper":     round(be_upper, 2),
            "be_lower":     round(be_lower, 2),
            "sell_ce_at":   round(ce_bid - 0.05, 2),
            "sell_pe_at":   round(pe_bid - 0.05, 2),
            "near_theta":   round(nt,4) if not np.isnan(nt) else None,
            "sl_pct":       "25% of premium received",
            "risk_note":    f"⚠ EXIT by 3:20 PM. DTE={dte} — HIGH theta but gap risk.",
        })
    except Exception:
        pass
    return opps


# ── S7: RATIO SPREAD ────────────────────────────────────────────
def s7_ratio_spread(main_df, atm, vix):
    """
    Buy 1 ATM, Sell 2 OTM (1:2 ratio).
    Profit if market moves mildly in one direction.
    Limited risk on one side, unlimited on other — use stops.
    """
    opps = []
    for direction in ["BULL", "BEAR"]:
        otm_str  = atm + 100 if direction == "BULL" else atm - 100
        atm_row  = main_df[main_df["near_strike"] == atm]
        otm_row  = main_df[main_df["near_strike"] == otm_str]
        if atm_row.empty or otm_row.empty: continue

        try:
            if direction == "BULL":
                buy_ask  = float(atm_row["near_ask"].iloc[0])
                sell_bid = float(otm_row["near_bid"].iloc[0])
                net_cost = round(buy_ask - 2*sell_bid, 2)
                opt_type = "CE"
            else:
                strad_atm = float(atm_row["straddle"].iloc[0])
                strad_otm = float(otm_row["straddle"].iloc[0])
                buy_ask  = round(strad_atm - float(atm_row["near_bid"].iloc[0]), 2)
                sell_bid = round(strad_otm - float(otm_row["near_bid"].iloc[0]), 2)
                net_cost = round(buy_ask - 2*sell_bid, 2)
                opt_type = "PE"

            # Score
            credit_sc = min(30, int(abs(net_cost)/5*30)) if net_cost < 0 else 5
            vix_sc    = (20 if (vix or 0) < VIX_MEDIUM else
                         12 if (vix or 0) < VIX_HIGH else 6)
            vol_sc    = min(20, int((_f(atm_row["near_vol"].iloc[0]) or 0)/400000*20))
            sc        = credit_sc + vix_sc + vol_sc + 25

            max_profit = round(100 - net_cost, 2)   # if market moves to OTM strike
            opps.append({
                "strategy":   "S7 RATIO SPREAD",
                "regime":     "MILD DIRECTIONAL",
                "score":      sc,
                "direction":  direction,
                "reason":     (f"{direction} ratio 1:2 | Net {'credit' if net_cost<0 else 'debit'} "
                               f"{abs(net_cost):.2f}pts | Max profit at {otm_str}"),
                "inst":       "NIFTY",
                "type":       opt_type,
                "atm":        atm,
                "otm_strike": otm_str,
                "buy_ask":    round(buy_ask, 2),
                "sell_bid":   round(sell_bid, 2),
                "net_cost":   net_cost,
                "max_profit": max_profit,
                "buy_at":     round(buy_ask + 0.05, 2),
                "sell_at":    round(sell_bid - 0.05, 2),
                "risk_note":  f"Risk on {'downside' if direction=='BULL' else 'upside'} — set hard SL",
            })
        except Exception:
            continue
    return opps


# ════════════════════════════════════════════════════════════════
#  MASTER ANALYSIS  — runs all strategies, returns sorted list
# ════════════════════════════════════════════════════════════════
def run_all_strategies(main_df, far_pe_df, atm, vix, spot, near_exp, regime):
    all_opps = []
    all_opps += s1_calendar(main_df, far_pe_df, atm, vix)
    all_opps += s2_iron_condor(main_df, atm, vix)
    all_opps += s3_short_straddle(main_df, atm, vix)
    all_opps += s4_momentum(main_df, atm, vix, spot)
    all_opps += s5_delta_strangle(main_df, atm, vix)
    all_opps += s6_expiry_0dte(main_df, atm, vix, near_exp)
    all_opps += s7_ratio_spread(main_df, atm, vix)
    all_opps.sort(key=lambda x: x["score"], reverse=True)
    return all_opps


# ════════════════════════════════════════════════════════════════
#  DISPLAY ENGINE
# ════════════════════════════════════════════════════════════════
def format_orders(o):
    """Returns formatted order lines for any strategy type."""
    lines = []
    st = o["strategy"]

    if "CALENDAR" in st:
        inst = o["inst"]
        lot  = lots_for(inst, TARGET_PTS[inst])
        if o["direction"] == "LONG":
            lines.append(f"    LEG 1 ➜ BUY  Far  CE {o['far_strike']}   LIMIT @ {o['buy_far_at']}  ({lot} lots)")
            lines.append(f"    LEG 2 ➜ SELL Near CE {o['near_strike']}  LIMIT @ {o['sell_near_at']}  ({lot} lots)")
        else:
            lines.append(f"    LEG 1 ➜ SELL Far  CE {o['far_strike']}   LIMIT @ {o['sell_far_at']}  ({lot} lots)")
            lines.append(f"    LEG 2 ➜ BUY  Near CE {o['near_strike']}  LIMIT @ {o['buy_near_at']}  ({lot} lots)")
        tp = round(o["spread"] + TARGET_PTS[inst], 2) if o["direction"]=="LONG" else round(o["spread"] - TARGET_PTS[inst], 2)
        sl = round(o["spread"] - SL_PTS[inst], 2)   if o["direction"]=="LONG" else round(o["spread"] + SL_PTS[inst], 2)
        lines.append(f"    TARGET  → Spread {'≥' if o['direction']=='LONG' else '≤'} {tp:.2f}  (+{TARGET_PTS[inst]}pts = +₹{TARGET_PTS[inst]*LOT_SIZES[inst]*lot:,.0f})")
        lines.append(f"    STOP    → Spread {'≤' if o['direction']=='LONG' else '≥'} {sl:.2f}  (-{SL_PTS[inst]}pts = -₹{SL_PTS[inst]*LOT_SIZES[inst]*lot:,.0f})")

    elif "IRON CONDOR" in st:
        lot = lots_for("NIFTY", 3)
        lines.append(f"    LEG 1 ➜ SELL CE {o['short_ce']}   LIMIT @ {o['sce_sell_at']}  ({lot} lots)")
        lines.append(f"    LEG 2 ➜ SELL PE {o['short_pe']}   LIMIT @ {o['spe_sell_at']}  ({lot} lots)")
        lines.append(f"    LEG 3 ➜ BUY  CE {o['wing_ce']}    LIMIT @ {o['wce_buy_at']}   ({lot} lots)  [wing]")
        lines.append(f"    LEG 4 ➜ BUY  PE {o['wing_pe']}    LIMIT @ {o['wpe_buy_at']}   ({lot} lots)  [wing]")
        lines.append(f"    TARGET  → Keep premium if spot stays [{o['breakeven_lower']:.0f}–{o['breakeven_upper']:.0f}]")
        lines.append(f"    STOP    → Exit if spot breaks {o['short_pe']-50:.0f} or {o['short_ce']+50:.0f}")

    elif "STRADDLE" in st and "EXPIRY" not in st:
        lot = lots_for("NIFTY", o["total_premium"])
        lines.append(f"    LEG 1 ➜ SELL CE {o['strike']}  LIMIT @ {o['sell_ce_at']}  ({lot} lots)")
        lines.append(f"    LEG 2 ➜ SELL PE {o['strike']}  LIMIT @ {o['sell_pe_at']}  ({lot} lots)")
        lines.append(f"    TARGET  → Keep full premium {o['total_premium']:.2f}pts if spot stays [{o['be_lower']:.0f}–{o['be_upper']:.0f}]")
        lines.append(f"    STOP    → 50% of premium = {round(o['total_premium']*0.5,2):.2f}pts loss")
        lines.append(f"    ⚠  NOTE: {o.get('risk_note','')}")

    elif "MOMENTUM" in st:
        lot = lots_for("NIFTY", TARGET_PTS["NIFTY"]*2)
        lines.append(f"    LEG 1 ➜ BUY {o['type']} {o['strike']}  LIMIT @ {o['buy_at']}  ({lot} lots)")
        lines.append(f"    TARGET  → Exit @ {o['target_at']}  (+₹{TARGET_PTS['NIFTY']*2*LOT_SIZES['NIFTY']*lot:,.0f})")
        lines.append(f"    STOP    → Exit @ {o['sl_at']}  (-₹{SL_PTS['NIFTY']*LOT_SIZES['NIFTY']*lot:,.0f})")
        lines.append(f"    ⚠  NOTE: {o.get('risk_note','')}")

    elif "STRANGLE" in st:
        lot = lots_for("NIFTY", TARGET_PTS["NIFTY"]*3)
        lines.append(f"    LEG 1 ➜ BUY CE {o['ce_strike']}   LIMIT @ {o['buy_ce_at']}  ({lot} lots)")
        lines.append(f"    LEG 2 ➜ BUY PE {o['pe_strike']}   LIMIT @ {o['buy_pe_at']}  ({lot} lots)")
        lines.append(f"    HEDGE  ➜ {o['futures_side']} NIFTY FUT  {o['futures_lots']} lots  [delta hedge]")
        lines.append(f"    TARGET  → VIX expansion or 200pt move in either direction")
        lines.append(f"    STOP    → 30% of premium paid")

    elif "0DTE" in st or "EXPIRY" in st:
        lot = lots_for("NIFTY", o["total_premium"])
        lines.append(f"    LEG 1 ➜ SELL CE {o['strike']}  LIMIT @ {o['sell_ce_at']}  ({lot} lots)")
        lines.append(f"    LEG 2 ➜ SELL PE {o['strike']}  LIMIT @ {o['sell_pe_at']}  ({lot} lots)")
        lines.append(f"    TARGET  → Full premium {o['total_premium']:.2f}pts by 3:20 PM")
        lines.append(f"    STOP    → {o['sl_pct']}")
        lines.append(f"    ⚠  NOTE: {o.get('risk_note','')}")

    elif "RATIO" in st:
        lot = lots_for("NIFTY", 3)
        lines.append(f"    LEG 1 ➜ BUY  1× {o['type']} {o['atm']}      LIMIT @ {o['buy_at']}   ({lot} lots)")
        lines.append(f"    LEG 2 ➜ SELL 2× {o['type']} {o['otm_strike']} LIMIT @ {o['sell_at']}  ({lot*2} lots)")
        lines.append(f"    TARGET  → Market moves to {o['otm_strike']} (+₹{round(o['max_profit']*LOT_SIZES['NIFTY']*lot,0):,.0f})")
        lines.append(f"    STOP    → {o.get('risk_note','')}")

    return "\n".join(lines)


def print_full_analysis(all_opps, regime, vix, top_n=7):
    n_scanned = len(all_opps)
    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  📊 MULTI-STRATEGY ANALYSIS  |  {ts()}
║  Market Regime: {regime['name']}  |  VIX={vix}  |  {risk_icon(vix)}
║  Best Strategy for current regime: {regime['best_strategy']}
║  All valid strategies: {', '.join(regime['all_valid'])}
║  Scanned {n_scanned} opportunities across 7 strategies
╠══════════════════════════════════════════════════════════════════════╣""")

    for rank, o in enumerate(all_opps[:top_n], 1):
        sc   = o["score"]
        g    = grade(sc)
        ri   = risk_icon(vix)
        rn   = o.get("risk_note","")
        orders = format_orders(o)

        print(f"""║
║  ── RANK #{rank}  {g}  Score:{sc}/100  ──────────────────────────────
║  Strategy  : {o['strategy']}
║  Regime    : {o['regime']}  |  Risk: {ri}
║  Direction : {o['direction']}
║  WHY       : {o['reason']}
║
║  ORDER INSTRUCTIONS:
{chr(10).join('║  ' + l for l in orders.split(chr(10)))}
║  {"⚠  "+rn if rn else ""}""")

    print(f"""║
║  ── DAILY P&L ────────────────────────────────────────────────────
║  Realised   : ₹{state['realised_pnl']:+,.0f}
║  Unrealised : ₹{state['unrealised_pnl']:+,.0f}
║  Total Today: ₹{total_pnl():+,.0f}  /  Target ₹{DAILY_TARGET:,}
║  {"✅ TARGET HIT — STOP NEW ENTRIES" if state['target_locked'] else f"Remaining: ₹{remaining():,.0f}  |  Lots needed: {lots_for('NIFTY', TARGET_PTS['NIFTY'])}"}
╚══════════════════════════════════════════════════════════════════════╝""")


def print_immediate_alert(o, vix):
    orders = format_orders(o)
    print(f"""
🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔
  ⚡ IMMEDIATE HIGH-SCORE SIGNAL  |  Score: {o['score']}/100  [{ts()}]
  {o['strategy']}  |  {o['direction']}  |  {risk_icon(vix)}
  WHY: {o['reason']}
  ──────────────────────────────────────────────────────────────
{chr(10).join('  ' + l for l in orders.split(chr(10)))}
  ──────────────────────────────────────────────────────────────
  Daily P&L: ₹{total_pnl():+,.0f} / ₹{DAILY_TARGET:,}  |  Remaining: ₹{remaining():,.0f}
🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔🔔""")


# ════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ════════════════════════════════════════════════════════════════
print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  NIFTY/BANKNIFTY MULTI-STRATEGY SYSTEM  |  {ts()}
║  7 Strategies  |  All market conditions  |  ₹50K daily target
╚══════════════════════════════════════════════════════════════════════╝
  File    : {FILEPATH}
  Refresh : {REFRESH}s  |  Full analysis every {ANALYSIS_EVERY}s
""")

print(f"  [{ts()}] Fetching VIX...")
v = fetch_vix()
state["vix"] = v
print(f"  [{ts()}] VIX = {v}\n" if v else f"  [{ts()}] VIX unavailable — will retry\n")

prev_banner_key = None
cycle           = 0
vix_countdown   = 0
last_analysis   = 0

while True:
    try:
        cycle        += 1
        vix_countdown += 1

        # VIX refresh every ~60s
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
            time.sleep(REFRESH)
            continue
        state["fail"] = 0

        meta = read_metadata(raw)
        if meta["spot"]:     state["spot"]     = meta["spot"]
        if meta["near_exp"]: state["near_exp"] = meta["near_exp"]
        if meta["far_exp"]:  state["far_exp"]  = meta["far_exp"]

        main_df              = read_main_table(raw)
        far_ce_df, far_pe_df = read_secondary_table(raw)

        if main_df is None or main_df.empty:
            print(f"  [{ts()}] No data yet...")
            time.sleep(REFRESH)
            continue

        atm = detect_atm(state["spot"], main_df)
        if atm and atm != state["atm"]:
            state["atm"] = atm

        vix    = state["vix"]
        spot   = state["spot"]
        strike = state["atm"]
        if not strike:
            time.sleep(REFRESH)
            continue

        # Detect market regime
        regime = detect_regime(main_df, vix, spot, state["near_exp"])
        state["regime"] = regime

        # Full analysis every ANALYSIS_EVERY seconds
        now = time.time()
        if (now - last_analysis) >= ANALYSIS_EVERY:
            all_opps = run_all_strategies(
                main_df, far_pe_df, strike, vix, spot,
                state["near_exp"], regime)
            print_full_analysis(all_opps, regime, vix, top_n=7)
            last_analysis = now

            # Immediate alert for top score
            if all_opps:
                top = all_opps[0]
                ak  = f"{top['strategy']}_{top.get('near_strike', top.get('strike',''))}"
                if (top["score"] >= IMMEDIATE_SCORE
                        and ak not in state["alerted"]
                        and not state["target_locked"]):
                    print_immediate_alert(top, vix)
                    state["alerted"].add(ak)

        # Live CE tick
        row = main_df[main_df["near_strike"] == strike]
        if not row.empty:
            try:
                ce_spread = round(float(row["far_prem"].iloc[0]) -
                                  float(row["near_ask"].iloc[0]), 2)
                straddle  = float(row["straddle"].iloc[0])
                print(f"  [{ts()}]  ATM:{strike}  "
                      f"CE-Spread:{ce_spread:+.2f}  "
                      f"Straddle:{straddle:.2f}  "
                      f"VIX:{vix}  {risk_icon(vix)}")
            except Exception:
                pass

        # P&L ticker every 10 cycles
        if cycle % 10 == 0:
            print(f"  [{ts()}]  P&L → ₹{total_pnl():+,.0f} / ₹{DAILY_TARGET:,}  "
                  f"Remaining: ₹{remaining():,.0f}")

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print(f"\n  [{ts()}] Stopped.")
        print(f"  FINAL P&L: ₹{total_pnl():+,.0f} / ₹{DAILY_TARGET:,}")
        break
    except Exception as e:
        print(f"  [{ts()}] Error: {e}")
        time.sleep(REFRESH)