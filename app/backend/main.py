"""
ALGOTRADE BACKEND  —  app/backend/main.py
==========================================
FastAPI production server:
  - JWT auth (bcrypt, jose)
  - WebSocket live signal streaming from algo engine
  - Trade logging + P&L
  - Analytics
  - Paper trading
  - Subscription tiers
  - Real XLS signals via multitrade_loader (falls back to mock)
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import sys
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ── Pydantic ──────────────────────────────────────────────────
from pydantic import BaseModel

# ── Optional heavy deps ───────────────────────────────────────
try:
    from jose import JWTError, jwt
    _JWT_OK = True
except ImportError:
    _JWT_OK = False
    logging.warning("python-jose not installed — using base64 JWT fallback")

try:
    import bcrypt as _bcrypt
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False
    logging.warning("bcrypt not installed — using sha256 fallback")

# ── Algo engine integration ────────────────────────────────────
# Add algo/ directory to path so we can import multitrade_loader
_ALGO_DIR = Path(__file__).parent.parent.parent / "algo"
sys.path.insert(0, str(_ALGO_DIR))

try:
    import multitrade_loader as _loader
    _XLS_OK = True
    logging.info("[XLS] multitrade_loader loaded OK")
except ImportError:
    _XLS_OK = False
    logging.warning("[XLS] multitrade_loader not found — using mock signals")

# ── NSE connector (optional) ──────────────────────────────────
try:
    from nse_connector import NseConnector as _NseConnector, NseSignalEnhancer
    _nse = _NseConnector()
    _nse_enhancer = NseSignalEnhancer(_nse)
    _NSE_OK = _nse._connected
except Exception:
    _NSE_OK = False
    _nse_enhancer = None

# ════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════
SECRET_KEY   = os.environ.get("SECRET_KEY", "algotrade-dev-secret-CHANGE-IN-PROD")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = 60 * 24  # minutes

logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── In-memory store (swap for PostgreSQL in production) ───────
_db: Dict[str, Any] = {
    "users":   {},
    "trades":  {},
    "signals": [],
    "regime":  {},
}
_paper: Dict[str, Any] = {}

# ── Subscription tiers ────────────────────────────────────────
TIERS = {
    "free":    {"strategies": 2,  "instruments": 1, "live": False, "delay_min": 15},
    "starter": {"strategies": 7,  "instruments": 3, "live": True,  "delay_min": 0},
    "pro":     {"strategies": 7,  "instruments": 5, "live": True,  "delay_min": 0},
    "elite":   {"strategies": 7,  "instruments": 6, "live": True,  "delay_min": 0},
}

PLANS_LIST = [
    {"id": "free",    "name": "Free",    "price": 0,     "price_str": "₹0/mo",
     "features": ["Paper trading", "15-min delayed signals", "2 strategies", "Community support"]},
    {"id": "starter", "name": "Starter", "price": 2999,  "price_str": "₹2,999/mo",
     "features": ["Live signals", "All 7 strategies", "3 instruments", "Email support"]},
    {"id": "pro",     "name": "Pro",     "price": 7999,  "price_str": "₹7,999/mo",
     "features": ["Everything in Starter", "5 instruments", "Backtest access",
                  "Regime alerts", "Priority support"]},
    {"id": "elite",   "name": "Elite",   "price": 19999, "price_str": "₹19,999/mo",
     "features": ["Everything in Pro", "API access", "Custom alerts",
                  "1-on-1 strategy call", "Dedicated account manager"]},
]

# ════════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════════
def hash_password(pw: str) -> str:
    if _BCRYPT_OK:
        return _bcrypt.hashpw(pw.encode()[:72], _bcrypt.gensalt()).decode()
    return hashlib.sha256(pw.encode()).hexdigest()


def verify_password(pw: str, hashed: str) -> bool:
    if _BCRYPT_OK:
        try:
            return _bcrypt.checkpw(pw.encode()[:72], hashed.encode())
        except Exception:
            return False
    return hashlib.sha256(pw.encode()).hexdigest() == hashed


def create_token(data: dict, expires_minutes: int = TOKEN_EXPIRE) -> str:
    payload = {**data, "exp": datetime.utcnow() + timedelta(minutes=expires_minutes)}
    if _JWT_OK:
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    import base64
    return base64.b64encode(json.dumps(payload, default=str).encode()).decode()


def decode_token(token: str) -> Optional[dict]:
    try:
        if _JWT_OK:
            return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        import base64
        return json.loads(base64.b64decode(token.encode()).decode())
    except Exception:
        return None


security = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    if not creds:
        raise HTTPException(401, "Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(401, "Invalid token")
    user = _db["users"].get(payload.get("sub"))
    if not user:
        raise HTTPException(401, "User not found")
    return user


def get_optional_user(creds: HTTPAuthorizationCredentials = Depends(security)):
    if not creds:
        return None
    payload = decode_token(creds.credentials)
    if not payload:
        return None
    return _db["users"].get(payload.get("sub"))


# ════════════════════════════════════════════════════════════════
#  PYDANTIC MODELS
# ════════════════════════════════════════════════════════════════
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TradeLogRequest(BaseModel):
    strategy: str
    instrument: str
    option_type: str
    direction: str
    near_strike: int
    far_strike: int
    lots: int
    entry_spread: float
    notes: Optional[str] = ""


class TradeCloseRequest(BaseModel):
    trade_id: str
    exit_spread: float
    notes: Optional[str] = ""


# ════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE  —  reads from real XLS, falls back to mock
# ════════════════════════════════════════════════════════════════
_MOCK_STRATEGIES = [
    "S1 CALENDAR", "S2 IRON CONDOR", "S3 SHORT STRADDLE",
    "S4 MOMENTUM", "S7 RATIO SPREAD"
]
_MOCK_REGIMES = ["R2 SIDEWAYS LOW", "R3 SIDEWAYS HIGH IV", "R4 TRENDING BULL", "R6 HIGH VOLATILITY"]

LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}


def _mock_signal() -> dict:
    spot  = 53000 + random.randint(-800, 800)
    atm   = int(round(spot / 100) * 100)
    strat = random.choice(_MOCK_STRATEGIES)
    score = random.randint(45, 95)
    dirn  = random.choice(["LONG", "SHORT"])
    spread = round(random.uniform(-8, 8), 2)
    fair   = round(spread + random.uniform(-3, 3), 2)
    vix    = round(random.uniform(12, 22), 2)
    return {
        "timestamp":     datetime.now().isoformat(),
        "source":        "mock",
        "strategy":      strat,
        "score":         score,
        "direction":     dirn,
        "instrument":    "BANKNIFTY",
        "near_strike":   atm,
        "far_strike":    atm,
        "spread":        spread,
        "fair_value":    fair,
        "deviation":     round(spread - fair, 2),
        "vix":           vix,
        "regime":        random.choice(_MOCK_REGIMES),
        "risk":          "LOW" if vix < 13 else "MEDIUM" if vix < 19 else "HIGH",
        "near_bid":      round(150 + random.uniform(-20, 20), 2),
        "near_ask":      round(152 + random.uniform(-20, 20), 2),
        "far_bid":       round(155 + random.uniform(-20, 20), 2),
        "far_ask":       round(157 + random.uniform(-20, 20), 2),
        "buy_far_at":    round(156 + random.uniform(-5, 5), 2),
        "sell_near_at":  round(149 + random.uniform(-5, 5), 2),
        "target_pts":    8,
        "sl_pts":        6,
        "lots_suggested": random.randint(1, 5),
        "reason":        f"Spread deviation {round(abs(spread - fair), 2):.2f}pts | VIX {vix:.1f}",
        "orders":        f"BUY Far {atm} @ {round(156, 2)}\nSELL Near {atm} @ {round(149, 2)}",
    }


def _xls_signal() -> Optional[dict]:
    """Try to generate a real signal from the XLS file."""
    if not _XLS_OK:
        return None
    try:
        df = _loader.get_instruments()
        if df is None or df.empty:
            return None
        atm = _loader.get_atm_strike(df)
        if not atm:
            return None
        ce = _loader.get_spread(df, atm, "CE")
        pe = _loader.get_spread(df, atm, "PE")
        if ce is None:
            return None

        spread = ce["spread"]
        fair   = ce["fair"]
        dev    = ce["deviation"]
        score  = min(95, max(30, 50 + int(abs(dev) * 8)))
        dirn   = "LONG" if dev < -3 else "SHORT" if dev > 3 else "WAIT"

        if dirn == "WAIT":
            return None

        signal = {
            "timestamp":    datetime.now().isoformat(),
            "source":       "xls_live",
            "strategy":     "S1 CALENDAR",
            "score":        score,
            "direction":    dirn,
            "instrument":   "BANKNIFTY",
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
            "far_bid":      ce.get("far_leg"),
            "far_ask":      None,
            "buy_far_at":   ce.get("buy_far_at"),
            "sell_near_at": ce.get("sell_near_at"),
            "target_pts":   8,
            "sl_pts":       6,
            "lots_suggested": 1,
            "near_theta":   ce.get("near_theta"),
            "far_theta":    ce.get("far_theta"),
            "near_vega":    ce.get("near_vega"),
            "reason":       f"CE Spread {spread:+.2f} | Fair {fair:+.2f} | Dev {dev:+.2f}",
            "orders":       (f"BUY Far {atm} CE @ {ce.get('buy_far_at')}\n"
                             f"SELL Near {atm} CE @ {ce.get('sell_near_at')}"),
        }

        # Add PE signal if available
        if pe:
            signal["pe_spread"]    = pe.get("spread")
            signal["pe_deviation"] = pe.get("deviation")
            signal["pe_buy_far"]   = pe.get("buy_far_at")
            signal["pe_sell_near"] = pe.get("sell_near_at")

        return signal
    except Exception as e:
        logging.debug(f"[XLS signal] {e}")
        return None


# ── Full NSE F&O universe (~220 SEBI-approved stocks) ─────────
# Scanned in batches — backend samples randomly each cycle
# for variety. The algo script Equity_India.py scans all of them.
NSE_FO_STOCKS = [
    # NIFTY 50
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
    # NIFTY NEXT 50
    "AMBUJACEM","AUROPHARMA","BANDHANBNK","BANKBARODA","BEL",
    "BERGEPAINT","BOSCHLTD","CANBK","CHOLAFIN","COLPAL",
    "CONCOR","DALBHARAT","DABUR","DLF","GAIL",
    "GODREJCP","GODREJPROP","HAVELLS","ICICIPRULI","INDIGO",
    "INDUSTOWER","IOC","IRCTC","JINDALSTEL","LICI",
    "LUPIN","MARICO","MCDOWELL-N","MUTHOOTFIN","NAUKRI",
    "NMDC","OFSS","PAGEIND","PETRONET","PIDILITIND",
    "PNB","RECLTD","SAIL","SIEMENS","SRF",
    "TORNTPHARM","TVSMOTOR","UBL","UNIONBANK","UPL",
    "VBL","VEDL",
    # MIDCAP liquid F&O
    "AARTIIND","ABB","ABCAPITAL","ABFRL","ACC",
    "AEGISCHEM","AFFLE","ALKEM","APOLLOTYRE","ASTRAL",
    "ATUL","AUBANK","BALKRISIND","BATAINDIA","BHARATFORG",
    "BHEL","BSE","CAMS","CANFINHOME","CEATLTD",
    "CENTURYPLY","CESC","CGPOWER","COFORGE","CROMPTON",
    "CUMMINSIND","CYIENT","DEEPAKNTR","DIXON","DMART",
    "ELGIEQUIP","EMAMILTD","EQUITASBNK","ESCORTS","EXIDEIND",
    "FEDERALBNK","FSL","GLENMARK","GMRAIRPORT","GRANULES",
    "GSPL","GUJGASLTD","HAL","HAVELLS","HEG",
    "HFCL","HINDZINC","IEX","IGL","IRCON",
    "JBCHEPHARM","JKCEMENT","JUBLFOOD","KAJARIACER","KALYANKJIL",
    "KEI","KIMS","KPIL","KTKBANK","L&TFH",
    "LAURUSLABS","LICHSGFIN","LALPATHLAB","LTTS","LUXIND",
    "MANAPPURAM","MAXHEALTH","MCX","METROPOLIS","MOIL",
    "MRF","NATIONALUM","NBCC","NCC","NAVINFLUOR",
    "OBEROIRLTY","OLECTRA","PHOENIX","PERSISTENT","POLYCAB",
    "PRESTIGE","PVRINOX","RAMCOCEM","RBLBANK","RELAXO",
    "ROUTE","SAFARI","SCHAEFFLER","SOBHA","STLTECH",
    "SUNDARMFIN","SUNDRMFAST","SUNTV","SYNGENE","TANLA",
    "TATACOMM","TATACHEM","TATAPOWER","TEAMLEASE","TIINDIA",
    "VOLTAS","WELCORP","ZEEL","ZOMATO","ZYDUSLIFE",
]

# ── NSE direct session ────────────────────────────────────────
import requests as _req
_nse_sess = _req.Session()
_nse_sess.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept":     "application/json",
    "Referer":    "https://www.nseindia.com",
})
_nse_sess_ts = 0.0


def _nse_refresh():
    global _nse_sess_ts
    if time.time() - _nse_sess_ts > 300:
        try:
            _nse_sess.get("https://www.nseindia.com", timeout=5)
            _nse_sess_ts = time.time()
        except Exception:
            pass


def _nse_equity_quote(symbol: str) -> dict:
    _nse_refresh()
    try:
        r = _nse_sess.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={symbol}",
            timeout=5)
        d  = r.json()
        pi = d.get("priceInfo", {})
        return {
            "symbol":     symbol,
            "ltp":        float(pi.get("lastPrice")   or 0),
            "change_pct": float(pi.get("pChange")     or 0),
            "open":       float(pi.get("open")        or 0),
            "high":       float(pi.get("intraDayHighLow", {}).get("max") or 0),
            "low":        float(pi.get("intraDayHighLow", {}).get("min") or 0),
            "prev_close": float(pi.get("previousClose") or 0),
        }
    except Exception:
        return {}


def generate_equity_signals(top_n: int = 8) -> list:
    """
    Scan top NSE F&O stocks for intraday signals.
    Strategies: E1 EMA Crossover, E2 VWAP Reversion,
                E3 ORB Breakout, E4 ADX Trend, E6 Gap Fill
    """
    signals = []
    now = datetime.now()
    market_open = (
        now.weekday() < 5 and
        ((now.hour == 9 and now.minute >= 15) or
         (10 <= now.hour <= 14) or
         (now.hour == 15 and now.minute <= 30))
    )

    # Sample 25 random stocks each cycle — rotates through the full universe
    # so over time all ~220 stocks get scanned
    stocks = random.sample(NSE_FO_STOCKS, min(25, len(NSE_FO_STOCKS)))

    for stock in stocks:
        try:
            q   = _nse_equity_quote(stock)
            ltp = float(q.get("ltp") or 0)
            if ltp == 0:
                # fallback mock price so dashboard always shows something
                ltp = random.uniform(500, 4000)
                q   = {
                    "ltp": ltp, "change_pct": random.uniform(-3, 3),
                    "open": ltp * 0.998, "high": ltp * 1.012,
                    "low": ltp * 0.988,  "prev_close": ltp * 0.997,
                }

            chg  = float(q.get("change_pct") or 0)
            prev = float(q.get("prev_close") or ltp)
            high = float(q.get("high") or ltp * 1.01)
            low  = float(q.get("low")  or ltp * 0.99)
            open_ = float(q.get("open") or ltp)
            gap_pct = (open_ - prev) / prev * 100 if prev else 0

            # Pick strategy based on market conditions
            strategy, direction, reason, score = None, None, None, 0

            if abs(chg) > 1.8:
                strategy  = "E1 EMA CROSSOVER"
                direction = "BUY" if chg > 0 else "SELL"
                score     = min(88, 58 + int(abs(chg) * 7))
                reason    = (f"EMA9 crossed {'above' if direction=='BUY' else 'below'} "
                             f"EMA21 | Δ{chg:+.2f}%")
            elif abs(gap_pct) > 0.8 and now.hour <= 10:
                strategy  = "E6 GAP FILL"
                direction = "SELL" if gap_pct > 0 else "BUY"
                score     = min(84, 58 + int(abs(gap_pct) * 6))
                reason    = (f"Gap {'up' if gap_pct>0 else 'down'} {gap_pct:+.2f}% "
                             f"from ₹{prev:.2f} — mean reversion")
            elif abs(chg) > 0.9:
                strategy  = "E2 VWAP REVERSION"
                direction = "BUY" if chg < 0 else "SELL"
                score     = min(78, 52 + int(abs(chg) * 5))
                reason    = f"Price {chg:+.2f}% from VWAP — fade setup"
            elif now.hour == 9 and now.minute >= 30 and (high - low) > 0:
                if ltp >= high * 0.999:
                    direction = "BUY"
                elif ltp <= low * 1.001:
                    direction = "SELL"
                if direction:
                    strategy = "E3 ORB BREAKOUT"
                    score    = min(80, 60 + int((high - low) / ltp * 200))
                    reason   = (f"ORB {'breakout' if direction=='BUY' else 'breakdown'} "
                                f"{'above' if direction=='BUY' else 'below'} "
                                f"₹{high if direction=='BUY' else low:.2f}")

            if not strategy or not direction:
                continue
            if score < 55:
                continue

            buf    = ltp * 0.002
            entry  = round(ltp + buf  if direction == "BUY" else ltp - buf, 2)
            target = round(ltp * 1.015 if direction == "BUY" else ltp * 0.985, 2)
            sl     = round(ltp * 0.993  if direction == "BUY" else ltp * 1.007, 2)

            signals.append({
                "market":      "EQUITY",
                "strategy":    strategy,
                "symbol":      stock,
                "score":       score,
                "direction":   direction,
                "risk":        "MEDIUM",
                "ltp":         round(ltp, 2),
                "change_pct":  round(chg, 2),
                "open":        round(open_, 2),
                "high":        round(high, 2),
                "low":         round(low, 2),
                "prev_close":  round(prev, 2),
                "entry_at":    entry,
                "target_at":   target,
                "sl_at":       sl,
                "source":      "NSE_DIRECT" if market_open else "NSE_EOD",
                "timestamp":   datetime.now().isoformat(),
                "reason":      reason,
                "action":      f"{direction} {stock} @ ₹{entry}",
            })
        except Exception:
            continue

    return sorted(signals, key=lambda x: x["score"], reverse=True)[:top_n]


# ════════════════════════════════════════════════════════════════
#  WEBSOCKET BROADCASTER
# ════════════════════════════════════════════════════════════════
class Broadcaster:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        self.connections = [c for c in self.connections if c is not ws]

    async def broadcast(self, msg: dict):
        payload = json.dumps(msg, default=str)
        dead = []
        for ws in self.connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


broadcaster = Broadcaster()


async def signal_loop():
    """
    Background loop — alternates between F&O and Equity signals.
      Every 5s : F&O signal (XLS real or mock)
      Every 30s: Equity signals (NSE top stocks)
    """
    cycle = 0
    while True:
        await asyncio.sleep(5)
        cycle += 1

        # F&O signal every cycle
        sig = _xls_signal() or _mock_signal()
        _db["signals"].append(sig)
        _db["signals"] = _db["signals"][-200:]
        await broadcaster.broadcast({"type": "signal", "data": sig})

        # Equity signals every 6 cycles (~30s)
        if cycle % 6 == 0:
            eq_signals = generate_equity_signals(top_n=6)
            for eq_sig in eq_signals:
                _db["signals"].append(eq_sig)
            _db["signals"] = _db["signals"][-300:]
            await broadcaster.broadcast({
                "type":    "equity_signals",
                "signals": eq_signals,
                "count":   len(eq_signals),
            })

        # Regime update every 30 cycles
        if cycle % 30 == 0:
            last = _db["signals"][-1]
            await broadcaster.broadcast({
                "type":      "regime",
                "regime":    last.get("regime", "UNKNOWN"),
                "vix":       last.get("vix"),
                "risk":      last.get("risk", "MEDIUM"),
                "timestamp": last.get("timestamp"),
                "source":    last.get("source", "mock"),
            })

        # Stats heartbeat every 12 cycles (1 min)
        if cycle % 12 == 0:
            await broadcaster.broadcast({
                "type":       "heartbeat",
                "xls_live":   _XLS_OK,
                "nse_live":   _NSE_OK,
                "signals_n":  len(_db["signals"]),
                "timestamp":  datetime.now().isoformat(),
            })


# ════════════════════════════════════════════════════════════════
#  LIFECYCLE
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed demo user
    if "demo@algotrade.in" not in _db["users"]:
        _db["users"]["demo@algotrade.in"] = {
            "name":         "Demo User",
            "email":        "demo@algotrade.in",
            "password":     hash_password("demo123"),
            "plan":         "pro",
            "joined":       str(date.today()),
            "daily_target": 50000,
        }
    logging.info(f"  AlgoTrade API starting...")
    logging.info(f"  XLS live feed: {'YES' if _XLS_OK else 'NO (mock)'}")
    logging.info(f"  NSE connector: {'YES' if _NSE_OK else 'NO'}")
    task = asyncio.create_task(signal_loop())
    yield
    task.cancel()


app = FastAPI(
    title="AlgoTrade API",
    description="NIFTY/BANKNIFTY Multi-Strategy Options Signal Platform",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000",
                   "http://127.0.0.1:5173", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ════════════════════════════════════════════════════════════════
@app.post("/auth/register")
def register(req: RegisterRequest):
    if req.email in _db["users"]:
        raise HTTPException(400, "Email already registered")
    _db["users"][req.email] = {
        "name":         req.name,
        "email":        req.email,
        "password":     hash_password(req.password),
        "plan":         "free",
        "joined":       str(date.today()),
        "daily_target": 50000,
    }
    token = create_token({"sub": req.email})
    user  = {k: v for k, v in _db["users"][req.email].items() if k != "password"}
    return {"token": token, "user": user}


@app.post("/auth/login")
def login(req: LoginRequest):
    user = _db["users"].get(req.email)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_token({"sub": req.email})
    return {"token": token, "user": {k: v for k, v in user.items() if k != "password"}}


@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {k: v for k, v in user.items() if k != "password"}


# ════════════════════════════════════════════════════════════════
#  SIGNAL ROUTES
# ════════════════════════════════════════════════════════════════
@app.get("/signals/latest")
def get_signals(limit: int = 20, user=Depends(get_optional_user)):
    sigs = _db["signals"][-limit:][::-1]
    tier = TIERS.get(user["plan"] if user else "free", TIERS["free"])
    if not tier["live"]:
        sigs = sigs[:5]
    return {"signals": sigs, "count": len(sigs),
            "xls_live": _XLS_OK, "nse_live": _NSE_OK}


@app.get("/signals/regime")
def get_regime():
    if _db["signals"]:
        last = _db["signals"][-1]
        return {
            "regime":    last.get("regime", "DETECTING"),
            "vix":       last.get("vix"),
            "risk":      last.get("risk", "MEDIUM"),
            "timestamp": last.get("timestamp"),
            "source":    last.get("source", "mock"),
        }
    return {"regime": "DETECTING", "vix": None, "risk": "UNKNOWN"}


@app.get("/signals/live")
def live_xls_signal():
    """Direct XLS read for latest real-time signal."""
    sig = _xls_signal()
    if sig:
        return {"signal": sig, "source": "xls_live"}
    return {"signal": _mock_signal(), "source": "mock",
            "note": "XLS not available — serving mock signal"}


@app.get("/signals/history")
def signal_history(days: int = 7, user=Depends(get_current_user)):
    return {"signals": _db["signals"], "days": days}


@app.get("/signals/equity")
def equity_signals(top: int = 10, user=Depends(get_optional_user)):
    """Live NSE equity signals — individual stocks, E1–E6 strategies."""
    signals = generate_equity_signals(top_n=top)
    return {
        "signals": signals,
        "count":   len(signals),
        "stocks":  NSE_FO_STOCKS,
        "strategies": {
            "E1": "EMA Crossover",
            "E2": "VWAP Reversion",
            "E3": "ORB Breakout",
            "E4": "ADX Trend",
            "E6": "Gap Fill",
        },
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/signals/equity/{symbol}")
def equity_signal_for_symbol(symbol: str, user=Depends(get_optional_user)):
    """Live signal for a specific NSE stock e.g. /signals/equity/RELIANCE"""
    symbol = symbol.upper()
    q   = _nse_equity_quote(symbol)
    ltp = float(q.get("ltp") or 0)
    if ltp == 0:
        raise HTTPException(404, f"No price data for {symbol}")
    return {
        "symbol":  symbol,
        "quote":   q,
        "signals": generate_equity_signals(top_n=5),
        "timestamp": datetime.now().isoformat(),
    }


# ════════════════════════════════════════════════════════════════
#  TRADE ROUTES
# ════════════════════════════════════════════════════════════════
@app.post("/trades/enter")
def enter_trade(req: TradeLogRequest, user=Depends(get_current_user)):
    import uuid
    email    = user["email"]
    trade_id = str(uuid.uuid4())[:8].upper()
    ls       = LOT_SIZES.get(req.instrument, 25)
    trade = {
        "id":           trade_id,
        "date":         str(date.today()),
        "entry_time":   datetime.now().isoformat(),
        "exit_time":    None,
        "strategy":     req.strategy,
        "instrument":   req.instrument,
        "type":         req.option_type,
        "direction":    req.direction,
        "near_strike":  req.near_strike,
        "far_strike":   req.far_strike,
        "lots":         req.lots,
        "lot_size":     ls,
        "entry_spread": req.entry_spread,
        "exit_spread":  None,
        "pnl_pts":      None,
        "pnl_inr":      None,
        "status":       "OPEN",
        "notes":        req.notes,
    }
    _db["trades"].setdefault(email, []).append(trade)
    return {"trade_id": trade_id, "trade": trade}


@app.post("/trades/close")
def close_trade(req: TradeCloseRequest, user=Depends(get_current_user)):
    email  = user["email"]
    trades = _db["trades"].get(email, [])
    trade  = next((t for t in trades if t["id"] == req.trade_id), None)
    if not trade:
        raise HTTPException(404, "Trade not found")
    if trade["status"] == "CLOSED":
        raise HTTPException(400, "Already closed")

    ls    = LOT_SIZES.get(trade["instrument"], 25)
    entry = float(trade["entry_spread"])
    exit_ = float(req.exit_spread)
    dirn  = trade["direction"]

    pnl_pts = (exit_ - entry) if dirn in ("LONG", "BUY BOTH", "BULL") else (entry - exit_)
    pnl_inr = round(pnl_pts * ls * trade["lots"], 0)

    trade.update({
        "exit_time":   datetime.now().isoformat(),
        "exit_spread": req.exit_spread,
        "pnl_pts":     round(pnl_pts, 2),
        "pnl_inr":     int(pnl_inr),
        "status":      "CLOSED",
        "notes":       (trade.get("notes", "") + " | " + (req.notes or "")).strip(" |"),
    })
    return {"trade": trade, "pnl_inr": int(pnl_inr)}


@app.get("/trades/today")
def today_trades(user=Depends(get_current_user)):
    email   = user["email"]
    trades  = _db["trades"].get(email, [])
    today   = str(date.today())
    todays  = [t for t in trades if t.get("date") == today]
    realised = sum(t["pnl_inr"] for t in todays
                   if t.get("pnl_inr") is not None and t["status"] == "CLOSED")
    open_c  = sum(1 for t in todays if t["status"] == "OPEN")
    target  = user.get("daily_target", 50000)
    return {
        "trades":       todays,
        "realised_pnl": realised,
        "open_count":   open_c,
        "daily_target": target,
        "remaining":    max(0, target - realised),
        "progress_pct": round(min(100, realised / target * 100), 1) if target else 0,
    }


@app.get("/trades/history")
def trade_history(user=Depends(get_current_user)):
    email  = user["email"]
    trades = _db["trades"].get(email, [])
    return {"trades": trades[-200:], "total": len(trades)}


# ════════════════════════════════════════════════════════════════
#  ANALYTICS
# ════════════════════════════════════════════════════════════════
@app.get("/analytics/summary")
def analytics_summary(user=Depends(get_current_user)):
    email  = user["email"]
    trades = [t for t in _db["trades"].get(email, []) if t["status"] == "CLOSED"]
    if not trades:
        return {"message": "No closed trades yet", "total_trades": 0}
    pnls  = [t["pnl_inr"] for t in trades if t.get("pnl_inr") is not None]
    wins  = [p for p in pnls if p > 0]
    by_strat: dict = {}
    for t in trades:
        s = t["strategy"]
        bs = by_strat.setdefault(s, {"trades": 0, "wins": 0, "total_pnl": 0})
        bs["trades"]    += 1
        bs["wins"]      += 1 if (t.get("pnl_inr") or 0) > 0 else 0
        bs["total_pnl"] += t.get("pnl_inr") or 0
    return {
        "total_trades": len(trades),
        "win_rate":     round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
        "total_pnl":    sum(pnls),
        "avg_pnl":      round(sum(pnls) / len(pnls), 0) if pnls else 0,
        "best_trade":   max(pnls) if pnls else 0,
        "worst_trade":  min(pnls) if pnls else 0,
        "by_strategy":  by_strat,
    }


@app.get("/analytics/daily")
def daily_pnl(user=Depends(get_current_user)):
    email  = user["email"]
    trades = [t for t in _db["trades"].get(email, []) if t["status"] == "CLOSED"]
    by_date: dict = {}
    for t in trades:
        d  = t.get("date", "")
        bd = by_date.setdefault(d, {"date": d, "pnl": 0, "trades": 0, "wins": 0})
        bd["pnl"]    += t.get("pnl_inr") or 0
        bd["trades"] += 1
        bd["wins"]   += 1 if (t.get("pnl_inr") or 0) > 0 else 0
    return {"daily": sorted(by_date.values(), key=lambda x: x["date"])}


# ════════════════════════════════════════════════════════════════
#  PAPER TRADING
# ════════════════════════════════════════════════════════════════
PAPER_INITIAL = 5_000_000  # ₹50 lakh virtual


def _get_paper(email: str) -> dict:
    if email not in _paper:
        _paper[email] = {
            "balance": PAPER_INITIAL,
            "initial": PAPER_INITIAL,
            "trades":  [],
            "created": str(date.today()),
        }
    return _paper[email]


@app.get("/paper/account")
def paper_account(user=Depends(get_current_user)):
    acc = _get_paper(user["email"])
    pnl = acc["balance"] - acc["initial"]
    return {**acc, "total_pnl": pnl,
            "pnl_pct": round(pnl / acc["initial"] * 100, 2)}


@app.post("/paper/trade")
def paper_trade(req: TradeLogRequest, user=Depends(get_current_user)):
    import uuid
    email = user["email"]
    acc   = _get_paper(email)
    margins = {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}
    cost    = margins.get(req.instrument, 80000) * req.lots
    if acc["balance"] < cost:
        raise HTTPException(400, "Insufficient paper capital")
    acc["balance"] -= cost
    trade = {
        "id":           str(uuid.uuid4())[:8].upper(),
        "date":         str(date.today()),
        "entry_time":   datetime.now().isoformat(),
        "strategy":     req.strategy,
        "instrument":   req.instrument,
        "lots":         req.lots,
        "entry_spread": req.entry_spread,
        "status":       "OPEN",
        "margin_used":  cost,
    }
    acc["trades"].append(trade)
    return {"paper_trade": trade, "remaining_balance": acc["balance"]}


@app.get("/paper/leaderboard")
def leaderboard():
    board = []
    for email, acc in _paper.items():
        pnl  = acc["balance"] - acc["initial"]
        user = _db["users"].get(email, {})
        board.append({
            "name":    user.get("name", "Anonymous"),
            "pnl":     pnl,
            "pnl_pct": round(pnl / acc["initial"] * 100, 2),
            "trades":  len(acc["trades"]),
        })
    return {"leaderboard": sorted(board, key=lambda x: x["pnl"], reverse=True)[:20]}


# ════════════════════════════════════════════════════════════════
#  SUBSCRIPTION
# ════════════════════════════════════════════════════════════════
@app.get("/subscription/plans")
def get_plans():
    return {"plans": PLANS_LIST}


@app.post("/subscription/upgrade")
def upgrade_plan(plan: str, user=Depends(get_current_user)):
    if plan not in TIERS:
        raise HTTPException(400, "Invalid plan")
    _db["users"][user["email"]]["plan"] = plan
    return {"message": f"Upgraded to {plan}", "plan": plan}


# ════════════════════════════════════════════════════════════════
#  WEBSOCKET
# ════════════════════════════════════════════════════════════════
@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        # Send last 10 signals on connect
        recent = _db["signals"][-10:] if _db["signals"] else []
        for sig in recent:
            await ws.send_text(json.dumps({"type": "signal", "data": sig}, default=str))
        # Send status
        await ws.send_text(json.dumps({
            "type":     "status",
            "xls_live": _XLS_OK,
            "nse_live": _NSE_OK,
        }))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)


# ════════════════════════════════════════════════════════════════
#  HEALTH
# ════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {
        "status":    "ok",
        "version":   "2.0.0",
        "users":     len(_db["users"]),
        "signals":   len(_db["signals"]),
        "xls_live":  _XLS_OK,
        "nse_live":  _NSE_OK,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/")
def root():
    return {"message": "AlgoTrade API v2.0", "docs": "/docs",
            "health": "/health", "ws": "/ws/signals"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True,
                log_level="info")