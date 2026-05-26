"""
EQUITY INDIA ALGO  —  equity_india.py
======================================
Directional single-side trades on NIFTY and BANKNIFTY
using REAL NSE data (via stock-nse-india server) + Delta Exchange orderbook.

DATA SOURCES (priority order):
  1. NSE India (via nse_connector.py → stock-nse-india server on :3000)
     → Real spot price, live option chain, PCR, max pain, OI levels
     → Accurate support/resistance from actual open interest
  2. Delta Exchange (NIFTYUSD / BANKNIFTYUSD perpetuals)
     → Live orderbook for best bid/ask entry
     → Funding rate signals
  3. Fallback: public NSE endpoints (no server required, slower)

STRATEGIES:
  E1  EMA Crossover        — EMA9 vs EMA21 with volume confirmation
  E2  VWAP Reversion       — Price >0.35% from VWAP, fade the move
  E3  Opening Range Break  — 15-min range breakout (9:30–12:00 only)
  E4  ADX Trend Follow     — ADX >22 + pullback to EMA21
  E5  Funding Rate Arb     — Extreme funding → fade crowded side
  E6  Gap Fill             — Morning gap vs previous close
  E7  S/R Levels           — NSE OI-based support and resistance

SETUP:
  1. Start NSE server:   npx stock-nse-india@latest   (port 3000)
  2. Set .env:           DELTA_API_KEY, DELTA_API_SECRET, DELTA_ENV=india
  3. Run:                python equity_india.py
                         python equity_india.py --instrument BANKNIFTY
"""

import os, sys, time, hmac, hashlib, argparse
import requests
import numpy as np
import pandas as pd
from datetime import datetime, date

# ── Environment ───────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=r"D:\AlgoTrading\source\.env")
except ImportError:
    pass

DELTA_API_KEY    = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
DELTA_ENV        = os.environ.get("DELTA_ENV", "india")

BASE_URLS = {
    "india":         "https://api.india.delta.exchange",
    "global":        "https://api.delta.exchange",
    "testnet-india": "https://cdn-ind.testnet.deltaex.org",
}
DELTA_BASE = BASE_URLS.get(DELTA_ENV, BASE_URLS["india"])

# ── NSE connector ─────────────────────────────────────────────
sys.path.insert(0, r"C:\AlgoTrading\scripts")
try:
    from nse_connector import NseConnector, NseSignalEnhancer
    _nse      = NseConnector()
    _enhancer = NseSignalEnhancer(_nse)
    _nse_ok   = _nse._connected
    if _nse_ok:
        print("  [NSE] ✅ Real NSE data active")
    else:
        print("  [NSE] ⚠  Server not running — start with: npx stock-nse-india@latest")
except ImportError:
    _nse_ok   = False
    _nse      = None
    _enhancer = None
    print("  [NSE] nse_connector.py not found in C:\\AlgoTrading\\scripts\\")

# ════════════════════════════════════════════════════════════════
#  SETTINGS
# ════════════════════════════════════════════════════════════════
INSTRUMENTS = {
    "NIFTY":     { "delta_sym":"NIFTYUSD",     "nse_sym":"NIFTY",     "margin":80000,  "tick":0.5,  "lot":25 },
    "BANKNIFTY": { "delta_sym":"BANKNIFTYUSD",  "nse_sym":"BANKNIFTY", "margin":90000,  "tick":1.0,  "lot":15 },
}

TARGET_PCT   = 0.008   # 0.8% take-profit
STOPLOSS_PCT = 0.004   # 0.4% stop-loss
REFRESH      = 5       # seconds between ticks
ANALYSIS_EVERY = 60    # seconds between full scans
IMMEDIATE_SC   = 68    # alert threshold

# ════════════════════════════════════════════════════════════════
#  POSITION SIZER
# ════════════════════════════════════════════════════════════════
class EquitySizer:
    def __init__(self):
        self.margin    = 0.0
        self.deployed  = 0.0
        self.pnl_today = 0.0
        self.trades    = []
        self._ask()

    def _ask(self):
        print("\n" + "="*60)
        print("  EQUITY INDIA ALGO — Margin Setup")
        print("="*60)
        while True:
            try:
                raw = input("  Available margin today (₹): ").strip().upper().replace(",","")
                if   raw.endswith("CR"): val = float(raw[:-2])*10_000_000
                elif raw.endswith("L"):  val = float(raw[:-1])*100_000
                else:                    val = float(raw)
                if val <= 0: raise ValueError
                self.margin = val
                print(f"\n  ✅ Margin: ₹{val:,.0f}")
                for n,c in INSTRUMENTS.items():
                    mx = int(val/c["margin"])
                    print(f"     {n:<12} max {mx} lots (₹{c['margin']:,}/lot)")
                print()
                break
            except (ValueError, KeyboardInterrupt):
                print("  Try: 50000  or  5L  or  1CR")

    def update(self):
        self._ask()

    def get_lots(self, instrument, score):
        cfg   = INSTRUMENTS.get(instrument, list(INSTRUMENTS.values())[0])
        free  = max(0, self.margin - self.deployed)
        if free < cfg["margin"]: return 0
        cap   = max(1, int(self.margin*0.40/cfg["margin"]))
        smult = 1.0 if score>=85 else 0.8 if score>=75 else 0.6 if score>=65 else 0.4 if score>=55 else 0.2
        # Loss streak protection
        recent = self.trades[-5:]
        losses = sum(1 for t in recent if t<0)
        lmult  = 0.4 if losses>=3 else 0.6 if losses>=2 else 0.8 if losses>=1 else 1.0
        return min(max(1, round(cap*smult*lmult)), int(free/cfg["margin"]))

    def deploy(self, lots, instrument):
        self.deployed += lots * INSTRUMENTS.get(instrument,{}).get("margin",80000)

    def release(self, lots, instrument):
        self.deployed = max(0, self.deployed - lots*INSTRUMENTS.get(instrument,{}).get("margin",80000))

    def record(self, pnl_inr, lots, instrument):
        self.pnl_today += pnl_inr
        self.trades.append(pnl_inr)
        self.release(lots, instrument)

    def status(self):
        wins = sum(1 for t in self.trades if t>0)
        wr   = round(wins/len(self.trades)*100,1) if self.trades else 0
        print(f"""
  ┌──────────────────────────────────────────┐
  │  SIZER  |  {ts()}
  │  Margin   : ₹{self.margin:>12,.0f}
  │  Deployed : ₹{self.deployed:>12,.0f}
  │  Free     : ₹{max(0,self.margin-self.deployed):>12,.0f}
  │  P&L Today: ₹{self.pnl_today:>+12,.0f}
  │  Trades   : {len(self.trades)}  Win Rate: {wr}%
  └──────────────────────────────────────────┘""")


# ════════════════════════════════════════════════════════════════
#  DELTA CLIENT  (orderbook + candles)
# ════════════════════════════════════════════════════════════════
class DeltaClient:
    def __init__(self):
        self._s = requests.Session()
        self._s.headers.update({"User-Agent":"equity-india-1.0","Accept":"application/json"})

    def _sign(self, method, path, query="", body=""):
        ts_ = str(int(time.time()))
        pre = method+ts_+path+query+(body or "")
        sig = hmac.new(DELTA_API_SECRET.encode(), pre.encode(), __import__("hashlib").sha256).hexdigest()
        return ts_, sig

    def _get(self, path, params=None, auth=False):
        url = DELTA_BASE+path
        hdrs = {}
        if auth and DELTA_API_KEY:
            q = "&".join(f"{k}={v}" for k,v in (params or {}).items())
            ts_, sig = self._sign("GET", path, q)
            hdrs = {"api-key":DELTA_API_KEY,"timestamp":ts_,"signature":sig,"Content-Type":"application/json"}
        try:
            r = self._s.get(url, params=params, headers=hdrs, timeout=8)
            d = r.json()
            return d.get("result",d) if d.get("success") else d
        except Exception:
            return {}

    def get_ticker(self, symbol):
        d = self._get(f"/v2/tickers/{symbol}")
        if not d: return None
        try:
            return {
                "symbol":     symbol,
                "mark":       float(d.get("mark_price",0) or 0),
                "last":       float(d.get("close",0) or 0),
                "bid":        float(d.get("bid",0) or 0),
                "ask":        float(d.get("ask",0) or 0),
                "volume":     float(d.get("volume",0) or 0),
                "funding":    float(d.get("funding_rate",0) or 0),
                "change_pct": float(d.get("price_change_percent",0) or 0),
                "high24":     float(d.get("high",0) or 0),
                "low24":      float(d.get("low",0) or 0),
            }
        except Exception:
            return None

    def get_orderbook(self, symbol, depth=10):
        d = self._get(f"/v2/l2orderbook/{symbol}")
        if not d: return None
        try:
            bids = [(float(b["price"]),float(b["size"])) for b in d.get("buy",[])[:depth]]
            asks = [(float(a["price"]),float(a["size"])) for a in d.get("sell",[])[:depth]]
            if not bids or not asks: return None
            bb,bbs = bids[0]; ba,bas = asks[0]
            spread = ba-bb; mid = round((bb+ba)/2,4)
            spct   = spread/mid*100 if mid else 0
            return {
                "bids":bids,"asks":asks,"best_bid":bb,"best_bid_sz":bbs,
                "best_ask":ba,"best_ask_sz":bas,"spread":round(spread,4),
                "spread_pct":round(spct,4),"mid":mid,
                "bid_depth":sum(s for _,s in bids[:5]),
                "ask_depth":sum(s for _,s in asks[:5]),
                "is_liquid":spct<0.15 and bbs>=3,
            }
        except Exception:
            return None

    def get_candles(self, symbol, resolution="5m", days_back=2):
        end   = int(time.time())
        start = end - days_back*86400
        d = self._get("/v2/history/candles", params={"symbol":symbol,"resolution":resolution,"start":start,"end":end})
        if not d or not isinstance(d,list): return pd.DataFrame()
        df = pd.DataFrame(d, columns=["time","open","high","low","close","volume"])
        df["time"] = pd.to_datetime(df["time"],unit="s")
        df = df.sort_values("time").reset_index(drop=True)
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna()


# ════════════════════════════════════════════════════════════════
#  INDICATORS
# ════════════════════════════════════════════════════════════════
def ema(s,n):  return s.ewm(span=n,adjust=False).mean()
def vwap(df):
    tp = (df["high"]+df["low"]+df["close"])/3
    return (tp*df["volume"]).cumsum()/df["volume"].cumsum()
def adx(df,n=14):
    hl=df["high"]-df["low"]; hpc=(df["high"]-df["close"].shift()).abs()
    lpc=(df["low"]-df["close"].shift()).abs()
    tr=pd.concat([hl,hpc,lpc],axis=1).max(axis=1).rolling(n).mean()
    dmu=df["high"].diff(); dmd=-df["low"].diff()
    pdm=dmu.where((dmu>dmd)&(dmu>0),0).rolling(n).mean()
    ndm=dmd.where((dmd>dmu)&(dmd>0),0).rolling(n).mean()
    pdi=100*pdm/tr.replace(0,np.nan); ndi=100*ndm/tr.replace(0,np.nan)
    dx=(100*(pdi-ndi).abs()/(pdi+ndi).replace(0,np.nan)).rolling(n).mean()
    return dx,pdi,ndi


# ════════════════════════════════════════════════════════════════
#  STRATEGY FUNCTIONS
# ════════════════════════════════════════════════════════════════
def e1_ema(df, ticker, ob):
    if len(df)<26: return None
    d=df.copy(); d["e9"]=ema(d["close"],9); d["e21"]=ema(d["close"],21)
    d["vm"]=d["volume"].rolling(10).mean()
    l=d.iloc[-1]; p=d.iloc[-2]; price=ticker["mark"]
    up   = prev_e9_below = p["e9"]<=p["e21"] and l["e9"]>l["e21"]
    down = p["e9"]>=p["e21"] and l["e9"]<l["e21"]
    if not (up or down): return None
    vc=l["volume"]>l["vm"]*1.2; gap=abs(l["e9"]-l["e21"])/price*100
    sc=min(35,int(gap*500))+( 20 if vc else 5)+min(15,int(ticker.get("volume",0)/10000*15))+(10 if ob and ob["is_liquid"] else 3)
    return {"strategy":"E1 EMA CROSSOVER","direction":"LONG" if up else "SHORT","score":min(sc+5,100),
            "reason":f"EMA9 {'above' if up else 'below'} EMA21 | Gap {gap:.3f}% | Vol {'OK' if vc else 'weak'}"}

def e2_vwap(df, ticker, ob):
    if len(df)<5: return None
    d=df.copy(); d["vw"]=vwap(d); vwp=d.iloc[-1]["vw"]; price=ticker["mark"]
    dev=(price-vwp)/vwp*100 if vwp else 0
    if abs(dev)<0.35: return None
    dirn="LONG" if dev<0 else "SHORT"
    sc=min(40,int(abs(dev)*50))+( 15 if ob and ob["is_liquid"] else 5)+(10 if abs(dev)>0.6 else 5)
    return {"strategy":"E2 VWAP REVERSION","direction":dirn,"score":min(sc+10,100),
            "reason":f"Price {abs(dev):.3f}% {'below' if dirn=='LONG' else 'above'} VWAP {vwp:.2f}"}

def e3_orb(df, ticker, ob):
    h=datetime.now().hour
    if not (9<=h<12): return None
    if len(df)<6: return None
    oc=df.head(3); orh=oc["high"].max(); orl=oc["low"].min(); price=ticker["mark"]
    bl=price>orh*1.001; blo=price<orl*0.999
    if not (bl or blo): return None
    dirn="LONG" if bl else "SHORT"
    bpct=abs(price-(orh if bl else orl))/price*100
    sc=min(35,int(bpct*300))+(20 if (orh-orl)/price>0.003 else 10)+(15 if ob and ob["is_liquid"] else 5)
    return {"strategy":"E3 ORB BREAKOUT","direction":dirn,"score":min(sc+5,100),
            "reason":f"Broke {'15-min high' if dirn=='LONG' else '15-min low'} | Range {orh-orl:.2f} | +{bpct:.3f}%"}

def e4_trend(df, ticker, ob):
    if len(df)<28: return None
    d=df.copy(); adx_v,pdi,ndi=adx(d); d["adx"]=adx_v; d["e21"]=ema(d["close"],21)
    l=d.iloc[-1]; price=ticker["mark"]
    if l["adx"]<22: return None
    up=l["e_pdi"]=float(pdi.iloc[-1]); nd=float(ndi.iloc[-1])
    up=(up>nd and l["adx"]>22); dn=(nd>float(pdi.iloc[-1]) and l["adx"]>22)
    if not (up or dn): return None
    near=abs(price-l["e21"])/price<0.003
    dirn="LONG" if up else "SHORT"
    sc=min(30,int(l["adx"]))+(20 if near else 8)+(10 if ob and ob["is_liquid"] else 3)
    return {"strategy":"E4 TREND FOLLOW","direction":dirn,"score":min(sc+5,100),
            "reason":f"ADX {l['adx']:.1f} | {'Near EMA21 pullback' if near else 'Trend momentum'}"}

def e5_funding(ticker):
    f=ticker.get("funding",0)
    if abs(f)<0.0003: return None
    dirn="SHORT" if f>0 else "LONG"
    sc=min(60,int(abs(f)*100_000))+(20 if abs(f)>0.001 else 10)
    return {"strategy":"E5 FUNDING ARB","direction":dirn,"score":min(sc,100),
            "reason":f"Funding {f*100:.4f}% | {'Longs paying→short' if f>0 else 'Shorts paying→long'}"}

def e6_gap(df, ticker):
    h=datetime.now().hour; m=datetime.now().minute
    if not (9<=h<11 or (h==11 and m<=30)): return None
    if len(df)<3: return None
    pc=float(df.iloc[-3]["close"]); co=float(df.iloc[-1]["open"]); price=ticker["mark"]
    if pc<=0 or co<=0: return None
    gap=(co-pc)/pc*100
    if abs(gap)<0.4: return None
    dirn="SHORT" if gap>0 else "LONG"
    sc=min(40,int(abs(gap)*40))+(20 if abs(gap)>0.8 else 10)+10
    return {"strategy":"E6 GAP FILL","direction":dirn,"score":min(sc,100),
            "reason":f"Gap {'UP' if gap>0 else 'DOWN'} {abs(gap):.2f}% | Prev {pc:.2f}→Open {co:.2f}"}

def e7_sr(df, ticker, ob, nse_support=None, nse_resist=None):
    """
    E7: Support/Resistance — enhanced by NSE OI data when available.
    NSE option chain OI walls give much more accurate S/R than candle history.
    """
    if len(df)<10: return None
    price=ticker["mark"]; h24=ticker.get("high24",price); l24=ticker.get("low24",price)

    # Use NSE OI-based levels if available (much more accurate)
    support    = nse_support  or l24
    resistance = nse_resist   or h24
    near_sup   = abs(price-support)   / price < 0.004 if support    else False
    near_res   = abs(price-resistance)/ price < 0.004 if resistance else False
    if not (near_sup or near_res): return None

    lv=df.iloc[-1]["volume"]; av=df["volume"].rolling(10).mean().iloc[-1]
    vs=lv>av*1.3; dirn="LONG" if near_sup else "SHORT"
    src="NSE OI" if (nse_support or nse_resist) else "24h price"
    sc=40+(20 if vs else 5)+(15 if ob and ob["is_liquid"] else 5)+10
    return {"strategy":"E7 S/R LEVEL","direction":dirn,"score":min(sc,100),
            "reason":f"Price near {'support' if near_sup else 'resistance'} {support if near_sup else resistance:.0f} [{src}] | Vol {'surge' if vs else 'normal'}"}


# ════════════════════════════════════════════════════════════════
#  BEST BID LOGIC
# ════════════════════════════════════════════════════════════════
def best_entry(direction, ob, ticker):
    if not ob:
        return round(ticker["mark"],2), "MARK (no OB)"
    bb=ob["best_bid"]; ba=ob["best_ask"]; bbs=ob["best_bid_sz"]; bas=ob["best_ask_sz"]
    spct=ob["spread_pct"]; mid=ob["mid"]
    inst=ticker.get("symbol","NIFTYUSD"); tick=0.5
    if direction=="LONG":
        if spct<0.05: return mid, f"MID (tight {spct:.3f}%)"
        if bbs<3:     return ba,  f"LIFT ASK (thin {bbs} lots)"
        return round(bb+tick,4),  f"BID+1tick (depth {bbs})"
    else:
        if spct<0.05: return mid, f"MID (tight {spct:.3f}%)"
        if bas<3:     return bb,  f"HIT BID (thin {bas} lots)"
        return round(ba-tick,4),  f"ASK-1tick (depth {bas})"


# ════════════════════════════════════════════════════════════════
#  FULL SCANNER — uses NSE primary, Delta for orderbook
# ════════════════════════════════════════════════════════════════
def scan(instrument, dc):
    cfg      = INSTRUMENTS[instrument]
    delta_sym= cfg["delta_sym"]
    nse_sym  = cfg["nse_sym"]

    # ── Step 1: Get real NSE spot and OI levels ──────────────
    nse_spot   = None
    nse_support= None
    nse_resist = None
    nse_pcr    = None
    nse_max_pain=None

    if _nse_ok and _nse:
        try:
            idx = _nse.get_banknifty() if "BANK" in instrument else _nse.get_nifty()
            if idx:
                nse_spot = idx["last"]
            oc_data = _nse.get_option_chain(nse_sym)
            if oc_data:
                nse_support  = oc_data["top_pe_oi_strike"]
                nse_resist   = oc_data["top_ce_oi_strike"]
                nse_pcr      = oc_data["pcr"]
                nse_max_pain = oc_data["max_pain"]
            print(f"  [NSE] {nse_sym}: spot={nse_spot:.2f if nse_spot else 'N/A'}  "
                  f"PCR={nse_pcr}  Support={nse_support}  Resist={nse_resist}")
        except Exception as e:
            print(f"  [NSE] Error: {e}")

    # ── Step 2: Delta Exchange orderbook ─────────────────────
    ticker = dc.get_ticker(delta_sym)
    if not ticker or ticker["mark"]==0:
        print(f"  [Delta] No ticker for {delta_sym}")
        return []

    # Inject NSE spot as the reference price
    ticker["nse_spot"] = nse_spot or ticker["mark"]
    ob  = dc.get_orderbook(delta_sym)
    df  = dc.get_candles(delta_sym, resolution="5m", days_back=2)

    # ── Step 3: Run all 7 strategies ─────────────────────────
    signals = []
    for fn in [e1_ema, e2_vwap, e3_orb, e6_gap]:
        try:
            r = fn(df, ticker, ob)
            if r: signals.append(r)
        except Exception: pass
    try:
        r = e5_funding(ticker)
        if r: signals.append(r)
    except Exception: pass
    try:
        r = e7_sr(df, ticker, ob, nse_support, nse_resist)
        if r: signals.append(r)
    except Exception: pass

    # ── Step 4: Enrich each signal ────────────────────────────
    for s in signals:
        entry, etype = best_entry(s["direction"], ob, ticker)
        dirn = s["direction"]
        tp   = round(entry*(1+TARGET_PCT) if dirn=="LONG" else entry*(1-TARGET_PCT), 2)
        sl   = round(entry*(1-STOPLOSS_PCT) if dirn=="LONG" else entry*(1+STOPLOSS_PCT), 2)
        rr   = round(abs(tp-entry)/abs(entry-sl),2) if entry!=sl else 0
        pnl_per_lot = round(abs(tp-entry)*cfg["lot"],0)

        s.update({
            "instrument":    instrument,
            "delta_symbol":  delta_sym,
            "nse_symbol":    nse_sym,
            "mark_price":    ticker["mark"],
            "nse_spot":      nse_spot or ticker["mark"],
            "nse_available": bool(nse_spot),
            "bid":           ticker["bid"],
            "ask":           ticker["ask"],
            "change_pct":    ticker["change_pct"],
            "funding_rate":  ticker["funding"],
            "entry_price":   entry,
            "entry_type":    etype,
            "target":        tp,
            "stop_loss":     sl,
            "rr_ratio":      rr,
            "pnl_per_lot":   pnl_per_lot,
            "spread_pct":    ob["spread_pct"] if ob else 0,
            "is_liquid":     ob["is_liquid"]  if ob else False,
            "nse_support":   nse_support,
            "nse_resistance":nse_resist,
            "pcr":           nse_pcr,
            "max_pain":      nse_max_pain,
            "nse_enhanced":  bool(nse_spot),
            "timestamp":     ts(),
        })

        # Apply NSE signal enhancement (score adjustment)
        if _enhancer and _nse_ok:
            try:
                s = _enhancer.enhance_equity_signal(s, nse_sym)
            except Exception:
                pass

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


# ════════════════════════════════════════════════════════════════
#  DISPLAY
# ════════════════════════════════════════════════════════════════
def ts(): return datetime.now().strftime("%H:%M:%S")

def grade(sc):
    if sc>=85: return "⭐⭐⭐ STRONG"
    if sc>=70: return "⭐⭐   GOOD"
    if sc>=55: return "⭐    MODERATE"
    return          "     WEAK"

def print_signal(s, sizer):
    inst  = s["instrument"]
    dirn  = s["direction"]
    lots  = sizer.get_lots(inst, s["score"])
    cfg   = INSTRUMENTS.get(inst,{})
    m_used= lots * cfg.get("margin",80000)
    tp_inr= round(s["pnl_per_lot"]*lots,0)
    sl_inr= round(abs(s["entry_price"]-s["stop_loss"])*cfg.get("lot",25)*lots,0)
    nse_line = f"NSE Spot: {s['nse_spot']:.2f}  PCR: {s.get('pcr','N/A')}  MaxPain: {s.get('max_pain','N/A')}" if s.get("nse_available") else "NSE: offline"
    sr_line  = f"Support: {s.get('nse_support','N/A')}  Resistance: {s.get('nse_resistance','N/A')}" if s.get("nse_available") else ""

    print(f"""
┌──────────────────────────────────────────────────────────────────┐
│  {'⭐ ' if s['score']>=80 else ''}{grade(s['score'])}  Score: {s['score']}/100  [{ts()}]
│  {s['strategy']}  |  {inst}  |  {'▲' if dirn=='LONG' else '▼'} {dirn}
│  {s['reason']}
├──────────────────────────────────────────────────────────────────┤
│  NSE DATA  {"(✅ real-time)" if s.get('nse_available') else "(⚠ offline)"}
│    {nse_line}
│    {sr_line}
├──────────────────────────────────────────────────────────────────┤
│  DELTA EXCHANGE ORDERBOOK
│    Mark: {s['mark_price']:.2f}   Bid: {s['bid']:.2f}   Ask: {s['ask']:.2f}
│    Spread: {s['spread_pct']:.4f}%   {'✅ Liquid' if s.get('is_liquid') else '⚠  Thin book'}
│    Funding: {s['funding_rate']*100:.4f}%   24h Change: {s['change_pct']:+.2f}%
├──────────────────────────────────────────────────────────────────┤
│  ORDER  ({lots} lot{"s" if lots!=1 else ""} · ₹{m_used:,.0f} margin)
│    {'BUY' if dirn=='LONG' else 'SELL'} {s['delta_symbol']}  LIMIT @ {s['entry_price']:.4f}
│    [{s['entry_type']}]
│
│    TARGET  → {s['target']:.4f}  (+₹{tp_inr:,.0f})
│    STOP    → {s['stop_loss']:.4f}  (-₹{sl_inr:,.0f})
│    R:R     → {s['rr_ratio']}:1
└──────────────────────────────────────────────────────────────────┘""")


def print_analysis(all_sigs, sizer):
    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  EQUITY INDIA — SIGNAL ANALYSIS  |  {ts()}
║  Data: NSE India (real-time) + Delta Exchange (orderbook)
╠══════════════════════════════════════════════════════════════════════╣""")
    if not all_sigs:
        print("║  No signals yet. Market may be closed or data unavailable.")
        print("╚══════════════════════════════════════════════════════════════════════╝")
        sizer.status(); return

    for rank, s in enumerate(all_sigs[:6], 1):
        lots = sizer.get_lots(s["instrument"], s["score"])
        dirn = s["direction"]
        nse_tag = "✅ NSE" if s.get("nse_available") else "⚠ No NSE"
        print(f"""║
║  #{rank}  {grade(s['score'])}  Score:{s['score']}/100  [{nse_tag}]
║  {s['strategy']}  |  {s['instrument']}  |  {'▲' if dirn=='LONG' else '▼'} {dirn}
║  {s['reason']}
║  Entry:{s['entry_price']:.4f} [{s['entry_type']}]  Target:{s['target']:.4f}  SL:{s['stop_loss']:.4f}
║  Lots:{lots}  |  PCR:{s.get('pcr','N/A')}  Support:{s.get('nse_support','N/A')}  Resist:{s.get('nse_resistance','N/A')}""")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    sizer.status()


# ════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instrument", default="all", choices=list(INSTRUMENTS.keys())+["all"])
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  EQUITY INDIA ALGO  —  NSE + Delta Exchange
║  Real NSE data: {'✅ Active' if _nse_ok else '⚠  Offline (start: npx stock-nse-india@latest)'}
║  7 strategies · Best bid logic · Dynamic sizing
╚══════════════════════════════════════════════════════════════════════╝""")

    dc    = DeltaClient()
    sizer = EquitySizer()
    insts = list(INSTRUMENTS.keys()) if args.instrument=="all" else [args.instrument]

    print(f"  [{ts()}] Scanning: {', '.join(insts)}")
    print(f"  [{ts()}] Refresh: {REFRESH}s · Full analysis every {ANALYSIS_EVERY}s\n")

    cycle=0; last_analysis=0; alerted=set()

    while True:
        try:
            cycle+=1; now=time.time()

            if (now-last_analysis)>=ANALYSIS_EVERY:
                all_sigs=[]
                for inst in insts:
                    all_sigs.extend(scan(inst,dc))
                all_sigs.sort(key=lambda x:x["score"],reverse=True)
                print_analysis(all_sigs,sizer)
                last_analysis=now
                for s in all_sigs:
                    ak=f"{s['instrument']}_{s['strategy']}_{s['direction']}"
                    if s["score"]>=IMMEDIATE_SC and ak not in alerted:
                        print(f"\n🔔 IMMEDIATE [{ts()}]")
                        print_signal(s, sizer)
                        alerted.add(ak)
                if len(alerted)>30: alerted.clear()
            else:
                for inst in insts:
                    cfg=INSTRUMENTS[inst]
                    t=dc.get_ticker(cfg["delta_sym"])
                    nse_spot=""
                    if _nse_ok and _nse:
                        try:
                            idx=_nse.get_banknifty() if "BANK" in inst else _nse.get_nifty()
                            if idx: nse_spot=f"  NSE:{idx['last']:,.2f}({idx['change_pct']:+.2f}%)"
                        except Exception: pass
                    if t:
                        print(f"  [{ts()}]  {inst:<12} Delta:{t['mark']:.2f}{nse_spot}  Chg:{t['change_pct']:+.2f}%  Fund:{t['funding']*100:.4f}%")

            time.sleep(REFRESH)

        except KeyboardInterrupt:
            print(f"\n\n  [{ts()}] Options: [1]P&L  [2]Update margin  [3]Resume  [4]Quit")
            try: c=input("  Choice: ").strip()
            except EOFError: c="3"
            if   c=="1": sizer.status()
            elif c=="2": sizer.update()
            elif c=="4": print(f"\n  P&L: ₹{sizer.pnl_today:+,.0f}"); break
            else: print(f"\n  [{ts()}] Resuming...\n")
        except Exception as e:
            print(f"  [{ts()}] Error: {e}"); time.sleep(REFRESH)

if __name__=="__main__":
    main()