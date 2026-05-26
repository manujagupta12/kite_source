"""
MULTI-STRATEGY TRADING SYSTEM  —  multistrategy.py
====================================================
Run this file for ALL 7 strategies across all market conditions.

  python multistrategy.py           — normal run
  python multistrategy.py --input   — open trade input mode first

Trade logging:
  Press Ctrl+C at any time → trade input menu appears
  All trades saved to: C:\AlgoTrading\logs\trades_YYYYMMDD.csv

7 STRATEGIES:
  S1 Calendar Spread      — Sideways, low VIX
  S2 Iron Condor          — Sideways, range-bound
  S3 Short Straddle       — High IV, sideways
  S4 Momentum Breakout    — Trending markets
  S5 Delta Hedge Strangle — VIX spikes
  S6 Expiry 0DTE          — Expiry week
  S7 Ratio Spread         — Mild directional
"""

import sys, time, shutil, requests, re
import pandas as pd
import numpy as np
from datetime import datetime, date

sys.path.insert(0, r"C:\AlgoTrading\scripts")
try:
    import trade_logger as logger
except ImportError:
    print("  ERROR: trade_logger.py not found in C:\\AlgoTrading\\scripts\\")
    sys.exit(1)

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
#  SETTINGS
# ════════════════════════════════════════════════════════════════
FILEPATH        = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH        = r"C:\AlgoTrading\data\_temp_read.xls"

MARGIN_BUDGET   = 5_000_000  # ₹50 lakh — full capital deployed every day
LOT_SIZES       = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}
MARGINS         = {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}
MAX_MARGIN      = 5_000_000  # hard ceiling = full 50L

TARGET_PTS      = {"NIFTY": 4, "BANKNIFTY": 8, "FINNIFTY": 3}
SL_PTS          = {"NIFTY": 3, "BANKNIFTY": 6, "FINNIFTY": 2}
REFRESH         = 3
ANALYSIS_EVERY  = 60
IMMEDIATE_SCORE = 70

VIX_LOW = 13; VIX_MEDIUM = 16; VIX_HIGH = 19; VIX_EXTREME = 22

# ── Import dynamic position sizer ────────────────────────────────
try:
    from position_sizer import PositionSizer, MARGINS, LOT_SIZES
    _sizer_available = True
except ImportError:
    _sizer_available = False
    MARGINS   = {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}
    LOT_SIZES = {"NIFTY": 25,    "BANKNIFTY": 15,     "FINNIFTY": 40}
    print("  WARNING: position_sizer.py not found — using fixed 1 lot.")

# ════════════════════════════════════════════════════════════════
#  COLUMN MAP
# ════════════════════════════════════════════════════════════════
C_NEAR_STRIKE=0; C_FAR_STRIKE=1; C_NEAR_BID=3; C_NEAR_ASK=4; C_NEAR_LTP=5
C_FAR_VOL=7; C_NEAR_VOL=8; C_NEAR_DELTA=9; C_FAR_DELTA=11
C_FAR_VEGA=15; C_NEAR_VEGA=16; C_FAR_THETA=19; C_NEAR_THETA=20
C_STRADDLE=23; C_FAR_PREM=24; C_SEC_TYPE=29; C_SEC_STRIKE=30
C_SEC_BID=31; C_SEC_ASK=32; C_SEC_LTP=33; C_SEC_VOL=34; C_SPOT=7

# ════════════════════════════════════════════════════════════════
#  STATE
# ════════════════════════════════════════════════════════════════
state = {
    "spot": None, "vix": None, "atm": None,
    "near_exp": None, "far_exp": None,
    "regime": None, "fail": 0,
    "last_analysis": 0, "alerted": set(),
}

def ts(): return datetime.now().strftime("%H:%M:%S")
def _f(v):
    try:
        f = float(v); return np.nan if (np.isnan(f) or np.isinf(f)) else f
    except: return np.nan
def risk_icon(vix):
    if vix is None or vix < VIX_LOW: return "🟢 LOW"
    elif vix < VIX_MEDIUM: return "🟡 MEDIUM"
    elif vix < VIX_HIGH:   return "🟠 HIGH"
    elif vix < VIX_EXTREME:return "🔴 VERY HIGH"
    else:                  return "💀 EXTREME"
def grade(sc):
    if sc>=85: return "⭐⭐⭐ STRONG"
    elif sc>=70: return "⭐⭐   GOOD"
    elif sc>=50: return "⭐    MODERATE"
    else: return "     WEAK"
def lots_for(strategy="MANUAL", score=70, vix=None, regime_key=None, inst="NIFTY"):
    """Dynamic lot sizing — calls PositionSizer. Returns 1 as safe fallback."""
    if _sizer_available and _sizer:
        return max(1, _sizer.get_lots(strategy, score, vix or 15, regime_key, inst))
    return 1

# ════════════════════════════════════════════════════════════════
#  VIX
# ════════════════════════════════════════════════════════════════
_sess = requests.Session()
_sess.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json",
                       "Referer":"https://www.nseindia.com"})
def fetch_vix():
    try:
        _sess.get("https://www.nseindia.com",timeout=5)
        r = _sess.get("https://www.nseindia.com/api/allIndices",timeout=5)
        for item in r.json().get("data",[]):
            if "INDIA VIX" in str(item.get("index","")).upper():
                return round(float(item["last"]),2)
    except: pass
    return None

# ════════════════════════════════════════════════════════════════
#  DATA
# ════════════════════════════════════════════════════════════════
def safe_read():
    try:
        shutil.copy2(FILEPATH,TEMPPATH)
        return pd.read_excel(TEMPPATH,header=None,engine="xlrd")
    except PermissionError: return None
    except Exception as e:
        print(f"  [{ts()}] {e}"); return None

def read_metadata(raw):
    meta={"spot":None,"near_exp":None,"far_exp":None}
    try:
        v=float(raw.iloc[0,C_SPOT])
        if 15000<v<35000: meta["spot"]=round(v,2)
    except: pass
    today=date.today(); expiry_dates=[]
    for r in range(min(2,len(raw))):
        for c in range(len(raw.columns)):
            m=re.search(r'NIFTY(\d{1,2})([A-Z]{3})(\d{4})',str(raw.iloc[r,c]).upper())
            if m:
                try:
                    exp_dt=datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}","%d%b%Y").date()
                    if (today-exp_dt).days<60: expiry_dates.append((exp_dt,str(raw.iloc[r,c]).strip()))
                except ValueError: pass
    seen,unique=set(),[]
    for dt,label in sorted(expiry_dates):
        if label not in seen: seen.add(label); unique.append((dt,label))
    if len(unique)>=2: meta["near_exp"]=unique[0][1]; meta["far_exp"]=unique[1][1]
    elif len(unique)==1: meta["near_exp"]=unique[0][1]
    return meta

def read_main_table(raw):
    rows=[]
    for idx in range(3,len(raw)):
        row=raw.iloc[idx]
        try:
            ns=float(row.iloc[C_NEAR_STRIKE])
            if not (21000<ns<27000): continue
            rows.append({"near_strike":int(ns),"far_strike":int(float(row.iloc[C_FAR_STRIKE])),
                "near_bid":float(row.iloc[C_NEAR_BID]),"near_ask":float(row.iloc[C_NEAR_ASK]),
                "near_ltp":float(row.iloc[C_NEAR_LTP]),"near_vol":_f(row.iloc[C_NEAR_VOL]),
                "far_vol":_f(row.iloc[C_FAR_VOL]),"near_theta":_f(row.iloc[C_NEAR_THETA]),
                "far_theta":_f(row.iloc[C_FAR_THETA]),"near_vega":_f(row.iloc[C_NEAR_VEGA]),
                "far_vega":_f(row.iloc[C_FAR_VEGA]),"near_delta":_f(row.iloc[C_NEAR_DELTA]),
                "far_delta":_f(row.iloc[C_FAR_DELTA]),"straddle":_f(row.iloc[C_STRADDLE]),
                "far_prem":_f(row.iloc[C_FAR_PREM])})
        except: continue
    return pd.DataFrame(rows) if rows else None

def read_secondary_table(raw):
    ce_rows,pe_rows=[],[]
    for idx in range(len(raw)):
        row=raw.iloc[idx]
        try:
            t=str(row.iloc[C_SEC_TYPE]).strip().upper()
            if t not in ("CE","PE"): continue
            s=float(row.iloc[C_SEC_STRIKE])
            if not (21000<s<27000): continue
            entry={"strike":int(s),"bid":float(row.iloc[C_SEC_BID]),
                   "ask":float(row.iloc[C_SEC_ASK]),"vol":_f(row.iloc[C_SEC_VOL])}
            (ce_rows if t=="CE" else pe_rows).append(entry)
        except: continue
    return (pd.DataFrame(ce_rows) if ce_rows else None,
            pd.DataFrame(pe_rows) if pe_rows else None)

def detect_atm(spot,main_df):
    available=sorted(main_df["near_strike"].dropna().astype(int).unique())
    if not available: return None
    if spot and 21000<spot<27000:
        atm=int(round(spot/50.0)*50)
        return atm if atm in available else min(available,key=lambda s:abs(s-atm))
    valid=main_df.dropna(subset=["near_vol"])
    if not valid.empty: return int(valid.loc[valid["near_vol"].idxmax(),"near_strike"])
    return available[len(available)//2]

# ════════════════════════════════════════════════════════════════
#  REGIME DETECTOR
# ════════════════════════════════════════════════════════════════
def detect_regime(vix, near_exp):
    today=date.today(); dte=99
    if near_exp:
        m=re.search(r'(\d{1,2})([A-Z]{3})(\d{4})',str(near_exp).upper())
        if m:
            try:
                exp_dt=datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}","%d%b%Y").date()
                dte=(exp_dt-today).days
            except: pass

    if   vix is None or vix < VIX_MEDIUM:  name="SIDEWAYS / LOW VOL"
    elif vix < VIX_HIGH:                   name="SIDEWAYS / ELEVATED VOL"
    elif vix < VIX_EXTREME:                name="HIGH VOLATILITY"
    else:                                  name="EXTREME PANIC"

    valid=["S1 CALENDAR","S2 IRON CONDOR","S7 RATIO SPREAD"]
    if vix and vix >= VIX_MEDIUM:
        valid += ["S3 SHORT STRADDLE","S5 DELTA STRANGLE"]
    if vix and vix >= VIX_HIGH:
        valid += ["S4 MOMENTUM"]
    if dte <= 3:
        valid += ["S6 EXPIRY 0DTE"]
    return {"name":name,"vix":vix,"dte":dte,"valid":list(dict.fromkeys(valid))}

# ════════════════════════════════════════════════════════════════
#  STRATEGY SCORERS  (condensed — full logic in comments)
# ════════════════════════════════════════════════════════════════
def score_calendar(main_df, atm, vix):
    results=[]
    strikes=[s for s in sorted(main_df["near_strike"].dropna().astype(int).unique())
             if abs(s-atm)<=200]
    for strike in strikes:
        row=main_df[main_df["near_strike"]==strike]
        if row.empty: continue
        try:
            fp=float(row["far_prem"].iloc[0]); na=float(row["near_ask"].iloc[0])
            nb=float(row["near_bid"].iloc[0]); fs=int(row["far_strike"].iloc[0])
            if np.isnan(fp) or np.isnan(na): continue
            sp=round(fp-na,2)
            ft=_f(row["far_theta"].iloc[0]); nt=_f(row["near_theta"].iloc[0])
            fv=_f(row["far_vega"].iloc[0]);  nv=_f(row["near_vega"].iloc[0])
            fd=_f(row["far_delta"].iloc[0]); nd=_f(row["near_delta"].iloc[0])
            nv_=_f(row["near_vol"].iloc[0]); fv_=_f(row["far_vol"].iloc[0])
            fair=round((ft-nt)*0.5,2) if not (np.isnan(ft) or np.isnan(nt)) else 0.0
            dev=round(sp-fair,2); te=round(abs(nt)-abs(ft),4) if not (np.isnan(nt) or np.isnan(ft)) else 0
            vd=round(fv-nv,4) if not (np.isnan(fv) or np.isnan(nv)) else 0
            sc=min(25,int((abs(dev)/5.0)*25))
            sc+=min(20,int((abs(te)/0.02)*20)) if te>0 else 5
            sc+=min(15,int((abs(vd)/20.0)*15)) if vd>0 else 3
            sc+=min(15,int(((nv_ or 0)+(fv_ or 0))/500000*15))
            dn=abs(nd-fd) if not (np.isnan(nd) or np.isnan(fd)) else 1
            sc+=max(0,15-int(dn*30))
            sc+=(10 if (vix or 99)<VIX_LOW else 8 if (vix or 99)<VIX_MEDIUM else 5 if (vix or 99)<VIX_HIGH else 2)
            dirn="LONG" if dev<-1.0 else "SHORT" if dev>1.0 else "LONG"
            results.append({
                "strategy":"S1 CALENDAR","score":sc,"direction":dirn,
                "inst":"NIFTY","type":"CE","near_strike":strike,"far_strike":fs,
                "spread":sp,"fair":fair,"deviation":dev,
                "near_bid":round(nb,2),"near_ask":round(na,2),
                "far_bid":round(fp,2),"far_ask":round(fp+0.10,2),
                "sell_near_at":round(nb-0.05,2),"buy_near_at":round(na+0.05,2),
                "buy_far_at":round(fp+0.05,2),"sell_far_at":round(fp-0.05,2),
                "theta_edge":te,"vega_diff":vd,
                "reason":f"Dev:{dev:+.2f}pts Theta:{te:.4f} Vega:{vd:.4f}",
                "orders":(f"{'BUY  Far CE '+str(fs)+' @ '+str(round(fp+0.05,2)) if dirn=='LONG' else 'SELL Far CE '+str(fs)+' @ '+str(round(fp-0.05,2))}\n"
                          f"    {'SELL Near CE '+str(strike)+' @ '+str(round(nb-0.05,2)) if dirn=='LONG' else 'BUY  Near CE '+str(strike)+' @ '+str(round(na+0.05,2))}"),
            })
        except: continue
    return results

def score_iron_condor(main_df, atm, vix):
    results=[]
    for offset in [100,150,200]:
        sce_r=main_df[main_df["near_strike"]==atm+offset]
        spe_r=main_df[main_df["near_strike"]==atm-offset]
        if sce_r.empty or spe_r.empty: continue
        try:
            sce_b=float(sce_r["near_bid"].iloc[0])
            spe_b=round(float(spe_r["straddle"].iloc[0])-float(spe_r["near_bid"].iloc[0]),2)
            if spe_b<=0: continue
            net=round(sce_b+spe_b,2)
            sc=min(40,int((net/50.0)*40))+min(20,int((offset/200.0)*20))
            sc+=(15 if (vix or 99)<VIX_HIGH else 10 if (vix or 99)<VIX_EXTREME else 5)
            sc+=min(15,int((_f(sce_r["near_vol"].iloc[0]) or 0)/200000*15))+10
            be_u=atm+offset+net; be_l=atm-offset-net
            results.append({
                "strategy":"S2 IRON CONDOR","score":sc,"direction":"SHORT VOL",
                "inst":"NIFTY","type":"IC","atm":atm,
                "short_ce":atm+offset,"short_pe":atm-offset,
                "wing_ce":atm+offset+100,"wing_pe":atm-offset-100,
                "net_premium":net,"breakeven_upper":round(be_u,1),"breakeven_lower":round(be_l,1),
                "sce_sell_at":round(sce_b-0.05,2),"spe_sell_at":round(spe_b-0.05,2),
                "reason":f"Net credit {net:.2f}pts Range[{atm-offset}–{atm+offset}] BE[{be_l:.0f}–{be_u:.0f}]",
                "orders":(f"SELL CE {atm+offset} @ {round(sce_b-0.05,2)}\n"
                          f"    SELL PE {atm-offset} @ {round(spe_b-0.05,2)}\n"
                          f"    BUY  CE {atm+offset+100} @ market  [wing]\n"
                          f"    BUY  PE {atm-offset-100} @ market  [wing]"),
            })
        except: continue
    return results

def score_short_straddle(main_df, atm, vix):
    results=[]
    for strike in [atm,atm-50,atm+50]:
        row=main_df[main_df["near_strike"]==strike]
        if row.empty: continue
        try:
            st=float(row["straddle"].iloc[0]); ce_b=float(row["near_bid"].iloc[0])
            pe_b=round(st-ce_b,2)
            if pe_b<=0: continue
            tot=round(ce_b+pe_b,2); be_u=strike+tot; be_l=strike-tot
            iv_b=(25 if (vix or 0)>=VIX_HIGH else 15 if (vix or 0)>=VIX_MEDIUM else 8)
            nt=_f(row["near_theta"].iloc[0])
            sc=iv_b+min(35,int((tot/200.0)*35))+max(0,20-int(abs(strike-atm)/10))+min(10,int((abs(nt)/20.0)*10) if not np.isnan(nt) else 5)+10
            results.append({
                "strategy":"S3 SHORT STRADDLE","score":sc,"direction":"SELL BOTH",
                "inst":"NIFTY","type":"STRADDLE","strike":strike,
                "ce_bid":round(ce_b,2),"pe_bid":round(pe_b,2),"total_premium":tot,
                "be_upper":round(be_u,2),"be_lower":round(be_l,2),
                "sell_ce_at":round(ce_b-0.05,2),"sell_pe_at":round(pe_b-0.05,2),
                "reason":f"Premium {tot:.2f}pts BE[{be_l:.0f}–{be_u:.0f}] IV elevated VIX={vix}",
                "orders":(f"SELL CE {strike} @ {round(ce_b-0.05,2)}\n"
                          f"    SELL PE {strike} @ {round(pe_b-0.05,2)}\n"
                          f"    ⚠ NAKED SHORT — SL at 50% premium loss"),
                "risk_note":"⚠ NAKED SHORT — needs strict SL",
            })
        except: continue
    return results

def score_momentum(main_df, atm, vix, spot):
    results=[]
    if not spot: return results
    sa=main_df[main_df["near_strike"]>atm].head(5)
    sb=main_df[main_df["near_strike"]<atm].tail(5)
    va=(sa["near_vol"].sum() if not sa.empty else 0)
    vb=(sb["near_vol"].sum() if not sb.empty else 0)
    tot=(va+vb) or 1
    br=va/tot
    if   br>0.6: dirn,t_str,otype="BULL",atm+150,"CE"
    elif (1-br)>0.6: dirn,t_str,otype="BEAR",atm-150,"PE"
    else: return results
    row=main_df[main_df["near_strike"]==t_str]
    if row.empty: return results
    try:
        ba=float(row["near_ask"].iloc[0]); bb=float(row["near_bid"].iloc[0])
        nd=_f(row["near_delta"].iloc[0]); nv=_f(row["near_vol"].iloc[0])
        sc=min(30,int((nv or 0)/300000*30))+min(20,int(abs(nd if not np.isnan(nd) else 0)*40))
        sc+=(15 if (vix or 0)<VIX_MEDIUM else 10 if (vix or 0)<VIX_HIGH else 5)
        sc+=min(25,int(abs(br-0.5)*2*25))+10
        results.append({
            "strategy":"S4 MOMENTUM","score":sc,"direction":f"BUY {otype}",
            "inst":"NIFTY","type":otype,"strike":t_str,
            "buy_ask":round(ba,2),"buy_at":round(ba+0.05,2),
            "target_at":round(ba+TARGET_PTS["NIFTY"]*2,2),
            "sl_at":round(ba-SL_PTS["NIFTY"],2),
            "reason":f"Vol skew {'BULL' if dirn=='BULL' else 'BEAR'} {round(br*100 if dirn=='BULL' else (1-br)*100,1)}%",
            "orders":(f"BUY {otype} {t_str} @ {round(ba+0.05,2)}\n"
                      f"    TARGET @ {round(ba+TARGET_PTS['NIFTY']*2,2)}  SL @ {round(ba-SL_PTS['NIFTY'],2)}"),
            "risk_note":"Directional — exit same day",
        })
    except: pass
    return results

def score_strangle(main_df, atm, vix):
    results=[]
    if (vix or 0)<VIX_MEDIUM: return results
    offset=200 if (vix or 0)<VIX_HIGH else 300
    row=main_df[main_df["near_strike"]==atm+offset]
    if row.empty: return results
    try:
        ca=float(row["near_ask"].iloc[0]); cv=_f(row["near_vega"].iloc[0])
        st=float(row["straddle"].iloc[0]); pa=round(st-float(row["near_bid"].iloc[0]),2)*1.2
        tot=round(ca+pa,2)
        vix_sc=(30 if (vix or 0)>=VIX_EXTREME else 20 if (vix or 0)>=VIX_HIGH else 10)
        sc=vix_sc+min(25,int(abs(cv if not np.isnan(cv) else 0)/30*25))+min(20,int((1-(tot/300))*20))+25
        results.append({
            "strategy":"S5 DELTA STRANGLE","score":sc,"direction":"BUY BOTH + HEDGE",
            "inst":"NIFTY","type":"STRANGLE","ce_strike":atm+offset,"pe_strike":atm-offset,
            "ce_ask":round(ca,2),"pe_ask":round(pa,2),"total_cost":tot,
            "buy_ce_at":round(ca+0.05,2),"buy_pe_at":round(pa+0.05,2),
            "reason":f"VIX={vix} elevated — buy vol for expansion",
            "orders":(f"BUY CE {atm+offset} @ {round(ca+0.05,2)}\n"
                      f"    BUY PE {atm-offset} @ {round(pa+0.05,2)}\n"
                      f"    SELL NIFTY FUT 1 lot  [delta hedge]"),
        })
    except: pass
    return results

def score_expiry(main_df, atm, vix, near_exp):
    results=[]
    today=date.today(); dte=99
    if near_exp:
        m=re.search(r'(\d{1,2})([A-Z]{3})(\d{4})',str(near_exp).upper())
        if m:
            try:
                exp_dt=datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}","%d%b%Y").date()
                dte=(exp_dt-today).days
            except: pass
    if dte>3: return results
    row=main_df[main_df["near_strike"]==atm]
    if row.empty: return results
    try:
        cb=float(row["near_bid"].iloc[0]); st=float(row["straddle"].iloc[0])
        pb=round(st-cb,2)
        if pb<=0: return results
        tot=round(cb+pb,2); be_u=atm+tot; be_l=atm-tot
        nt=_f(row["near_theta"].iloc[0])
        sc=(50 if dte==0 else 40 if dte==1 else 30)+min(30,int((tot/100.0)*30))+min(20,int((abs(nt)/50.0)*20) if not np.isnan(nt) else 10)
        results.append({
            "strategy":"S6 EXPIRY 0DTE","score":sc,"direction":"SELL STRADDLE",
            "inst":"NIFTY","type":"EXPIRY STRADDLE","strike":atm,"dte":dte,
            "ce_bid":round(cb,2),"pe_bid":round(pb,2),"total_premium":tot,
            "be_upper":round(be_u,2),"be_lower":round(be_l,2),
            "sell_ce_at":round(cb-0.05,2),"sell_pe_at":round(pb-0.05,2),
            "reason":f"DTE={dte} Max theta decay Premium={tot:.2f}pts BE[{be_l:.0f}–{be_u:.0f}]",
            "orders":(f"SELL CE {atm} @ {round(cb-0.05,2)}\n"
                      f"    SELL PE {atm} @ {round(pb-0.05,2)}\n"
                      f"    ⚠ EXIT BY 3:20 PM"),
            "risk_note":f"DTE={dte} EXIT BY 3:20PM",
        })
    except: pass
    return results

def score_ratio(main_df, atm, vix):
    results=[]
    for dirn,otype,otm in [("BULL","CE",atm+100),("BEAR","PE",atm-100)]:
        atm_r=main_df[main_df["near_strike"]==atm]
        otm_r=main_df[main_df["near_strike"]==otm]
        if atm_r.empty or otm_r.empty: continue
        try:
            if dirn=="BULL":
                ba=float(atm_r["near_ask"].iloc[0]); sb=float(otm_r["near_bid"].iloc[0])
            else:
                ba=round(float(atm_r["straddle"].iloc[0])-float(atm_r["near_bid"].iloc[0]),2)
                sb=round(float(otm_r["straddle"].iloc[0])-float(otm_r["near_bid"].iloc[0]),2)
            nc=round(ba-2*sb,2)
            sc=min(30,int(abs(nc)/5*30)) if nc<0 else 5
            sc+=(20 if (vix or 0)<VIX_MEDIUM else 12 if (vix or 0)<VIX_HIGH else 6)
            sc+=min(20,int((_f(atm_r["near_vol"].iloc[0]) or 0)/400000*20))+25
            results.append({
                "strategy":"S7 RATIO SPREAD","score":sc,"direction":dirn,
                "inst":"NIFTY","type":otype,"atm":atm,"otm_strike":otm,
                "buy_ask":round(ba,2),"sell_bid":round(sb,2),"net_cost":nc,
                "buy_at":round(ba+0.05,2),"sell_at":round(sb-0.05,2),
                "reason":f"{dirn} ratio 1:2 net {'credit' if nc<0 else 'debit'} {abs(nc):.2f}pts",
                "orders":(f"BUY  1x {otype} {atm}  @ {round(ba+0.05,2)}\n"
                          f"    SELL 2x {otype} {otm} @ {round(sb-0.05,2)}"),
                "risk_note":f"Unlimited risk on {'down' if dirn=='BULL' else 'up'}side — use SL",
            })
        except: continue
    return results

def run_all(main_df, far_pe_df, atm, vix, spot, near_exp):
    all_opps=[]
    all_opps += score_calendar(main_df,atm,vix)
    all_opps += score_iron_condor(main_df,atm,vix)
    all_opps += score_short_straddle(main_df,atm,vix)
    all_opps += score_momentum(main_df,atm,vix,spot)
    all_opps += score_strangle(main_df,atm,vix)
    all_opps += score_expiry(main_df,atm,vix,near_exp)
    all_opps += score_ratio(main_df,atm,vix)
    all_opps.sort(key=lambda x:x["score"],reverse=True)
    return all_opps

# ════════════════════════════════════════════════════════════════
#  DISPLAY
# ════════════════════════════════════════════════════════════════
def print_analysis(opps, regime, vix, top_n=7):
    realised,open_c,total = logger.get_daily_pnl()
    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  📊 MULTI-STRATEGY ANALYSIS  |  {ts()}
║  Regime: {regime['name']}  VIX={vix} {risk_icon(vix)}  DTE={regime['dte']}
║  Valid strategies: {', '.join(regime['valid'])}
║  Scanned {len(opps)} opportunities across 7 strategies
║  Today's P&L: ₹{realised:+,.0f}  |  Open: {open_c}  |  Dynamic sizing active
╠══════════════════════════════════════════════════════════════════════╣""")

    for rank, o in enumerate(opps[:top_n], 1):
        sc   = o["score"]
        inst = o.get("inst","NIFTY")
        strat= o.get("strategy","MANUAL")
        # Dynamic lots: score + VIX + regime all factor in
        lots = lots_for(strat, sc, vix, regime.get("key"), inst)
        tp   = TARGET_PTS.get(inst,4); sl=SL_PTS.get(inst,3)
        lsz  = LOT_SIZES.get(inst,25)
        tp_inr=round(tp*lsz*lots,0); sl_inr=round(sl*lsz*lots,0)
        margin_this = lots * MARGINS.get(inst,80000)
        rn   = o.get("risk_note","")
        orders=o.get("orders","(see above)")

        if lots >= 10:   size_note = "Large"
        elif lots >= 5:  size_note = "Medium"
        elif lots >= 2:  size_note = "Small"
        else:            size_note = "Minimum"

        print(f"""║
║  RANK #{rank}  {grade(sc)}  Score:{sc}/100  {risk_icon(vix)}
║  {o['strategy']}  |  {o['direction']}  |  {o.get('reason','')}
║  LOTS: {lots} ({size_note})  Margin: ₹{margin_this:,.0f}  R:R={round(tp_inr/max(sl_inr,1),1)}:1
║  Orders:
║    {orders.replace(chr(10), chr(10)+'║    ')}
║  Target: +₹{tp_inr:,.0f}  |  Stop: -₹{sl_inr:,.0f}
║  {'⚠  '+rn if rn else ''}
║  → Ctrl+C to log this trade with your actual lots""")

    print(f"""║
║  Log file: C:\\AlgoTrading\\logs\\trades_{date.today().strftime('%Y%m%d')}.csv
╚══════════════════════════════════════════════════════════════════════╝""")

def print_alert(o, vix, regime_key=None):
    inst  = o.get("inst","NIFTY")
    strat = o.get("strategy","MANUAL")
    sc    = o.get("score",70)
    lots  = lots_for(strat, sc, vix, regime_key, inst)
    orders= o.get("orders","")
    margin_this = lots * MARGINS.get(inst,80000)

    # Show sizing breakdown for immediate alerts
    if _sizer_available and _sizer:
        _sizer.explain_lots(strat, sc, vix or 15, regime_key, inst, lots)

    print(f"""
🔔 IMMEDIATE SIGNAL  Score:{sc}/100  [{ts()}]  {risk_icon(vix)}
  {o['strategy']}  |  {o['direction']}
  {o.get('reason','')}
  LOTS: {lots}  (₹{margin_this:,.0f} margin)
  Orders:
    {orders.replace(chr(10),'    '+(chr(10)))}
  → Ctrl+C to log this trade
🔔""")

# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  MULTI-STRATEGY SYSTEM  |  {ts()}
║  7 Strategies | All market conditions | Dynamic position sizing
║  Lots vary by signal score, VIX, regime, margin, and win/loss streak
║  Press Ctrl+C at any time to log trades / update margin / check P&L
╚══════════════════════════════════════════════════════════════════════╝
  File: {FILEPATH}  |  Logs: C:\\AlgoTrading\\logs\\
""")

logger.load_today()
realised,_,total = logger.get_daily_pnl()
print(f"  Today's P&L loaded: ₹{realised:+,.0f}  ({total} trades)")

# Initialise dynamic position sizer
_sizer = None
if _sizer_available:
    _sizer = PositionSizer()
else:
    print("  Running with fixed 1 lot — add position_sizer.py for dynamic sizing.")

if "--input" in sys.argv:
    logger.interactive_input()

print(f"\n  [{ts()}] Fetching VIX...")
v=fetch_vix(); state["vix"]=v
print(f"  [{ts()}] VIX={v}\n" if v else f"  [{ts()}] VIX unavailable\n")

cycle=0; vix_countdown=0; last_analysis=0

while True:
    try:
        cycle+=1; vix_countdown+=1
        if vix_countdown>=20:
            v=fetch_vix()
            if v and v!=state["vix"]:
                print(f"\n  [{ts()}] VIX: {state['vix']} → {v}")
                state["vix"]=v
            vix_countdown=0

        raw=safe_read()
        if raw is None:
            state["fail"]+=1
            if state["fail"]%5==1: print(f"  [{ts()}] Waiting... {state['fail']}")
            time.sleep(REFRESH); continue
        state["fail"]=0

        meta=read_metadata(raw)
        if meta["spot"]:     state["spot"]    =meta["spot"]
        if meta["near_exp"]: state["near_exp"]=meta["near_exp"]
        if meta["far_exp"]:  state["far_exp"] =meta["far_exp"]

        main_df=read_main_table(raw); _,far_pe_df=read_secondary_table(raw)
        if main_df is None or main_df.empty:
            print(f"  [{ts()}] No data..."); time.sleep(REFRESH); continue

        atm=detect_atm(state["spot"],main_df)
        if atm and atm!=state["atm"]:
            state["atm"]=atm
            print(f"\n  [{ts()}] ATM:{atm}  Spot:{state['spot']}")

        vix=state["vix"]; spot=state["spot"]; strike=state["atm"]
        if not strike: time.sleep(REFRESH); continue

        # ── REGIME DETECTION every cycle ────────────────────────
        regime_full = None
        if _regime_available and _regime_engine:
            regime_full = _regime_engine.detect(
                main_df, vix, state["spot"], state["near_exp"], strike)

        # Print regime report on change
        if regime_full and _regime_engine.changed():
            print_regime_report(regime_full)
            _regime_engine.mark_printed()
            if regime_full["key"] == "R8_EXTREME_PANIC":
                print(f"\n  [{ts()}] 💀 EXTREME PANIC — All new signals suppressed.")
            elif regime_full["key"] == "R1_DEAD":
                print(f"\n  [{ts()}] 🔴 DEAD MARKET — Calendar spread edge is NEGATIVE. "
                      f"Showing safe alternatives only.")

        # Also run simple regime for display (backward compat)
        regime = detect_regime(vix, state["near_exp"])

        now=time.time()
        if (now-last_analysis)>=ANALYSIS_EVERY:
            all_opps=run_all(main_df,far_pe_df,strike,vix,spot,state["near_exp"])

            # Apply deep regime filter
            if regime_full and _regime_available:
                all_opps = filter_by_regime(all_opps, regime_full)
                mult = regime_full.get("size_mult", 1.0)
                if mult < 1.0:
                    print(f"\n  [{ts()}] ⚠  SIZE OVERRIDE: {mult*100:.0f}% "
                          f"({regime_full['name']})")

            print_analysis(all_opps, regime, vix)
            last_analysis=now

            unblocked = [o for o in all_opps if not o.get("regime_blocked", False)]
            if unblocked:
                top=unblocked[0]
                ak=f"{top['strategy']}_{top.get('near_strike',top.get('strike',''))}"
                mult = regime_full.get("size_mult",1.0) if regime_full else 1.0
                if top["score"]>=IMMEDIATE_SCORE and ak not in state["alerted"]:
                    if mult == 0.0:
                        print(f"  [{ts()}] 💀 Signal suppressed — regime blocks all entries")
                    else:
                        regime_key_for_alert = regime_full.get("key") if regime_full else None
                        print_alert(top, vix, regime_key_for_alert)
                    state["alerted"].add(ak)

        # Live tick
        row=main_df[main_df["near_strike"]==strike]
        if not row.empty:
            try:
                ce_sp=round(float(row["far_prem"].iloc[0])-float(row["near_ask"].iloc[0]),2)
                st=float(row["straddle"].iloc[0])
                realised,open_c,_=logger.get_daily_pnl()
                regime_line = regime_summary_line(regime_full) if regime_full and _regime_available else risk_icon(vix)
                print(f"  [{ts()}]  ATM:{strike}  CE:{ce_sp:+.2f}  "
                      f"Straddle:{st:.2f}  VIX:{vix}  "
                      f"P&L:₹{realised:+,.0f}  {regime_line}")
            except: pass

        if cycle%10==0:
            if _sizer_available and _sizer:
                _sizer.show_status()
            else:
                realised,open_c,_=logger.get_daily_pnl()
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
        print(f"  [{ts()}] Error: {e}"); time.sleep(REFRESH)
