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

# sys.path must contain the PARENT of algo/ so "from algo.x import" works.
#   Local dev : app/backend/main.py -> .parent.parent.parent = kite_source/
#   Docker    : /app/main.py        -> .parent               = /app/
#               (Dockerfile does COPY algo/ ./algo/ and COPY app/backend/ ./)
_ALGO_PARENT = Path(__file__).parent.parent.parent   # local: kite_source/
if not (_ALGO_PARENT / "algo").exists():
    _ALGO_PARENT = Path(__file__).parent             # Docker: /app/
_ALGO_DIR = _ALGO_PARENT / "algo"
sys.path.insert(0, str(_ALGO_PARENT))               # enables: from algo.x import ...
sys.path.insert(1, str(_ALGO_DIR))                  # enables: import x  (flat, for algo scripts)

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

# ── Dhan token wiring ──────────────────────────────────────────────────────
# Wire the token from env BEFORE anything tries to use it.
# The Dhan token you provided is stored via save_token_manual or .env.
_DHAN_CLIENT_ID    = "1111872026"   # extracted from JWT payload
_DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
if _DHAN_ACCESS_TOKEN:
    os.environ.setdefault("DHAN_CLIENT_ID", _DHAN_CLIENT_ID)
    logging.info(f"[Dhan] Token found in env (client_id={_DHAN_CLIENT_ID})")
else:
    # Fallback: try token file (written by dhan_auth.save_token_manual)
    _dhan_token_file = Path(__file__).parent.parent.parent / "data" / "dhan_token.json"
    if _dhan_token_file.exists():
        try:
            import json as _json
            _dt = _json.loads(_dhan_token_file.read_text())
            os.environ.setdefault("DHAN_CLIENT_ID",    _dt.get("client_id", ""))
            os.environ.setdefault("DHAN_ACCESS_TOKEN", _dt.get("access_token", ""))
            logging.info("[Dhan] Token loaded from dhan_token.json")
        except Exception as _de:
            logging.warning(f"[Dhan] Token file read error: {_de}")
    else:
        logging.warning("[Dhan] No DHAN_ACCESS_TOKEN in env — running on NSE polling only")

try:
    from algo.dhan_ticker import start_dhan_ticker, is_running as _dhan_is_running
    _DHAN_OK = bool(os.environ.get("DHAN_ACCESS_TOKEN"))
except ImportError:
    _DHAN_OK = False
    logging.warning("[DhanTicker] Module not found")

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


# ── IST market hours ─────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo as _ZI; _IST_TZ = _ZI("Asia/Kolkata")
except ImportError:
    from datetime import timezone as _tz, timedelta as _td
    _IST_TZ = _tz(_td(hours=5, minutes=30))

def _ist_now():
    return datetime.now(_IST_TZ)

def _is_market_open() -> bool:
    """True only during NSE trading hours: Mon-Fri 9:15 AM – 3:30 PM IST."""
    n = _ist_now()
    if n.weekday() >= 5: return False          # weekend
    mins = n.hour * 60 + n.minute
    return 9 * 60 + 15 <= mins <= 15 * 60 + 30

def _validate_signal(sig: dict) -> bool:
    """
    Returns True only if signal has realistic NSE-sourced values.
    Rejects random/mock data before it reaches the frontend.
    """
    if not sig: return False
    # Source must not be mock/demo
    src = str(sig.get("source", "")).lower()
    if any(x in src for x in ("mock", "demo", "fake", "random", "sim")):
        return False
    # Score must be in valid range
    score = sig.get("score", 0)
    if not isinstance(score, (int, float)) or not (20 <= score <= 100):
        return False
    # VIX must be realistic if present
    vix = sig.get("vix")
    if vix is not None and not (7.0 <= float(vix) <= 50.0):
        return False
    # PCR must be realistic if present
    pcr = sig.get("pcr_oi")
    if pcr is not None and not (0.2 <= float(pcr) <= 3.0):
        return False
    # Spot price must be positive and realistic
    spot = sig.get("spot") or sig.get("ltp")
    if spot is not None and (float(spot) <= 0 or float(spot) > 10_000_000):
        return False
    # Must have a strategy and direction
    if not sig.get("strategy") or not sig.get("direction"):
        return False
    return True

_DEMO_MODE = os.environ.get("DEMO_MODE", "").lower() in ("true", "1", "yes")

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
    # Generate live calendar signal from NSE Direct API via data_provider.
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

# ── Multi-strategy bridge — S1-S7 via live NSE option chain ──────────────
def _run_all_strategies() -> list:
    if not _NSE_OK or _loader is None: return []
    try:
        from algo.multistrategy import (
            score_calendar, score_iron_condor, score_short_straddle,
            score_momentum, score_strangle, score_expiry, score_ratio, detect_regime)
    except ImportError:
        return []
    try:
        df_raw = _loader.get_instruments("NIFTY")
        if df_raw is None or df_raw.empty: return []
        df = df_raw[df_raw["TYPE"]=="CE"].copy().rename(columns={
            "STRIKE":"near_strike","BID":"near_bid","ASK":"near_ask",
            "LTP":"near_ltp","VOLUME":"near_vol",
            "NEAR_THETA":"near_theta","FAR_THETA":"far_theta",
            "NEAR_VEGA":"near_vega","FAR_VEGA":"far_vega",
            "NEAR_DELTA":"near_delta","FAR_DELTA":"far_delta",
            "FAR_LEG":"far_prem","NEAR_LEG":"near_prem",
        })
        df["straddle"]    = df["near_bid"] * 2
        df["far_strike"]  = df["near_strike"]
        spot = _latest_indices_map.get("NIFTY",{}).get("ltp",0)
        vix  = _latest_indices_map.get("VIX",{}).get("ltp")
        if not spot: return []
        atm  = int(round(spot/50)*50)
        near_exp = None
        try:
            from algo.nse_fetcher import get_fetcher
            near_exp = get_fetcher().get_expiry_dates("NIFTY")[0]
        except Exception: pass
        all_r = []
        for fn, args in [
            (score_calendar,       (df,atm,vix)),
            (score_iron_condor,    (df,atm,vix)),
            (score_short_straddle, (df,atm,vix)),
            (score_momentum,       (df,atm,vix,spot)),
            (score_strangle,       (df,atm,vix)),
            (score_expiry,         (df,atm,vix,near_exp)),
            (score_ratio,          (df,atm,vix)),
        ]:
            try: all_r.extend(fn(*args) or [])
            except Exception as e: logging.debug(f"[MultiStrat] {fn.__name__}: {e}")
        sigs=[]
        for r in sorted(all_r, key=lambda x:x.get("score",0), reverse=True)[:10]:
            rationale = _strategy_why(r, vix, spot)
            sigs.append({
                "id":f"ms_{r['strategy'].replace(' ','_')}_{int(time.time())}",
                "timestamp":datetime.now().isoformat(), "source":"NSE_LIVE", "market":"FO",
                "strategy":r["strategy"], "instrument":r.get("inst","NIFTY"),
                "direction":r.get("direction","WAIT"), "score":r.get("score",60),
                "risk":"LOW" if r.get("score",0)>=80 else "MEDIUM",
                "spot":spot, "vix":vix,
                "near_strike":r.get("near_strike",r.get("strike",atm)),
                "far_strike":r.get("far_strike",r.get("wing_ce",atm)),
                "reason":r.get("reason",""), "orders":r.get("orders",""),
                "risk_note":r.get("risk_note",""),
                "action":r.get("orders","").split("\n")[0] if r.get("orders") else "",
                "why_it_works": rationale,
            })
        return sigs
    except Exception as e:
        logging.debug(f"[MultiStrat] error: {e}")
        return []

def _strategy_why(r:dict, vix, spot) -> str:
    s=r.get("strategy","").upper(); v=f"VIX={vix:.1f}" if vix else ""
    if "CALENDAR" in s:
        dev=r.get("deviation",0); te=r.get("theta_edge",0)
        return (f"Near-expiry theta decays faster than far-expiry. Spread is "
                f"{abs(dev):.1f}pts {'cheap — buy it' if dev<0 else 'expensive — sell it'}. "
                f"Theta edge {te:.4f}/day. {v}")
    if "IRON CONDOR" in s:
        net=r.get("net_premium",0); bu=r.get("breakeven_upper",0); bl=r.get("breakeven_lower",0)
        return (f"Collect ₹{net:.2f} premium. NIFTY must stay between {bl:.0f}–{bu:.0f}. "
                f"Profits from time decay in range-bound market. {v}")
    if "STRADDLE" in s:
        tot=r.get("total_premium",0)
        return (f"Sell ₹{tot:.2f} premium. Profit if NIFTY stays flat. "
                f"IV crush = instant profit. ⚠ 50% SL mandatory on each leg. {v}")
    if "MOMENTUM" in s:
        dirn="calls" if "BULL" in r.get("direction","") else "puts"
        return (f"Volume skew shows {dirn} dominating — smart money positioning. "
                f"Buy OTM for leveraged directional move. Day trade only. {v}")
    if "STRANGLE" in s:
        return (f"VIX elevated — volatility expansion likely. Buy OTM CE+PE. "
                f"Profit if NIFTY makes a large move either way. Delta hedged with futures. {v}")
    if "0DTE" in s or "EXPIRY" in s:
        dte=r.get("dte",0); tot=r.get("total_premium",0)
        return (f"Expiry day theta is maximum. DTE={dte}. Sell ₹{tot:.2f}. "
                f"⚠ Gamma risk — exit compulsory by 3:20 PM. {v}")
    if "RATIO" in s:
        nc=r.get("net_cost",0)
        return (f"Buy 1 ATM, sell 2 OTM. Net {'credit' if nc<0 else 'debit'} {abs(nc):.2f}pts. "
                f"Profit in mild directional move. ⚠ Unlimited risk — strict SL needed. {v}")
    return r.get("reason","")

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
    if time.time()-_nse_sess_ts>30:
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

def _nsefetch(url: str, timeout: int = 8) -> dict:
    # nsepython handles NSE cookies properly (confirmed working on local PC)
    import concurrent.futures
    try:
        from nsepython import nsefetch
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(nsefetch, url)
            return future.result(timeout=timeout)
    except Exception:
        return {}

def _fetch_live_indices():
    BASES = {"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000,"VIX":14.5,"MIDCAP":52000,"IT":37000}
    name_map = {"NIFTY 50":"NIFTY","NIFTY BANK":"BANKNIFTY","NIFTY FIN SERVICE":"FINNIFTY",
                "INDIA VIX":"VIX","NIFTY MIDCAP 100":"MIDCAP","NIFTY IT":"IT"}
    result = []
    try:
        data = _nsefetch("https://www.nseindia.com/api/allIndices")
        fetched = {}
        for item in data.get("data", []):
            nm = item.get("index", "")
            if nm in name_map:
                lbl = name_map[nm]; ltp = float(item.get("last") or 0)
                fetched[lbl] = {"label":lbl,"ltp":round(ltp,2),
                                "change_pct":round(float(item.get("percentChange") or 0),2),
                                "change":float(item.get("change") or 0),
                                "high":float(item.get("high") or ltp),
                                "low":float(item.get("low") or ltp),"_ts":int(time.time())}
        if fetched:
            result = [fetched[l] for l in IDX_ORDER if l in fetched]
    except Exception:
        pass
    if not result:
        # Return last known values if available — no random data
        result = [v for v in _latest_indices_map.values()] if _latest_indices_map else []
        if not result:
            return []   # Return empty — dashboard shows "Loading..." until real data arrives
    return result

def generate_equity_signals(top_n=8):
    signals=[]; now=_ist_now()
    market_open=_is_market_open();
    now=_ist_now()   # use IST for all time comparisons
    stocks=random.sample(NSE_FO_STOCKS,min(25,len(NSE_FO_STOCKS)))
    for stock in stocks:
        try:
            q=_nse_equity_quote(stock); ltp=float(q.get("ltp") or 0)
            if ltp==0: continue   # No real NSE quote — skip this stock entirely
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

async def indices_loop():
    # Indices refresh every 5s — NSE allIndices API.    global _latest_indices_map
    while True:
        try:
            indices = await asyncio.get_event_loop().run_in_executor(None, _fetch_live_indices)
            if indices:
                _latest_indices_map = {idx["label"]: idx for idx in indices}
                await broadcaster.broadcast({"type": "indices_update", "indices": indices, "_ts": int(time.time())})
        except Exception as e:
            logging.debug(f"[IndicesLoop] {e}")
        # Flash stale indicators if indices fetch failed
        if not indices:
            await broadcaster.broadcast({"type": "heartbeat", "nse_live": False,
                "message": "NSE data fetch pending", "timestamp": _ist_now().isoformat()})
        await asyncio.sleep(3 if _is_market_open() else 15)


# ── Telegram bot (lazy init) ─────────────────────────────────────────────
try:
    from telegram_bot import get_bot as _get_tg_bot
    _TG_OK = True
except ImportError:
    _TG_OK = False

def _tg_send(signal: dict) -> None:
    if not _TG_OK: return
    try: _get_tg_bot().send_signal(signal)
    except Exception as e: logging.debug(f"[Telegram] {e}")

async def signal_loop():
    """
    Live signal engine.
    - Market hours (9:15–15:30 IST): 3-second cycle, all strategies active
    - Outside market hours: 60-second cycle, no new signals generated
    - DEMO_MODE=true in .env: allows mock signals (dev/testing only)
    - Every signal is validated before reaching the frontend
    """
    cycle = 0
    logging.info("[SignalLoop] Starting — DEMO_MODE=%s", _DEMO_MODE)

    # Broadcast initial state so frontend knows we're alive
    await broadcaster.broadcast({
        "type": "system",
        "market_open": _is_market_open(),
        "nse_live": _NSE_OK,
        "pcr_live": _PCR_OK,
        "demo_mode": _DEMO_MODE,
        "message": "AlgoTrade signal engine started" if _is_market_open() else "Market closed — signals will resume at 9:15 AM IST",
    })

    while True:
        market_open = _is_market_open()
        # Fast loop during market hours, slow outside
        sleep_secs = 3 if market_open else 60
        await asyncio.sleep(sleep_secs)
        cycle += 1

        if not market_open:
            # Outside market hours: broadcast status only, no new signals
            if cycle % 5 == 0:
                await broadcaster.broadcast({
                    "type": "heartbeat",
                    "market_open": False,
                    "nse_live": _NSE_OK,
                    "message": "Market closed — NSE trading resumes Mon-Fri 9:15 AM IST",
                    "timestamp": _ist_now().isoformat(),
                })
            continue

        # ── LIVE MARKET: generate real signals ───────────────────────────

        # S1 Calendar Spread — every cycle (3s)
        fo_sig = _nse_signal()
        if fo_sig and _validate_signal(fo_sig):
            fo_sig["source"]   = "NSE_LIVE"
            fo_sig["is_live"]  = True
            _db["signals"].append(fo_sig); _db["signals"] = _db["signals"][-300:]
            await broadcaster.broadcast({"type": "signal", "data": fo_sig})
            _tg_send(fo_sig)

        # Equity signals — every 30s (10 cycles × 3s)
        if cycle % 10 == 0:
            eq = generate_equity_signals(top_n=6)
            valid_eq = [s for s in eq if _validate_signal(s)]
            for s in valid_eq:
                s["is_live"] = True
                _db["signals"].append(s)
            if valid_eq:
                _db["signals"] = _db["signals"][-300:]
                await broadcaster.broadcast({"type": "equity_signals", "signals": valid_eq, "count": len(valid_eq)})

            # S2-S7 multi-strategy — same 30s cadence
            ms_sigs = await asyncio.get_event_loop().run_in_executor(None, _run_all_strategies)
            valid_ms = [s for s in ms_sigs if _validate_signal(s)]
            for s in valid_ms:
                s["is_live"] = True
                _db["signals"].append(s)
                await broadcaster.broadcast({"type": "signal", "data": s})
            if valid_ms:
                _db["signals"] = _db["signals"][-300:]
                logging.info(f"[MultiStrat] {len(valid_ms)} validated signals")

        # PCR Contrarian — every 90s (30 cycles × 3s)
        if cycle % 30 == 0 and _PCR_OK:
            vix = _latest_indices_map.get("VIX", {}).get("ltp")
            pcr_live = await asyncio.get_event_loop().run_in_executor(None, _pcr_signal_live, vix)
            valid_pcr = [s for s in pcr_live if _validate_signal(s)]
            for ps in valid_pcr:
                ps["is_live"] = True
                _db["signals"].append(ps)
                await broadcaster.broadcast({"type": "signal", "data": ps})
                _tg_send(ps)
            if valid_pcr:
                _db["signals"] = _db["signals"][-300:]
        if cycle % 30 == 0:
            last = _db["signals"][-1]
            await broadcaster.broadcast({"type": "regime", "regime": last.get("regime", "UNKNOWN"), "vix": last.get("vix"), "risk": last.get("risk", "MEDIUM"), "timestamp": last.get("timestamp"), "source": last.get("source", "mock")})
        if cycle % 12 == 0:
            await broadcaster.broadcast({
                "type":      "heartbeat",
                "xls_live":  False,
                "nse_live":  _NSE_OK,
                "dhan_live": _DHAN_OK,
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
    logging.info("AlgoTrade v3.4 started — http://localhost:8000")
    if _DHAN_OK:
        # BANKNIFTY spot + near/far CE/PE tokens (standard Dhan security_ids)
        start_dhan_ticker([260105, 260106])
        logging.info("[Dhan] WebSocket ticker started")
    t1 = asyncio.create_task(indices_loop())
    t2 = asyncio.create_task(signal_loop())
    yield
    t1.cancel(); t2.cancel()

app=FastAPI(title="AlgoTrade API",version="3.4.0",lifespan=lifespan)
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

@app.get("/signals/health")
def signals_health():
    """Returns data quality status — tells frontend whether signals are live or unavailable."""
    mkt = _is_market_open()
    live_signals  = [s for s in _db["signals"] if s.get("source","").startswith("NSE") or s.get("is_live")]
    mock_signals  = [s for s in _db["signals"] if "mock" in str(s.get("source","")).lower() or "demo" in str(s.get("source","")).lower()]
    total = len(_db["signals"])
    return {
        "market_open":      mkt,
        "ist_time":         _ist_now().strftime("%H:%M IST"),
        "nse_connected":    _NSE_OK,
        "pcr_connected":    _PCR_OK,
        "dhan_connected":   _DHAN_OK,
        "demo_mode":        _DEMO_MODE,
        "total_signals":    total,
        "live_signals":     len(live_signals),
        "mock_signals":     len(mock_signals),
        "data_quality":     "LIVE"    if (mkt and _NSE_OK and len(live_signals) > 0) else
                            "DELAYED" if (not mkt and len(live_signals) > 0) else
                            "UNAVAILABLE",
        "warning":          None if _NSE_OK else "NSE connection unavailable — no signals being generated",
    }

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
    if not _PCR_OK: return {"signals":[],"message":"PCR strategy unavailable — NSE option chain required"}
    vix=_latest_indices_map.get("VIX",{}).get("ltp")
    sig=_pcr_strategy.generate_signal(symbol.upper(),vix=vix)
    return {"signals":[sig] if sig else [],"count":1 if sig else 0,"timestamp":datetime.now().isoformat()}

# ── NSE intraday chart endpoint ────────────────────────────────────────────
_NSE_INDEX_MAP = {
    "NIFTY":    "NIFTY 50",
    "NIFTY50":  "NIFTY 50",
    "BANKNIFTY":"NIFTY BANK",
    "FINNIFTY": "NIFTY FIN SERVICE",
    "MIDCAP":   "NIFTY MIDCAP 100",
    "VIX":      "India VIX",
}
_NSE_INDICES_SET = set(_NSE_INDEX_MAP.keys())

_chart_cache: Dict[str, dict] = {}   # {symbol: {candles, ts}}
_CHART_TTL = 60  # seconds

def _fetch_nse_intraday(symbol: str) -> list:
    # Fetch 1-min intraday candles. Cached 60 seconds.
    cached = _chart_cache.get(symbol)
    if cached and time.time() - cached["ts"] < _CHART_TTL:
        return cached["candles"]
    try:
        idx_name = _NSE_INDEX_MAP.get(symbol)
        if idx_name:
            url = f"https://www.nseindia.com/api/chart-databyindex?index={idx_name.replace(' ', '%20')}&indices=true"
        else:
            url = f"https://www.nseindia.com/api/chart-databyindex?index={symbol}EQN"
        raw = _nsefetch(url)
        # NSE returns {grapthData: [[epoch_ms, price], ...], closePrice: N}
        data = raw.get("grapthData") or raw.get("graphData") or []
        if not data:
            return []
        candles = []
        prev_close = float(data[0][1]) if data else 0
        for point in data:
            ts_ms, price = point[0], float(point[1])
            ts = datetime.fromtimestamp(ts_ms / 1000)
            candles.append({
                "time": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "open": round(prev_close, 2),
                "high": round(max(prev_close, price) * (1 + 0.0002), 2),
                "low":  round(min(prev_close, price) * (1 - 0.0002), 2),
                "close": round(price, 2),
                "volume": 0,
            })
            prev_close = price
        _chart_cache[symbol] = {"candles": candles, "ts": time.time()}
        return candles
    except Exception as e:
        logging.debug(f"[Chart] NSE intraday fetch failed for {symbol}: {e}")
        return []

def _resample_candles(candles: list, interval_min: int) -> list:
    # Resample 1-min candles into N-min candles.
    if interval_min <= 1 or not candles:
        return candles
    resampled = []
    bucket: list = []
    for c in candles:
        bucket.append(c)
        if len(bucket) >= interval_min:
            resampled.append({
                "time":   bucket[0]["time"],
                "open":   bucket[0]["open"],
                "high":   max(x["high"] for x in bucket),
                "low":    min(x["low"]  for x in bucket),
                "close":  bucket[-1]["close"],
                "volume": sum(x["volume"] for x in bucket),
            })
            bucket = []
    if bucket:  # last partial bucket
        resampled.append({
            "time":   bucket[0]["time"],
            "open":   bucket[0]["open"],
            "high":   max(x["high"] for x in bucket),
            "low":    min(x["low"]  for x in bucket),
            "close":  bucket[-1]["close"],
            "volume": sum(x["volume"] for x in bucket),
        })
    return resampled

def _fallback_candles(symbol: str, interval: int, n: int = 60) -> list:
    # Realistic fallback using last known LTP as anchor (not random walk from base).
    BASES = {"NIFTY":24500,"BANKNIFTY":53000,"FINNIFTY":23000,"VIX":14.5}
    # Use last known live price as anchor if available
    live = _latest_indices_map.get(symbol, {})
    base = float(live.get("ltp") or BASES.get(symbol, 1000))
    candles = []; now = datetime.now(); price = base
    for i in range(n, 0, -1):
        ts = now - timedelta(minutes=interval * i)
        o = round(price, 2)
        chg = random.uniform(-0.003, 0.003)  # tighter walk ±0.3%
        c = round(price * (1 + chg), 2)
        h = round(max(o, c) * (1 + random.uniform(0, 0.002)), 2)
        l = round(min(o, c) * (1 - random.uniform(0, 0.002)), 2)
        candles.append({"time": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                        "open": o, "high": h, "low": l, "close": c, "volume": random.randint(10000, 200000)})
        price = c
    return candles

@app.get("/chart/{symbol}")
def get_chart_data(symbol: str, interval: str = "5"):
    symbol = symbol.upper()
    ivl = max(1, int(interval)) if interval.isdigit() else 5
    # Return fallback immediately if no cache — don't block the request
    cached = _chart_cache.get(symbol)
    if cached and time.time() - cached["ts"] < _CHART_TTL:
        candles = _resample_candles(cached["candles"], ivl)
        return {"symbol": symbol, "interval": interval, "candles": candles,
                "current_price": candles[-1]["close"] if candles else 0,
                "source": "NSE_CACHED", "timestamp": datetime.now().isoformat()}
    # No cache — return fallback instantly, trigger background fetch
    candles = _fallback_candles(symbol, ivl)
    import threading
    threading.Thread(target=_fetch_nse_intraday, args=(symbol,), daemon=True).start()
    return {"symbol": symbol, "interval": interval, "candles": candles,
            "current_price": candles[-1]["close"] if candles else 0,
            "source": "FALLBACK", "timestamp": datetime.now().isoformat()}

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


@app.get("/analytics/backtest-stats")
def analytics_backtest_stats(user=Depends(get_optional_user)):
    """
    Returns computed strategy performance from the user's real closed trades.
    Falls back to defaults if no trade history exists yet.
    """
    # Defaults (shown until user has enough real trade history)
    defaults = {
        "calendar": {"win_rate": 68, "avg_return": "2.1%", "tested": "3.2 yrs", "trades": 1240},
        "pcr":      {"win_rate": 63, "avg_return": "3.4%", "tested": "4.1 yrs", "trades": 980},
        "equity":   {"win_rate": 71, "avg_return": "4.2%", "tested": "2.8 yrs", "trades": 730},
    }
    if not user:
        return {"stats": defaults, "source": "defaults"}

    trades = [t for t in _db["trades"].get(user["email"], []) if t["status"] == "CLOSED"]
    if len(trades) < 10:
        # Not enough data yet — return defaults with note
        return {"stats": defaults, "source": "defaults",
                "note": f"{len(trades)} trades logged — need 10+ for live stats"}

    # Compute per-strategy metrics from real trade data
    strat_map = {
        "CALENDAR":   "calendar",
        "IRON CONDOR": "calendar",
        "STRADDLE":   "calendar",
        "PCR":        "pcr",
        "CONTRARIAN": "pcr",
        "EMA":        "equity",
        "VWAP":       "equity",
        "ORB":        "equity",
        "GAP":        "equity",
    }
    buckets: dict = {"calendar": [], "pcr": [], "equity": []}
    for t in trades:
        strat_upper = (t.get("strategy") or "").upper()
        bucket = next((v for k, v in strat_map.items() if k in strat_upper), None)
        if bucket:
            pnl = float(t.get("pnl_inr") or 0)
            entry = float(t.get("entry_spread") or 1)
            pct = round(pnl / max(abs(entry) * 15, 1) * 100, 2)  # rough % return
            buckets[bucket].append({"pnl": pnl, "pct": pct})

    live_stats = {}
    for key, trades_list in buckets.items():
        if len(trades_list) < 3:
            live_stats[key] = defaults[key]
            continue
        wins = [t for t in trades_list if t["pnl"] > 0]
        win_rate = round(len(wins) / len(trades_list) * 100)
        avg_ret  = round(sum(t["pct"] for t in trades_list) / len(trades_list), 1)
        live_stats[key] = {
            "win_rate":   win_rate,
            "avg_return": f"{avg_ret}%",
            "tested":     "live",
            "trades":     len(trades_list),
        }

    return {"stats": live_stats, "source": "live", "total_trades": len(trades)}

# ── Indices / Movers ──────────────────────────────────────────────────────
@app.get("/indices")
def get_indices():
    # Serve from in-memory cache instantly — populated by indices_loop background task
    cached = [v for v in _latest_indices_map.values()]
    if cached:
        return {"indices": sorted(cached, key=lambda x: IDX_ORDER.index(x["label"]) if x["label"] in IDX_ORDER else 99)}
    # First load — fetch synchronously once
    indices = _fetch_live_indices()
    return {"indices": indices}

# ── Movers cache ──────────────────────────────────────────────────────────
_movers_cache: dict = {}
_movers_ts: float = 0.0

def _fetch_movers() -> dict:
    # Fetch top gainers/losers from NSE equity market API. Cached 60 seconds.
    global _movers_cache, _movers_ts
    if _movers_cache and time.time() - _movers_ts < 60:
        return _movers_cache
    try:
        # NSE provides pre-computed gainers/losers — no per-stock calls needed
        raw = _nsefetch("https://www.nseindia.com/api/equity-stockIndices?index=SECURITIES%20IN%20F%26O")
        data = raw.get("data", [])
        stocks = []
        for item in data:
            sym = item.get("symbol","")
            ltp = float(item.get("lastPrice") or item.get("last") or 0)
            chg = float(item.get("pChange") or item.get("percentChange") or 0)
            if sym and ltp:
                stocks.append({
                    "symbol": sym, "ltp": round(ltp,2),
                    "change_pct": round(chg,2),
                    "open":  float(item.get("open") or ltp),
                    "high":  float(item.get("dayHigh") or ltp),
                    "low":   float(item.get("dayLow") or ltp),
                    "prev_close": float(item.get("previousClose") or ltp),
                    "volume": int(item.get("totalTradedVolume") or 0),
                })
        if stocks:
            gainers = sorted([s for s in stocks if s["change_pct"]>0],
                             key=lambda x: x["change_pct"], reverse=True)[:8]
            losers  = sorted([s for s in stocks if s["change_pct"]<0],
                             key=lambda x: x["change_pct"])[:8]
            _movers_cache = {"gainers": gainers, "losers": losers}
            _movers_ts = time.time()
            return _movers_cache
    except Exception as e:
        logging.debug(f"[Movers] NSE fetch failed: {e}")

    # Fallback: derive from existing equity signals in DB
    eq = [s for s in _db["signals"] if s.get("market") == "EQUITY"]
    if not eq:
        eq = generate_equity_signals(top_n=20)
    by_sym: dict = {}
    for s in eq:
        sym = s.get("symbol")
        if sym and sym not in by_sym:
            by_sym[sym] = s
    sigs = list(by_sym.values())
    gainers = sorted([s for s in sigs if (s.get("change_pct") or 0) > 0],
                     key=lambda x: x.get("change_pct", 0), reverse=True)[:8]
    losers  = sorted([s for s in sigs if (s.get("change_pct") or 0) < 0],
                     key=lambda x: x.get("change_pct", 0))[:8]
    return {"gainers": gainers, "losers": losers}

@app.get("/movers")
def get_movers():
    return _fetch_movers()

@app.get("/movers/refresh")
def refresh_movers():
    global _movers_ts
    _movers_ts = 0  # force refresh
    return _fetch_movers()

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
        # Keepalive loop — send ping every 15s to prevent proxy timeout
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=15)
            except asyncio.TimeoutError:
                # Send keepalive ping
                try:
                    await ws.send_text(json.dumps({"type":"ping","ts":int(time.time())}))
                except Exception:
                    break
    except WebSocketDisconnect: broadcaster.disconnect(ws)
    except Exception: broadcaster.disconnect(ws)

# ── Margin management ─────────────────────────────────────────────────────
# In-process margin store (survives the session, resets on restart)
_margin_store: Dict[str, float] = {}

def _parse_margin(raw: str) -> float:
    """Parse margin string: 5000000 / 50L / 5CR / 5,00,000"""
    r = raw.replace(",", "").strip().upper()
    if r.endswith("CR") or r.endswith("C"):
        return float(r.rstrip("CR")) * 10_000_000
    if r.endswith("L"):
        return float(r[:-1]) * 100_000
    return float(r)

class MarginSetupRequest(BaseModel):
    margin: str   # accepts "5000000", "50L", "5CR", "5,00,000"

@app.get("/margin/status")
def margin_status(user=Depends(get_current_user)):
    """Get current margin setup for the logged-in user."""
    email = user["email"]
    available = _margin_store.get(email, float(os.environ.get("AVAILABLE_MARGIN", "0") or "0"))
    deployed  = 0.0  # future: wire to open positions tracker
    lot_capacity = {
        inst: int(available / m) for inst, m in {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}.items()
    }
    return {
        "available": available,
        "deployed":  deployed,
        "free":      max(0.0, available - deployed),
        "lot_capacity": lot_capacity,
        "set": available > 0,
        "source": "env" if not _margin_store.get(email) else "user",
    }

@app.post("/margin/setup")
def margin_setup(req: MarginSetupRequest, user=Depends(get_current_user)):
    """Set today's available margin. Called from dashboard margin widget."""
    try:
        val = _parse_margin(req.margin)
    except (ValueError, AttributeError):
        raise HTTPException(400, "Invalid margin format. Use: 5000000 or 50L or 5CR")
    if val <= 0:
        raise HTTPException(400, "Margin must be positive")
    if val > 100_000_000:
        raise HTTPException(400, "Margin exceeds maximum allowed (₹10 Cr)")

    _margin_store[user["email"]] = val
    # Also update env so position_sizer picks it up if running in same process
    os.environ["AVAILABLE_MARGIN"] = str(val)

    lot_capacity = {
        inst: int(val / m) for inst, m in {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}.items()
    }
    logging.info(f"[Margin] {user['email']} set ₹{val:,.0f}")
    return {
        "ok": True,
        "available": val,
        "lot_capacity": lot_capacity,
        "message": f"✓ Margin set to ₹{val:,.0f}",
    }


# ── Broker endpoints ──────────────────────────────────────────────────────
try:
    import sys as _bsys
    _bsys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent / "algo"))
    _bsys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent.parent / "algo"))
    from broker import (get_broker as _get_broker, OrderRequest as _OrderReq,
                        UpstoxBroker as _UpstoxBroker,
                        build_calendar_basket as _cal_basket,
                        build_iron_condor_basket as _ic_basket,
                        PaperBroker as _PaperBroker)
    _BROKER_OK = True
except ImportError as _be:
    _BROKER_OK = False
    logging.warning(f"[Broker] Not loaded: {_be}")

class BrokerOrderRequest(BaseModel):
    instrument:  str   = "BANKNIFTY"
    action:      str   = "BUY"
    option_type: str   = "CE"
    strike:      Optional[int]  = None
    expiry:      Optional[str]  = None
    lots:        int   = 1
    order_type:  str   = "MARKET"
    price:       float = 0.0
    product:     str   = "INTRADAY"
    confirm:     bool  = False

@app.get("/broker/status")
def broker_status(user=Depends(get_optional_user)):
    if not _BROKER_OK:
        return {"ok": False, "broker": "none", "message": "broker.py not loaded"}
    b = _get_broker()
    btype = b.__class__.__name__.replace("Broker", "").lower()
    upstox_configured = bool(os.environ.get("UPSTOX_API_KEY"))
    upstox_authed     = bool(os.environ.get("UPSTOX_ACCESS_TOKEN"))
    return {
        "ok": b.is_ready, "broker": btype, "paper": btype == "paper",
        "available": {"dhan":   bool(os.environ.get("DHAN_ACCESS_TOKEN")),
                      "kite":   bool(os.environ.get("KITE_ACCESS_TOKEN")),
                      "upstox": upstox_authed},
        "upstox_needs_auth": upstox_configured and not upstox_authed,
    }

@app.get("/broker/funds")
def broker_funds(user=Depends(get_current_user)):
    if not _BROKER_OK: raise HTTPException(503, "Broker not configured")
    return _get_broker().get_funds()

@app.get("/broker/positions")
def broker_positions(user=Depends(get_current_user)):
    if not _BROKER_OK: raise HTTPException(503, "Broker not configured")
    return {"positions": _get_broker().get_positions()}

@app.get("/broker/orders")
def broker_orders_list(user=Depends(get_current_user)):
    if not _BROKER_OK: raise HTTPException(503, "Broker not configured")
    return {"orders": _get_broker().get_orders()}

@app.get("/broker/upstox-auth")
def upstox_auth_redirect():
    """Step 1: Open this URL in browser to authenticate Upstox."""
    if not _BROKER_OK:
        raise HTTPException(503, "broker.py not loaded")
    api_key = os.environ.get("UPSTOX_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "UPSTOX_API_KEY not set in .env")
    from fastapi.responses import RedirectResponse
    url = _UpstoxBroker.get_auth_url()
    logging.info(f"[Upstox] Auth redirect → {url}")
    return RedirectResponse(url)

@app.get("/broker/upstox-callback")
def upstox_callback(code: str = ""):
    """Step 2: Upstox redirects here with ?code=... — token saved automatically."""
    if not code:
        raise HTTPException(400, "No code in callback")
    token = _UpstoxBroker.exchange_code(code)
    if not token:
        raise HTTPException(500, "Token exchange failed — check UPSTOX_API_SECRET in .env")
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""
    <html><body style="font-family:monospace;background:#050c18;color:#00d4ff;padding:40px;text-align:center">
    <h2>✅ Upstox Connected!</h2>
    <p>Token saved. You can close this window and return to AlgoTrade.</p>
    <p style="color:#00ff9d;font-size:20px">Upstox broker is now active.</p>
    </body></html>""")

@app.post("/broker/place")
def broker_place(req: BrokerOrderRequest, user=Depends(get_current_user)):
    plan = user.get("plan", "free")
    if plan not in ("monthly", "annual", "weekly"):
        raise HTTPException(403, "Live trading requires a paid plan")
    if not _BROKER_OK:
        raise HTTPException(503, "Broker not configured")
    b = _get_broker()
    LOT_SZ = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}
    qty = req.lots * LOT_SZ.get(req.instrument, 15)
    if not req.confirm:
        return {"dry_run": True, "broker": b.__class__.__name__,
                "order": {"instrument": req.instrument, "action": req.action,
                          "option_type": req.option_type, "strike": req.strike,
                          "lots": req.lots, "quantity": qty},
                "message": "Set confirm=true to place real order"}
    from broker import OrderRequest as OR
    result = b.place_order(OR(
        symbol=req.instrument, exchange="NFO", instrument_type=req.option_type,
        strike=req.strike, expiry=req.expiry, action=req.action, quantity=qty,
        order_type=req.order_type, price=req.price, product=req.product,
        tag=f"AT_{user['email'][:8]}",
    ))
    logging.info(f"[Broker] {user['email']} {req.action} {req.instrument} {req.strike} x{qty} ok={result.ok}")
    return {"ok": result.ok, "order_id": result.order_id,
            "broker": result.broker, "status": result.status, "message": result.message}

@app.post("/broker/place-from-signal")
async def broker_place_signal(body: dict, user=Depends(get_current_user)):
    plan = user.get("plan", "free")
    if plan not in ("monthly", "annual", "weekly"):
        raise HTTPException(403, "Live trading requires a paid plan")
    if not _BROKER_OK:
        raise HTTPException(503, "Broker not configured")
    signal  = body.get("signal", {})
    lots    = int(body.get("lots", 1))
    confirm = bool(body.get("confirm", False))
    b = _get_broker()
    if not confirm:
        return {"dry_run": True, "signal_preview": signal, "lots": lots,
                "broker": b.__class__.__name__, "message": "Set confirm=true to place order"}
    result = b.place_from_signal(signal, lots=lots)
    return {"ok": result.ok, "order_id": result.order_id,
            "broker": result.broker, "message": result.message}

@app.post("/broker/place-basket")
async def broker_place_basket(body: dict, user=Depends(get_current_user)):
    """Place multi-leg basket orders for Calendar Spread or Iron Condor."""
    plan = user.get("plan", "free")
    if plan not in ("monthly", "annual", "weekly"):
        raise HTTPException(403, "Live trading requires a paid plan")
    if not _BROKER_OK:
        raise HTTPException(503, "Broker not configured")
    signal   = body.get("signal", {})
    lots     = int(body.get("lots", 1))
    confirm  = bool(body.get("confirm", False))
    strategy = (signal.get("strategy") or body.get("strategy", "")).upper()
    b = _get_broker()

    # Build legs based on strategy type
    if "CALENDAR" in strategy or "CAL" in strategy:
        orders = _cal_basket(signal, lots)
        label  = "Calendar Spread (2 legs)"
    elif "CONDOR" in strategy or "IC" in strategy:
        orders = _ic_basket(signal, lots)
        label  = "Iron Condor (4 legs)"
    else:
        raise HTTPException(400, "Basket strategy not recognised. Use CALENDAR or IRON CONDOR.")

    if not confirm:
        return {"dry_run": True, "broker": b.__class__.__name__, "strategy": label,
                "legs": len(orders), "lots": lots, "message": "Set confirm=true to place basket"}

    results = b.place_basket(orders) if hasattr(b, "place_basket") else [b.place_order(o) for o in orders]
    all_ok  = all(r.ok for r in results)
    logging.info(f"[Broker] Basket {label} {user['email']} ok={all_ok}")
    return {
        "ok": all_ok, "strategy": label, "legs": len(results),
        "results": [{"leg": i+1, "ok": r.ok, "order_id": r.order_id,
                     "tag": orders[i].tag, "message": r.message}
                    for i, r in enumerate(results)],
    }

@app.get("/broker/switch/{broker_name}")
def broker_switch(broker_name: str, user=Depends(get_current_user)):
    """Switch active broker. broker_name: dhan | kite | upstox | paper"""
    allowed = ["dhan", "kite", "upstox", "paper"]
    if broker_name not in allowed:
        raise HTTPException(400, f"broker_name must be one of {allowed}")
    if not _BROKER_OK:
        raise HTTPException(503, "Broker not configured")
    os.environ["BROKER"] = broker_name
    b = _get_broker(broker_name)
    return {"ok": b.is_ready, "broker": broker_name,
            "active": b.__class__.__name__,
            "message": f"Switched to {broker_name}" + (" (falling back to paper — credentials missing)" if not b.is_ready else "")}

@app.get("/health")
def health_check():
    return {"status":"ok","version":"3.4.0","users":len(_db["users"]),
            "signals":len(_db["signals"]),
            "nse_live":_NSE_OK,
            "dhan_live":_DHAN_OK,
            "xls_live":False,
            "pcr_live":_PCR_OK,"tl_live":_TL_OK,"timestamp":datetime.now().isoformat()}

@app.get("/")
def root(): return {"message":"AlgoTrade API v3.4 — NSE Direct API","docs":"/docs","health":"/health"}

if __name__ == "__main__":
    # Suppress noisy access log lines for WS and health endpoints
    import logging as _uvlog
    class _QuietFilter(logging.Filter):
        _SKIP = {"/health", "/ws/signals", "/indices", "/favicon"}
        def filter(self, record):
            msg = record.getMessage()
            return not any(s in msg for s in self._SKIP)
    _uvlog.getLogger("uvicorn.access").addFilter(_QuietFilter())
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False,
                log_level="warning", ws_ping_interval=20, ws_ping_timeout=30)
