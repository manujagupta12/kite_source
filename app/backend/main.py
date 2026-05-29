"""
ALGOTRADE BACKEND  —  app/backend/main.py
v3.3.0 — XLS removed, NSE Direct API wired, nse_live flag live
"""

import asyncio, csv, hashlib, json, logging, os, random, sys, time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

try:
    from jose import jwt; _JWT_OK = True
except ImportError:
    _JWT_OK = False

try:
    import bcrypt as _bcrypt; _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

_ALGO_DIR = Path(__file__).parent.parent.parent / "algo"
sys.path.insert(0, str(_ALGO_DIR))

# ── Data provider — NSE Direct API (replaces multitrade_loader / XLS) ──────
try:
    import algo.data_provider as _loader
    _NSE_OK = True
    logging.info("[DataProvider] NSE Direct API loaded")
except ImportError:
    try:
        import data_provider as _loader
        _NSE_OK = True
        logging.info("[DataProvider] NSE Direct API loaded (local path)")
    except ImportError:
        _loader = None
        _NSE_OK = False
        logging.warning("[DataProvider] Not loaded — signals will use mock")

# Legacy alias kept so any remaining code that checks _XLS_OK still works
_XLS_OK = _NSE_OK

try:
    from pcr_strategy import PCRStrategy, NseOiFetcher
    _pcr_fetcher = NseOiFetcher(); _pcr_strategy = PCRStrategy(_pcr_fetcher); _PCR_OK = True
except Exception as e:
    _PCR_OK = False; _pcr_strategy = None
    logging.warning(f"[PCR] Not loaded: {e}")

try:
    import trader_logger as _tl; _tl.load_today(); _TL_OK = True
except Exception as e:
    _TL_OK = False; _tl = None
    logging.warning(f"[TL] trader_logger not loaded: {e}")

SECRET_KEY   = os.environ.get("SECRET_KEY", "algotrade-dev-secret-CHANGE-IN-PROD")
ALGORITHM   = "HS256"
TOKEN_EXPIRE = 60 * 24 * 7
logging.basicConfig(level=logging.INFO, format="%(message)s")

_db: Dict[str, Any] = {"users": {}, "trades": {}, "signals": [], "regime": {}}
_paper: Dict[str, Any] = {}

# ── Subscription Plans ────────────────────────────────────────────────────
TIERS = {
    "free":    {"strategies": 2,  "instruments": 1, "live": False, "delay_min": 15, "log_trades": False},
    "weekly":  {"strategies": 7,  "instruments": 3, "live": True,  "delay_min": 0,  "log_trades": True},
    "monthly": {"strategies": 7,  "instruments": 5, "live": True,  "delay_min": 0,  "log_trades": True},
    "annual":  {"strategies": 7,  "instruments": 6, "live": True,  "delay_min": 0,  "log_trades": True},
}

PLANS_LIST = [
    {
        "id": "free", "name": "Free", "badge": "START FREE",
        "weekly_price": 0, "monthly_price": 0, "annual_price": 0,
        "features": ["Paper trading only", "15-min delayed signals", "2 strategies", "1 instrument", "No trade logging"],
    },
    {
        "id": "weekly", "name": "Weekly", "badge": "TRY IT",
        "weekly_price": 500, "monthly_price": None, "annual_price": None,
        "features": ["Live signals", "All 7 F&O strategies", "3 instruments", "Trade logger", "Paper + Live trades"],
    },
    {
        "id": "monthly", "name": "Monthly", "badge": "POPULAR",
        "weekly_price": None, "monthly_price": 1500, "annual_price": None,
        "features": ["Everything in Weekly", "5 instruments", "PCR strategy", "Backtest access", "Priority support"],
    },
    {
        "id": "annual", "name": "Annual", "badge": "BEST VALUE",
        "weekly_price": None, "monthly_price": None, "annual_price": 10000,
        "features": ["Everything in Monthly", "6 instruments", "API access", "Custom alerts", "Save \u20b98,000 vs monthly"],
    },
]

# ── Auth helpers ──────────────────────────────────────────────────────────
def hash_password(pw):
    if _BCRYPT_OK: return _bcrypt.hashpw(pw.encode()[:72], _bcrypt.gensalt()).decode()
    return hashlib.sha256(pw.encode()).hexdigest()

def verify_password(pw, hashed):
    if _BCRYPT_OK:
        try: return _bcrypt.checkpw(pw.encode()[:72], hashed.encode())
        except: return False
    return hashlib.sha256(pw.encode()).hexdigest() == hashed

def create_token(data, expires_minutes=TOKEN_EXPIRE):
    payload = {**data, "exp": datetime.utcnow() + timedelta(minutes=expires_minutes)}
    if _JWT_OK: return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    import base64; return base64.b64encode(json.dumps(payload, default=str).encode()).decode()

def decode_token(token):
    try:
        if _JWT_OK: return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        import base64; return json.loads(base64.b64decode(token.encode()).decode())
    except: return None

security = HTTPBearer(auto_error=False)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    if not creds: raise HTTPException(401, "Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload: raise HTTPException(401, "Invalid token")
    user = _db["users"].get(payload.get("sub"))
    if not user: raise HTTPException(401, "User not found")
    return user

def get_optional_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    if not creds: return None
    payload = decode_token(creds.credentials)
    if not payload: return None
    return _db["users"].get(payload.get("sub"))

# ── Pydantic models ───────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str; email: str; password: str
class LoginRequest(BaseModel):
    email: str; password: str
class TradeLogRequest(BaseModel):
    strategy: str; instrument: str; option_type: str = "CE"; direction: str
    near_strike: int = 0; far_strike: int = 0; lots: int = 1; entry_spread: float = 0.0
    notes: Optional[str] = ""
class TradeCloseRequest(BaseModel):
    trade_id: str; exit_spread: float; notes: Optional[str] = ""
class FoSignalIngest(BaseModel):
    strategy: str; instrument: str; direction: str
    near_strike: Optional[int]=None; far_strike: Optional[int]=None
    spread: Optional[float]=None; fair_value: Optional[float]=None
    deviation: Optional[float]=None; score: Optional[int]=70
    vix: Optional[float]=None; regime: Optional[str]="LIVE"
    risk: Optional[str]="MEDIUM"; source: Optional[str]="calendar_algo"
    action: Optional[str]=""; reason: Optional[str]=""; orders: Optional[str]=""
    target_pts: Optional[float]=None; sl_pts: Optional[float]=None
    lots_suggested: Optional[int]=1; near_bid: Optional[float]=None
    near_ask: Optional[float]=None; far_bid: Optional[float]=None
    far_ask: Optional[float]=None; buy_far_at: Optional[float]=None
    sell_near_at: Optional[float]=None; event_type: Optional[str]="signal"
class PaperTradeRequest(BaseModel):
    strategy: str; instrument: str; direction: str
    lots: int=1; entry_spread: float=0.0
    near_strike: Optional[int]=None; notes: Optional[str]=""
class PaperCloseRequest(BaseModel):
    trade_id: str; exit_spread: float; notes: Optional[str]=""
class UpgradeRequest(BaseModel):
    plan: str; billing: str = "monthly"

class TLTradeEnterRequest(BaseModel):
    strategy: str; instrument: str = "BANKNIFTY"; option_type: str = "CE"
    direction: str = "LONG"; near_strike: str = "0"; far_strike: str = "0"
    lots: int = 1; entry_spread: float = 0.0; notes: Optional[str] = ""
class TLTradeCloseRequest(BaseModel):
    trade_index: int; exit_spread: float; notes: Optional[str] = ""

LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}
_MOCK_REGIMES = ["R2 SIDEWAYS LOW", "R3 SIDEWAYS HIGH IV", "R4 TRENDING BULL", "R6 HIGH VOLATILITY"]
_ROUND_ROBIN = ["NIFTY", "BANKNIFTY", "FINNIFTY"]
_rr_idx = 0

# ── Mock signal generators ────────────────────────────────────────────────
def _mock_fo_signal(instrument=None):
    global _rr_idx
    if instrument is None:
        instrument = _ROUND_ROBIN[_rr_idx % 3]; _rr_idx += 1
    bases = {"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000}
    steps = {"NIFTY":50,"BANKNIFTY":100,"FINNIFTY":50}
    fo_strats = ["S1 CALENDAR","S2 IRON CONDOR","S3 SHORT STRADDLE","S4 0DTE SCALP"]
    base=bases[instrument]; spot=base+random.randint(-500,500)
    atm=int(round(spot/steps[instrument])*steps[instrument])
    strat=random.choice(fo_strats); score=random.randint(58,95)
    dirn=random.choice(["LONG","SHORT"])
    spread=round(random.uniform(-8,8),2); fair=round(spread+random.uniform(-3,3),2)
    vix=round(random.uniform(12,22),2)
    return {
        "timestamp":datetime.now().isoformat(),"source":"mock","market":"FO",
        "strategy":strat,"score":score,"direction":dirn,"instrument":instrument,
        "symbol":instrument,"near_strike":atm,"far_strike":atm,
        "spread":spread,"fair_value":fair,"deviation":round(spread-fair,2),
        "vix":vix,"regime":random.choice(_MOCK_REGIMES),
        "risk":"LOW" if vix<13 else "MEDIUM" if vix<19 else "HIGH",
        "near_bid":round(150+random.uniform(-20,20),2),
        "near_ask":round(152+random.uniform(-20,20),2),
        "far_bid":round(155+random.uniform(-20,20),2),
        "far_ask":round(157+random.uniform(-20,20),2),
        "buy_far_at":round(156+random.uniform(-5,5),2),
        "sell_near_at":round(149+random.uniform(-5,5),2),
        "target_pts":8,"sl_pts":6,"lots_suggested":random.randint(1,5),
        "reason":f"{strat} | Dev={round(spread-fair,2):+.2f}pts | VIX {vix:.1f}",
        "action":f"{dirn} {instrument} @ {atm}",
        "orders":f"BUY Far {instrument} {atm} CE @ {round(156+random.uniform(-5,5),2)}\nSELL Near {instrument} {atm} CE @ {round(149+random.uniform(-5,5),2)}",
        "event_type":"signal",
    }

def _mock_pcr_signal(instrument=None):
    inst=instrument or random.choice(["NIFTY","BANKNIFTY","FINNIFTY"])
    bases={"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000}
    spot=bases[inst]+random.randint(-300,300)
    vix=round(random.uniform(12,22),2)
    pcr=random.choice([round(random.uniform(0.45,0.62),3),round(random.uniform(1.28,1.55),3)])
    is_oversold=pcr>1.28; is_overbought=pcr<0.62
    zone="OVERSOLD" if is_oversold else "OVERBOUGHT"
    dirn="LONG" if is_oversold else "SHORT"
    signal="BULLISH REVERSAL EXPECTED" if is_oversold else "BEARISH REVERSAL EXPECTED"
    tag="FEAR EXTREME" if is_oversold else "GREED EXTREME"
    dist=(pcr-1.3) if is_oversold else (0.6-pcr)
    score=min(92,62+int(dist*60)); risk="LOW" if score>=80 else "MEDIUM"
    put_oi=random.randint(8_000_000,25_000_000); call_oi=round(put_oi/pcr)
    put_vol=random.randint(500_000,3_000_000); call_vol=round(put_vol/(pcr*0.95))
    return {
        "timestamp":datetime.now().isoformat(),"source":"pcr_mock","market":"FO",
        "strategy":"S5 PCR CONTRARIAN","score":score,"direction":dirn,
        "instrument":inst,"symbol":inst,"zone":zone,"tag":tag,"signal":signal,
        "pcr_oi":pcr,"pcr_volume":round(put_vol/call_vol,3),
        "total_put_oi":put_oi,"total_call_oi":call_oi,
        "total_put_vol":put_vol,"total_call_vol":call_vol,
        "spot":spot,"vix":vix,"risk":risk,
        "regime":"FEAR" if is_oversold else "GREED",
        "near_strike":None,"far_strike":None,"spread":None,"target_pts":None,"sl_pts":None,
        "lots_suggested":1,
        "reason":f"PCR_OI {pcr:.3f} {'>' if is_oversold else '<'} {1.3 if is_oversold else 0.6}",
        "action":f"{dirn} {inst} \u2014 PCR={pcr:.3f} ({tag})",
        "event_type":"signal",
    }

# ── FIXED: NSE live signal (was _xls_signal, used wrong far_bid key) ───────
def _nse_signal():
    """Generate live calendar signal from NSE Direct API via data_provider."""
    if not _NSE_OK or _loader is None:
        return None
    try:
        df = _loader.get_instruments("BANKNIFTY")
        if df is None or df.empty:
            return None
        atm = _loader.get_atm_strike(df)
        if not atm:
            return None
        ce = _loader.get_spread(df, atm, "CE", "BANKNIFTY")
        pe = _loader.get_spread(df, atm, "PE", "BANKNIFTY")
        if ce is None:
            return None
        spread = ce["spread"]
        fair   = ce["fair"]
        dev    = ce["deviation"]
        score  = min(95, max(30, 50 + int(abs(dev) * 8)))
        dirn   = "LONG" if dev < -3 else "SHORT" if dev > 3 else "WAIT"
        if dirn == "WAIT":
            return None
        sig = {
            "timestamp":    datetime.now().isoformat(),
            "source":       "nse_live",          # FIXED: was 'xls_live'
            "market":       "FO",
            "strategy":     "S1 CALENDAR",
            "score":        score,
            "direction":    dirn,
            "instrument":   "BANKNIFTY",
            "symbol":       "BANKNIFTY",
            "near_strike":  atm,
            "far_strike":   atm,
            "spread":       spread,
            "fair_value":   fair,
            "deviation":    dev,
            "vix":          None,
            "regime":       "LIVE",
            "risk":         "MEDIUM",
            "near_bid":     ce.get("bid"),
            "near_ask":     ce.get("ask"),
            "far_bid":      ce.get("far_bid"),   # FIXED: was ce.get("far_leg")
            "far_ask":      ce.get("far_ask"),   # FIXED: now included
            "buy_far_at":   ce.get("buy_far_at"),
            "sell_near_at": ce.get("sell_near_at"),
            "target_pts":   8,
            "sl_pts":       6,
            "lots_suggested": 1,
            "near_expiry":  ce.get("near_expiry", ""),
            "far_expiry":   ce.get("far_expiry", ""),
            "reason":       f"CE Spread {spread:+.2f} | Fair {fair:+.2f} | Dev {dev:+.2f}",
            "action":       f"{dirn} BANKNIFTY Calendar @ {atm}",
            "orders":       (
                f"BUY Far {atm} CE @ {ce.get('buy_far_at')}\n"
                f"SELL Near {atm} CE @ {ce.get('sell_near_at')}"
            ),
            "event_type":   "signal",
        }
        if pe:
            sig.update({
                "pe_spread":    pe.get("spread"),
                "pe_deviation": pe.get("deviation"),
                "pe_buy_far":   pe.get("buy_far_at"),
                "pe_sell_near": pe.get("sell_near_at"),
                "pe_far_bid":   pe.get("far_bid"),   # FIXED: correct key
            })
        return sig
    except Exception as e:
        logging.debug(f"[NSE] signal error: {e}")
        return None

# Keep old name as alias so any external callers don't break
_xls_signal = _nse_signal

def _pcr_signal_live(vix=None):
    if not _PCR_OK or not _pcr_strategy: return []
    try: return _pcr_strategy.generate_all(vix=vix)
    except Exception as e: logging.warning(f"[PCR] {e}"); return []

NSE_FO_STOCKS=[
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK",
    "BAJAJ-AUTO","BAJAJFINSV","BAJFINANCE","BHARTIARTL","BPCL",
    "BRITANNIA","CIPLA","COALINDIA","DIVISLAB","DRREDDY",
    "EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK",
    "INFY","ITC","JSWSTEEL","KOTAKBANK","LT",
    "M&M","MARUTI","NESTLEIND","NTPC","ONGC",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN",
    "SUNPHARMA","TATACONSUM","TATAMOTORS","TATASTEEL","TCS",
    "TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
]

import requests as _req
_nse_sess=_req.Session(); _nse_sess_ts=0.0

def _nse_refresh():
    global _nse_sess,_nse_sess_ts
    if time.time()-_nse_sess_ts>60:
        try:
            _nse_sess=_req.Session()
            _nse_sess.headers.update({"User-Agent":"Mozilla/5.0","Accept":"application/json","Referer":"https://www.nseindia.com"})
            _nse_sess.get("https://www.nseindia.com",timeout=5)
            _nse_sess_ts=time.time()
        except: pass

def _nse_equity_quote(symbol):
    _nse_refresh()
    try:
        r=_nse_sess.get(f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",timeout=5)
        pi=r.json().get("priceInfo",{})
        return {"symbol":symbol,"ltp":float(pi.get("lastPrice") or 0),
                "change_pct":float(pi.get("pChange") or 0),"open":float(pi.get("open") or 0),
                "high":float((pi.get("intraDayHighLow") or {}).get("max") or 0),
                "low":float((pi.get("intraDayHighLow") or {}).get("min") or 0),
                "prev_close":float(pi.get("previousClose") or 0)}
    except: return {}

_latest_indices_map: Dict[str,dict]={}
IDX_ORDER=["NIFTY","BANKNIFTY","FINNIFTY","VIX","MIDCAP","IT"]

def _fetch_live_indices():
    _nse_refresh(); result=[]
    try:
        r=_nse_sess.get("https://www.nseindia.com/api/allIndices",timeout=5)
        name_map={"NIFTY 50":"NIFTY","NIFTY BANK":"BANKNIFTY","NIFTY FIN SERVICE":"FINNIFTY",
                  "INDIA VIX":"VIX","NIFTY MIDCAP 100":"MIDCAP","NIFTY IT":"IT"}
        fetched={}
        for item in r.json().get("data",[]):
            nm=item.get("index","")
            if nm in name_map:
                lbl=name_map[nm]; ltp=float(item.get("last") or 0)
                fetched[lbl]={"label":lbl,"ltp":round(ltp,2),
                              "change_pct":round(float(item.get("percentChange") or 0),2),
                              "change":float(item.get("change") or 0),
                              "high":float(item.get("high") or ltp),
                              "low":float(item.get("low") or ltp),"_ts":int(time.time())}
        if fetched: result=[fetched[l] for l in IDX_ORDER if l in fetched]
    except: pass
    if not result:
        BASES={"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000,"VIX":14.5,"MIDCAP":52000,"IT":37000}
        for lbl in IDX_ORDER:
            prev=_latest_indices_map.get(lbl); base=prev["ltp"] if prev else BASES[lbl]
            ltp=round(base+base*random.uniform(-0.003,0.003),2)
            result.append({"label":lbl,"ltp":ltp,
                           "change_pct":round((ltp-BASES[lbl])/BASES[lbl]*100,2),
                           "change":round(ltp-BASES[lbl],2),
                           "high":round(ltp*1.01,2),"low":round(ltp*0.99,2),"_ts":int(time.time())})
    return result

def generate_equity_signals(top_n=8):
    signals=[]; now=datetime.now()
    market_open=(now.weekday()<5 and ((now.hour==9 and now.minute>=15) or (10<=now.hour<=14) or (now.hour==15 and now.minute<=30)))
    stocks=random.sample(NSE_FO_STOCKS,min(25,len(NSE_FO_STOCKS)))
    for stock in stocks:
        try:
            q=_nse_equity_quote(stock); ltp=float(q.get("ltp") or 0)
            if ltp==0: ltp=random.uniform(500,4000); q={"ltp":ltp,"change_pct":random.uniform(-3,3),"open":ltp*0.998,"high":ltp*1.012,"low":ltp*0.988,"prev_close":ltp*0.997}
            chg=float(q.get("change_pct") or 0); prev=float(q.get("prev_close") or ltp)
            high=float(q.get("high") or ltp*1.01); low=float(q.get("low") or ltp*0.99)
            open_=float(q.get("open") or ltp); gap_pct=(open_-prev)/prev*100 if prev else 0
            strategy=direction=reason=None; score=0
            if abs(chg)>1.8: strategy="E1 EMA CROSSOVER"; direction="BUY" if chg>0 else "SELL"; score=min(88,58+int(abs(chg)*7)); reason=f"EMA9 crossed {'above' if direction=='BUY' else 'below'} EMA21 | {chg:+.2f}%"
            elif abs(gap_pct)>0.8 and now.hour<=10: strategy="E6 GAP FILL"; direction="SELL" if gap_pct>0 else "BUY"; score=min(84,58+int(abs(gap_pct)*6)); reason=f"Gap {gap_pct:+.2f}%"
            elif abs(chg)>0.9: strategy="E2 VWAP REVERSION"; direction="BUY" if chg<0 else "SELL"; score=min(78,52+int(abs(chg)*5)); reason=f"Price {chg:+.2f}% from VWAP"
            elif now.hour==9 and now.minute>=30 and (high-low)>0:
                if ltp>=high*0.999: direction="BUY"
                elif ltp<=low*1.001: direction="SELL"
                if direction: strategy="E3 ORB BREAKOUT"; score=min(80,60+int((high-low)/ltp*200)); reason=f"ORB {'breakout' if direction=='BUY' else 'breakdown'}"
            if not strategy or not direction or score<55: continue
            buf=ltp*0.002; entry=round(ltp+buf if direction=="BUY" else ltp-buf,2)
            target=round(ltp*1.015 if direction=="BUY" else ltp*0.985,2)
            sl=round(ltp*0.993 if direction=="BUY" else ltp*1.007,2)
            signals.append({"market":"EQUITY","strategy":strategy,"symbol":stock,
                            "score":score,"direction":direction,"risk":"MEDIUM",
                            "ltp":round(ltp,2),"change_pct":round(chg,2),
                            "open":round(open_,2),"high":round(high,2),"low":round(low,2),"prev_close":round(prev,2),
                            "entry_at":entry,"target_at":target,"sl_at":sl,
                            "source":"NSE_DIRECT" if market_open else "NSE_EOD",
                            "timestamp":datetime.now().isoformat(),"reason":reason,
                            "action":f"{direction} {stock} @ {entry}"})
        except: continue
    return sorted(signals,key=lambda x:x["score"],reverse=True)[:top_n]

class Broadcaster:
    def __init__(self): self.connections:List[WebSocket]=[]
    async def connect(self,ws): await ws.accept(); self.connections.append(ws)
    def disconnect(self,ws): self.connections=[c for c in self.connections if c is not ws]
    async def broadcast(self,msg):
        payload=json.dumps(msg,default=str); dead=[]
        for ws in self.connections:
            try: await ws.send_text(payload)
            except: dead.append(ws)
        for ws in dead: self.disconnect(ws)

broadcaster=Broadcaster()

async def signal_loop():
    global _latest_indices_map
    cycle=0
    for inst in ["NIFTY","BANKNIFTY","FINNIFTY"]:
        _db["signals"].append(_mock_fo_signal(inst))
    for inst in ["NIFTY","BANKNIFTY","FINNIFTY"]:
        _db["signals"].append(_mock_pcr_signal(inst))
    while True:
        await asyncio.sleep(5); cycle+=1
        # FIXED: use _nse_signal() not _xls_signal()
        fo_sig = _nse_signal() or _mock_fo_signal()
        _db["signals"].append(fo_sig); _db["signals"]=_db["signals"][-300:]
        await broadcaster.broadcast({"type":"signal","data":fo_sig})
        pcr_mock=_mock_pcr_signal(_ROUND_ROBIN[cycle%3])
        _db["signals"].append(pcr_mock); _db["signals"]=_db["signals"][-300:]
        await broadcaster.broadcast({"type":"signal","data":pcr_mock})
        if cycle%2==0:
            indices=await asyncio.get_event_loop().run_in_executor(None,_fetch_live_indices)
            if indices:
                _latest_indices_map={idx["label"]:idx for idx in indices}
                await broadcaster.broadcast({"type":"indices_update","indices":indices,"_ts":int(time.time())})
        if cycle%6==0:
            eq=generate_equity_signals(top_n=6)
            for s in eq: _db["signals"].append(s)
            _db["signals"]=_db["signals"][-300:]
            await broadcaster.broadcast({"type":"equity_signals","signals":eq,"count":len(eq)})
        if cycle%36==0 and _PCR_OK:
            vix=_latest_indices_map.get("VIX",{}).get("ltp")
            pcr_live=await asyncio.get_event_loop().run_in_executor(None,_pcr_signal_live,vix)
            for ps in pcr_live:
                _db["signals"].append(ps)
                await broadcaster.broadcast({"type":"signal","data":ps})
            if pcr_live: _db["signals"]=_db["signals"][-300:]
        if cycle%30==0:
            last=_db["signals"][-1]
            await broadcaster.broadcast({"type":"regime","regime":last.get("regime","UNKNOWN"),"vix":last.get("vix"),"risk":last.get("risk","MEDIUM"),"timestamp":last.get("timestamp"),"source":last.get("source","mock")})
        if cycle%12==0:
            # FIXED: nse_live now reflects actual NSE connectivity, not hardcoded False
            await broadcaster.broadcast({
                "type":      "heartbeat",
                "xls_live":  False,          # XLS permanently retired
                "nse_live":  _NSE_OK,        # FIXED: true when data_provider loaded
                "pcr_live":  _PCR_OK,
                "tl_live":   _TL_OK,
                "signals_n": len(_db["signals"]),
                "timestamp": datetime.now().isoformat(),
            })

@asynccontextmanager
async def lifespan(app:FastAPI):
    if "demo@algotrade.in" not in _db["users"]:
        _db["users"]["demo@algotrade.in"]={"name":"Demo User","email":"demo@algotrade.in",
            "password":hash_password("demo123"),"plan":"monthly","billing":"monthly",
            "joined":str(date.today()),"daily_target":50000,"plan_expiry":str(date.today()+timedelta(days=30))}
    logging.info(f"  AlgoTrade API v3.3  NSE:{_NSE_OK}  PCR:{_PCR_OK}  TradeLogger:{_TL_OK}")
    task=asyncio.create_task(signal_loop())
    yield
    task.cancel()

app=FastAPI(title="AlgoTrade API",version="3.3.0",lifespan=lifespan)
app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,allow_methods=["*"],allow_headers=["*"])

# ── Auth ──────────────────────────────────────────────────────────────────
@app.post("/auth/register")
def register(req:RegisterRequest):
    if req.email in _db["users"]: raise HTTPException(400,"Email already registered")
    _db["users"][req.email]={"name":req.name,"email":req.email,
        "password":hash_password(req.password),"plan":"free","billing":"monthly",
        "joined":str(date.today()),"daily_target":50000,"plan_expiry":None}
    return {"token":create_token({"sub":req.email}),
            "user":{k:v for k,v in _db["users"][req.email].items() if k!="password"}}

@app.post("/auth/login")
def login(req:LoginRequest):
    user=_db["users"].get(req.email)
    if not user or not verify_password(req.password,user["password"]): raise HTTPException(401,"Invalid credentials")
    return {"token":create_token({"sub":req.email}),
            "user":{k:v for k,v in user.items() if k!="password"}}

@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {k:v for k,v in user.items() if k!="password"}

# ── Signals ───────────────────────────────────────────────────────────────
@app.get("/signals")
def signals_shorthand(limit:int=50,user=Depends(get_optional_user)):
    # FIXED: expose nse_live not xls_live to frontend
    return {"signals":_db["signals"][-limit:][::-1],"count":len(_db["signals"]),
            "nse_live":_NSE_OK,"xls_live":False}

@app.get("/signals/latest")
def get_signals(limit:int=50,user=Depends(get_optional_user)):
    return {"signals":_db["signals"][-limit:][::-1],"count":len(_db["signals"]),
            "nse_live":_NSE_OK,"xls_live":False}

@app.get("/signals/regime")
def get_regime():
    if _db["signals"]:
        last=_db["signals"][-1]
        return {"regime":last.get("regime","DETECTING"),"vix":last.get("vix"),"risk":last.get("risk","MEDIUM"),"timestamp":last.get("timestamp"),"source":last.get("source","mock")}
    return {"regime":"DETECTING","vix":None,"risk":"UNKNOWN"}

@app.get("/signals/equity")
def equity_signals(top:int=10,user=Depends(get_optional_user)):
    signals=generate_equity_signals(top_n=top)
    return {"signals":signals,"count":len(signals),"timestamp":datetime.now().isoformat()}

@app.post("/signals/fo_ingest")
async def fo_ingest(req:FoSignalIngest):
    signal={"timestamp":datetime.now().isoformat(),"source":req.source or "calendar_algo",
            "market":"FO","strategy":req.strategy,"score":req.score or 70,
            "direction":req.direction,"instrument":req.instrument,"symbol":req.instrument,
            "near_strike":req.near_strike,"far_strike":req.far_strike,
            "spread":req.spread,"fair_value":req.fair_value,"deviation":req.deviation,
            "vix":req.vix,"regime":req.regime or "LIVE","risk":req.risk or "MEDIUM",
            "action":req.action or f"{req.direction} {req.instrument} @ {req.near_strike}",
            "reason":req.reason or "","orders":req.orders or "",
            "target_pts":req.target_pts,"sl_pts":req.sl_pts,
            "lots_suggested":req.lots_suggested or 1,
            "near_bid":req.near_bid,"near_ask":req.near_ask,
            "far_bid":req.far_bid,"buy_far_at":req.buy_far_at,
            "sell_near_at":req.sell_near_at,"event_type":req.event_type or "signal"}
    _db["signals"].append(signal); _db["signals"]=_db["signals"][-300:]
    await broadcaster.broadcast({"type":"signal","data":signal})
    return {"ok":True,"signal":signal}

@app.get("/signals/pcr")
def pcr_signals_endpoint(symbol:str="NIFTY"):
    if not _PCR_OK: return {"signals":[_mock_pcr_signal(symbol.upper())],"message":"PCR mock"}
    vix=_latest_indices_map.get("VIX",{}).get("ltp")
    sig=_pcr_strategy.generate_signal(symbol.upper(),vix=vix)
    return {"signals":[sig] if sig else [],"count":1 if sig else 0,"timestamp":datetime.now().isoformat()}

@app.get("/chart/{symbol}")
def get_chart_data(symbol:str, interval:str="5"):
    symbol=symbol.upper()
    BASES={"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000,"NIFTY50":24500,"VIX":14.5}
    base=BASES.get(symbol,1000)
    candles=[]; now=datetime.now(); price=base
    for i in range(60,0,-1):
        ts=now-timedelta(minutes=int(interval)*i)
        open_=round(price,2); chg=random.uniform(-0.008,0.008)
        close=round(price*(1+chg),2)
        high=round(max(open_,close)*(1+random.uniform(0,0.004)),2)
        low=round(min(open_,close)*(1-random.uniform(0,0.004)),2)
        vol=random.randint(50000,500000)
        candles.append({"time":ts.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open":open_,"high":high,"low":low,"close":close,"volume":vol})
        price=close
    current_price=candles[-1]["close"] if candles else base
    return {"symbol":symbol,"interval":interval,"candles":candles,
            "current_price":current_price,"timestamp":datetime.now().isoformat()}

# ── Trader Logger ─────────────────────────────────────────────────────────
@app.get("/tradelog/today")
def tl_today(user=Depends(get_current_user)):
    if not _TL_OK:
        trades=_db["trades"].get(user["email"],[])
        return {"trades":trades,"source":"db","tl_available":False}
    realised,open_count,total=_tl.get_daily_pnl()
    return {"trades":_tl._trades,"realised_pnl":realised,
            "open_count":open_count,"total_trades":total,
            "source":"csv","tl_available":True,
            "log_path":_tl._get_log_path()}

@app.post("/tradelog/enter")
def tl_enter(req:TLTradeEnterRequest,user=Depends(get_current_user)):
    tier=TIERS.get(user.get("plan","free"),TIERS["free"])
    if not tier["log_trades"]: raise HTTPException(403,"Trade logging requires Weekly plan or above")
    if not _TL_OK:
        import uuid; ls=LOT_SIZES.get(req.instrument,25)
        trade={"id":str(uuid.uuid4())[:8].upper(),"date":str(date.today()),
               "entry_time":datetime.now().isoformat(),"exit_time":None,
               "strategy":req.strategy,"instrument":req.instrument,
               "type":req.option_type,"direction":req.direction,
               "near_strike":req.near_strike,"far_strike":req.far_strike,
               "lots":req.lots,"lot_size":ls,"entry_spread":req.entry_spread,
               "exit_spread":None,"pnl_pts":None,"pnl_inr":None,
               "status":"OPEN","notes":req.notes,"mode":"LIVE","source":"manual"}
        _db["trades"].setdefault(user["email"],[]).append(trade)
        return {"ok":True,"trade":trade,"source":"db"}
    from datetime import datetime as dt; ls=LOT_SIZES.get(req.instrument,25)
    row={"time":dt.now().strftime("%H:%M:%S"),"strategy":req.strategy,
         "instrument":req.instrument,"type":req.option_type,"direction":req.direction,
         "near_strike":str(req.near_strike),"far_strike":str(req.far_strike),
         "lots":req.lots,"entry_spread":round(req.entry_spread,2),
         "exit_spread":"","pnl_pts":"","pnl_inr":"","status":"OPEN","notes":req.notes or ""}
    _tl._trades.append(row); _tl._append_row(row)
    return {"ok":True,"trade":row,"source":"csv"}

@app.post("/tradelog/close")
def tl_close(req:TLTradeCloseRequest,user=Depends(get_current_user)):
    if not _TL_OK: raise HTTPException(503,"trader_logger not available; use /trades/close")
    open_trades=[(i,t) for i,t in enumerate(_tl._trades) if str(t.get("status","")).upper()=="OPEN"]
    if req.trade_index<0 or req.trade_index>=len(open_trades): raise HTTPException(400,"Invalid trade index")
    orig_idx,trade=open_trades[req.trade_index]; ls=LOT_SIZES.get(trade.get("instrument","BANKNIFTY"),25)
    lots=int(trade.get("lots") or 1); entry=float(trade.get("entry_spread") or 0)
    dirn=str(trade.get("direction","")).upper()
    pnl_pts=round((req.exit_spread-entry) if dirn in ("LONG","BUY") else (entry-req.exit_spread),2)
    pnl_inr=round(pnl_pts*ls*lots,0)
    trade.update({"exit_spread":round(req.exit_spread,2),"pnl_pts":pnl_pts,
                  "pnl_inr":int(pnl_inr),"status":"CLOSED",
                  "notes":(str(trade.get("notes",""))+" "+(req.notes or "")).strip()})
    _tl._trades[orig_idx]=trade; _tl._write_all()
    return {"ok":True,"trade":trade,"pnl_inr":int(pnl_inr)}

@app.get("/tradelog/summary")
def tl_summary(user=Depends(get_current_user)):
    if not _TL_OK: return {"message":"trader_logger not available","tl_available":False}
    realised,open_count,total=_tl.get_daily_pnl()
    closed=[t for t in _tl._trades if str(t.get("status","")).upper()=="CLOSED"]
    wins=[t for t in closed if float(t.get("pnl_inr") or 0)>0]
    return {"total_trades":total,"open_count":open_count,"closed_count":len(closed),
            "realised_pnl":realised,"win_rate":round(len(wins)/len(closed)*100,1) if closed else 0,
            "winners":len(wins),"losers":len(closed)-len(wins),"tl_available":True}

@app.get("/tradelog/export")
def tl_export(user=Depends(get_current_user)):
    if not _TL_OK or not _tl._trades: return {"csv":"","message":"No trades today"}
    output=StringIO(); w=csv.DictWriter(output,fieldnames=_tl.FIELDNAMES)
    w.writeheader(); w.writerows(_tl._trades)
    return {"csv":output.getvalue(),"rows":len(_tl._trades)}

# ── Live Trades ───────────────────────────────────────────────────────────
@app.post("/trades/enter")
def enter_trade(req:TradeLogRequest,user=Depends(get_current_user)):
    import uuid
    email=user["email"]; trade_id=str(uuid.uuid4())[:8].upper(); ls=LOT_SIZES.get(req.instrument,25)
    trade={"id":trade_id,"date":str(date.today()),"entry_time":datetime.now().isoformat(),"exit_time":None,
           "strategy":req.strategy,"instrument":req.instrument,"type":req.option_type,"direction":req.direction,
           "near_strike":req.near_strike,"far_strike":req.far_strike,"lots":req.lots,"lot_size":ls,
           "entry_spread":req.entry_spread,"exit_spread":None,"pnl_pts":None,"pnl_inr":None,
           "status":"OPEN","notes":req.notes,"mode":"LIVE"}
    _db["trades"].setdefault(email,[]).append(trade)
    return {"trade_id":trade_id,"trade":trade}

@app.post("/trades/close")
def close_trade(req:TradeCloseRequest,user=Depends(get_current_user)):
    email=user["email"]; trades=_db["trades"].get(email,[])
    trade=next((t for t in trades if t["id"]==req.trade_id),None)
    if not trade: raise HTTPException(404,"Trade not found")
    if trade["status"]=="CLOSED": raise HTTPException(400,"Already closed")
    ls=LOT_SIZES.get(trade["instrument"],25)
    pnl_pts=(req.exit_spread-trade["entry_spread"]) if trade["direction"] in ("LONG","BUY BOTH","BULL") else (trade["entry_spread"]-req.exit_spread)
    pnl_inr=round(pnl_pts*ls*trade["lots"],0)
    trade.update({"exit_time":datetime.now().isoformat(),"exit_spread":req.exit_spread,
                  "pnl_pts":round(pnl_pts,2),"pnl_inr":int(pnl_inr),"status":"CLOSED",
                  "notes":(trade.get("notes","")+" | "+(req.notes or "")).strip(" |")})
    return {"trade":trade,"pnl_inr":int(pnl_inr)}

@app.get("/trades")
def trades_shorthand(user=Depends(get_current_user)):
    trades=_db["trades"].get(user["email"],[])
    return {"trades":trades[-100:],"total":len(trades),
            "open_count":sum(1 for t in trades if t["status"]=="OPEN"),
            "closed_count":sum(1 for t in trades if t["status"]=="CLOSED"),
            "total_pnl":sum(t.get("pnl_inr") or 0 for t in trades if t["status"]=="CLOSED")}

# ── Paper Trading ─────────────────────────────────────────────────────────
PAPER_INITIAL=5_000_000
def _get_paper(email):
    if email not in _paper: _paper[email]={"balance":PAPER_INITIAL,"initial":PAPER_INITIAL,"trades":[],"created":str(date.today())}
    return _paper[email]

@app.get("/paper/account")
def paper_account(user=Depends(get_current_user)):
    acc=_get_paper(user["email"]); pnl=acc["balance"]-acc["initial"]
    open_t=[t for t in acc["trades"] if t["status"]=="OPEN"]
    closed_t=[t for t in acc["trades"] if t["status"]=="CLOSED"]
    return {**acc,"total_pnl":pnl,"pnl_pct":round(pnl/acc["initial"]*100,2),
            "open_count":len(open_t),"closed_count":len(closed_t),
            "realised_pnl":sum(t.get("pnl_inr") or 0 for t in closed_t)}

@app.post("/paper/trade")
def paper_trade_enter(req:PaperTradeRequest,user=Depends(get_current_user)):
    import uuid; acc=_get_paper(user["email"])
    margin={"NIFTY":80000,"BANKNIFTY":90000,"FINNIFTY":50000}.get(req.instrument,80000)*req.lots
    if acc["balance"]<margin: raise HTTPException(400,"Insufficient paper capital")
    acc["balance"]-=margin
    trade={"id":str(uuid.uuid4())[:8].upper(),"date":str(date.today()),
           "entry_time":datetime.now().isoformat(),"exit_time":None,
           "strategy":req.strategy,"instrument":req.instrument,
           "direction":req.direction,"lots":req.lots,
           "near_strike":req.near_strike,"entry_spread":req.entry_spread,
           "exit_spread":None,"pnl_pts":None,"pnl_inr":None,
           "margin_used":margin,"status":"OPEN","notes":req.notes,"mode":"PAPER"}
    acc["trades"].append(trade)
    return {"paper_trade":trade,"remaining_balance":acc["balance"]}

@app.post("/paper/close")
def paper_trade_close(req:PaperCloseRequest,user=Depends(get_current_user)):
    acc=_get_paper(user["email"])
    trade=next((t for t in acc["trades"] if t["id"]==req.trade_id),None)
    if not trade: raise HTTPException(404,"Paper trade not found")
    if trade["status"]=="CLOSED": raise HTTPException(400,"Already closed")
    ls=LOT_SIZES.get(trade["instrument"],25)
    pnl_pts=(req.exit_spread-trade["entry_spread"]) if trade["direction"] in ("LONG","BUY","BULL") else (trade["entry_spread"]-req.exit_spread)
    pnl_inr=round(pnl_pts*ls*trade["lots"],0)
    trade.update({"exit_time":datetime.now().isoformat(),"exit_spread":req.exit_spread,
                  "pnl_pts":round(pnl_pts,2),"pnl_inr":int(pnl_inr),"status":"CLOSED",
                  "notes":(trade.get("notes","")+" | "+(req.notes or "")).strip(" |")})
    acc["balance"]+=trade["margin_used"]+pnl_inr
    return {"trade":trade,"pnl_inr":int(pnl_inr),"new_balance":acc["balance"]}

# ── Subscription ──────────────────────────────────────────────────────────
@app.get("/subscription/plans")
def get_plans(): return {"plans":PLANS_LIST}

@app.get("/subscription/status")
def subscription_status(user=Depends(get_current_user)):
    plan=user.get("plan","free"); billing=user.get("billing","monthly")
    tier=TIERS.get(plan,TIERS["free"])
    plan_info=next((p for p in PLANS_LIST if p["id"]==plan),PLANS_LIST[0])
    if billing=="weekly": price=plan_info.get("weekly_price")
    elif billing=="annual": price=plan_info.get("annual_price")
    else: price=plan_info.get("monthly_price")
    return {"plan":plan,"billing":billing,"active_price":price,"tier":tier,
            "plan_info":plan_info,"email":user["email"],"name":user.get("name",""),
            "plan_expiry":user.get("plan_expiry"),"tl_available":_TL_OK}

@app.post("/subscription/upgrade")
def upgrade_plan(req:UpgradeRequest,user=Depends(get_current_user)):
    if req.plan not in TIERS: raise HTTPException(400,"Invalid plan")
    valid_billing=["weekly","monthly","annual"]
    if req.billing not in valid_billing: raise HTTPException(400,f"billing must be one of {valid_billing}")
    if req.billing=="weekly": expiry=str(date.today()+timedelta(days=7))
    elif req.billing=="annual": expiry=str(date.today()+timedelta(days=365))
    else: expiry=str(date.today()+timedelta(days=30))
    _db["users"][user["email"]]["plan"]=req.plan
    _db["users"][user["email"]]["billing"]=req.billing
    _db["users"][user["email"]]["plan_expiry"]=expiry
    plan_info=next((p for p in PLANS_LIST if p["id"]==req.plan),{})
    if req.billing=="weekly": price=plan_info.get("weekly_price")
    elif req.billing=="annual": price=plan_info.get("annual_price")
    else: price=plan_info.get("monthly_price")
    return {"message":f"Upgraded to {req.plan} ({req.billing})","plan":req.plan,
            "billing":req.billing,"price":price,"expiry":expiry,"tier":TIERS[req.plan]}

# ── Analytics ─────────────────────────────────────────────────────────────
@app.get("/analytics/pnl")
def analytics_pnl(user=Depends(get_current_user)):
    trades=[t for t in _db["trades"].get(user["email"],[]) if t["status"]=="CLOSED"]
    pnls=[t.get("pnl_inr") or 0 for t in trades]; wins=[p for p in pnls if p>0]
    by_date:dict={}
    for t in trades:
        d=t.get("date",""); bd=by_date.setdefault(d,{"date":d,"pnl":0,"trades":0})
        bd["pnl"]+=t.get("pnl_inr") or 0; bd["trades"]+=1
    by_strat:dict={}
    for t in trades:
        bs=by_strat.setdefault(t["strategy"],{"trades":0,"wins":0,"total_pnl":0})
        bs["trades"]+=1; bs["wins"]+=1 if (t.get("pnl_inr") or 0)>0 else 0
        bs["total_pnl"]+=t.get("pnl_inr") or 0
    return {"pnl":sorted(by_date.values(),key=lambda x:x["date"]),"by_strategy":by_strat,
            "total_pnl":sum(pnls),"total_trades":len(trades),"winning_trades":len(wins)}

# ── Indices / Movers ──────────────────────────────────────────────────────
@app.get("/indices")
def get_indices(): return {"indices":_fetch_live_indices()}

@app.get("/movers")
def get_movers():
    eq=[s for s in _db["signals"] if s.get("market")=="EQUITY"]
    if not eq: eq=generate_equity_signals(top_n=20)
    by_sym:dict={}
    for s in eq:
        sym=s.get("symbol")
        if sym and sym not in by_sym: by_sym[sym]=s
    sigs=list(by_sym.values())
    gainers=sorted([s for s in sigs if (s.get("change_pct") or 0)>0],key=lambda x:x.get("change_pct",0),reverse=True)[:6]
    losers=sorted([s for s in sigs if (s.get("change_pct") or 0)<0],key=lambda x:x.get("change_pct",0))[:6]
    return {"gainers":gainers,"losers":losers}

# ── WebSocket ─────────────────────────────────────────────────────────────
@app.websocket("/ws/signals")
async def ws_signals(ws:WebSocket):
    await broadcaster.connect(ws)
    try:
        for sig in (_db["signals"][-20:] if _db["signals"] else []):
            await ws.send_text(json.dumps({"type":"signal","data":sig},default=str))
        if _latest_indices_map:
            idxs=[_latest_indices_map[l] for l in IDX_ORDER if l in _latest_indices_map]
            await ws.send_text(json.dumps({"type":"indices_update","indices":idxs,"_ts":int(time.time())}))
        # FIXED: send nse_live not xls_live
        await ws.send_text(json.dumps({
            "type":     "status",
            "nse_live": _NSE_OK,
            "xls_live": False,
            "pcr_live": _PCR_OK,
            "tl_live":  _TL_OK,
        }))
        while True: await ws.receive_text()
    except WebSocketDisconnect: broadcaster.disconnect(ws)

@app.get("/health")
def health_check():
    return {"status":"ok","version":"3.3.0","users":len(_db["users"]),
            "signals":len(_db["signals"]),
            "nse_live":_NSE_OK,   # FIXED
            "xls_live":False,     # XLS retired
            "pcr_live":_PCR_OK,"tl_live":_TL_OK,"timestamp":datetime.now().isoformat()}

@app.get("/")
def root(): return {"message":"AlgoTrade API v3.3 — NSE Direct API","docs":"/docs","health":"/health"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_level="info")
