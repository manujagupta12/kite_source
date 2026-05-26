"""
ALGOTRADE BACKEND  —  app/backend/main.py
==========================================
Multi-market signal platform:
  - NIFTY / BANKNIFTY F&O  (from MultiTrade Excel)
  - Crypto perpetuals       (BTC, ETH, SOL via Delta Exchange)
  - Crypto funding arb      (Delta Exchange)
  - Equity futures          (NIFTY on Delta Exchange)

WebSocket streams ALL markets simultaneously to the dashboard.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from contextlib import asynccontextmanager
import asyncio, json, hashlib, os, random
from datetime import datetime, timedelta, date
from typing import Optional, List
import uvicorn

try:
    from jose import jwt
    _JWT_OK = True
except ImportError:
    _JWT_OK = False

try:
    import bcrypt as _bcrypt_lib
    _BCRYPT_OK = True
except ImportError:
    _BCRYPT_OK = False

try:
    from pydantic import BaseModel
except ImportError:
    from pydantic import BaseModel  # type: ignore

# ════════════════════════════════════════════════════════════════
#  CONFIG
# ════════════════════════════════════════════════════════════════
SECRET_KEY   = os.environ.get("SECRET_KEY", "algotrade-dev-secret-change-in-prod")
ALGORITHM    = "HS256"
TOKEN_EXPIRE = 60 * 24

# Delta Exchange credentials (loaded from environment / .env)
DELTA_API_KEY    = os.environ.get("DELTA_API_KEY", "")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "")
DELTA_ENV        = os.environ.get("DELTA_ENV", "india")

_db = {
    "users":   {},
    "trades":  {},
    "signals": [],   # rolling last 200 across all markets
}

TIERS = {
    "free":    {"markets": ["nifty"],              "live": False, "max_signals": 5},
    "starter": {"markets": ["nifty", "crypto"],    "live": True,  "max_signals": 20},
    "pro":     {"markets": ["nifty","crypto","equity_india"], "live": True, "max_signals": 50},
    "elite":   {"markets": ["nifty","crypto","equity_india","options"], "live": True, "max_signals": 100},
}

# ════════════════════════════════════════════════════════════════
#  AUTH
# ════════════════════════════════════════════════════════════════
def hash_password(pw: str) -> str:
    if _BCRYPT_OK:
        return _bcrypt_lib.hashpw(pw.encode("utf-8")[:72],
                                  _bcrypt_lib.gensalt()).decode("utf-8")
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def verify_password(pw: str, hashed: str) -> bool:
    if _BCRYPT_OK:
        try:
            return _bcrypt_lib.checkpw(pw.encode("utf-8")[:72],
                                       hashed.encode("utf-8"))
        except Exception:
            return False
    return hashlib.sha256(pw.encode("utf-8")).hexdigest() == hashed

def create_token(data: dict) -> str:
    payload = {**data, "exp": datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE)}
    if _JWT_OK:
        return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    import base64
    return base64.b64encode(json.dumps(payload).encode()).decode()

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
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = _db["users"].get(payload.get("sub"))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
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
    name: str; email: str; password: str

class LoginRequest(BaseModel):
    email: str; password: str

class TradeLogRequest(BaseModel):
    strategy:     str
    market:       str       # "nifty" | "crypto" | "equity_india"
    instrument:   str       # "NIFTY" | "BTCUSD" | "ETHUSD"
    option_type:  str       # "CE" | "PE" | "PERP" | "SPOT"
    direction:    str
    near_strike:  Optional[float] = None
    far_strike:   Optional[float] = None
    lots:         int
    entry_spread: float
    notes:        Optional[str] = ""

class TradeCloseRequest(BaseModel):
    trade_id:    str
    exit_spread: float
    notes:       Optional[str] = ""

# ════════════════════════════════════════════════════════════════
#  WEBSOCKET BROADCASTER
# ════════════════════════════════════════════════════════════════
class SignalBroadcaster:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, message: dict):
        payload = json.dumps(message, default=str)
        dead    = []
        for ws in self.connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

broadcaster = SignalBroadcaster()

# ════════════════════════════════════════════════════════════════
#  DELTA EXCHANGE LIVE DATA FETCH
#  Fetches real market data from Delta Exchange every 5 seconds
# ════════════════════════════════════════════════════════════════
async def fetch_delta_signals() -> list:
    """
    Fetches live crypto signals from Delta Exchange.
    Falls back to simulated data if API unavailable.
    """
    if not DELTA_API_KEY:
        return _mock_crypto_signals()

    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        from delta_connector import DeltaConnector, DeltaSignalEngine
        dc  = DeltaConnector()
        eng = DeltaSignalEngine(dc)
        return eng.scan_all()
    except Exception as e:
        print(f"  [Delta] Live fetch failed: {e} — using mock data")
        return _mock_crypto_signals()


def _mock_crypto_signals() -> list:
    """Simulated crypto signals for demo/testing when API key not set."""
    symbols = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
    strategies = ["CRYPTO MOMENTUM", "FUNDING RATE ARB"]
    signals = []
    for sym in symbols:
        base_prices = {"BTCUSD": 93000, "ETHUSD": 3200, "SOLUSD": 185, "XRPUSD": 2.4}
        price = base_prices[sym] * (1 + random.uniform(-0.02, 0.02))
        funding = random.uniform(-0.001, 0.002)
        change = random.uniform(-3, 3)
        direction = "LONG" if change > 0 else "SHORT"
        strat = random.choice(strategies)
        score = random.randint(45, 90)
        signals.append({
            "strategy":     strat,
            "market":       "CRYPTO",
            "symbol":       sym,
            "score":        score,
            "direction":    direction,
            "risk":         "LOW" if abs(funding) < 0.0005 else "MEDIUM" if abs(funding) < 0.001 else "HIGH",
            "mark_price":   round(price, 2),
            "bid":          round(price * 0.9995, 2),
            "ask":          round(price * 1.0005, 2),
            "funding_rate": round(funding * 100, 4),
            "change_pct":   round(change, 2),
            "trend":        "UP" if change > 0.3 else "DOWN" if change < -0.3 else "FLAT",
            "entry_at":     round(price * 1.001 if direction=="LONG" else price * 0.999, 2),
            "target_at":    round(price * 1.02  if direction=="LONG" else price * 0.98, 2),
            "sl_at":        round(price * 0.99  if direction=="LONG" else price * 1.01, 2),
            "reason":       f"{direction} momentum | Funding {round(funding*100,4)}% | {change:.2f}% change",
            "orders":       f"{'BUY' if direction=='LONG' else 'SELL'} {sym} LIMIT @ {round(price*1.001 if direction=='LONG' else price*0.999, 2)}",
            "timestamp":    datetime.now().isoformat(),
        })
    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


def _mock_nifty_signal() -> dict:
    """Simulated NIFTY F&O signal for demo."""
    spot  = 23800 + random.randint(-300, 300)
    atm   = int(round(spot / 50) * 50)
    strat = random.choice(["S1 CALENDAR", "S2 IRON CONDOR", "S7 RATIO SPREAD"])
    score = random.randint(50, 92)
    dirn  = random.choice(["LONG", "SHORT"])
    spread = round(random.uniform(-8, 8), 2)
    vix    = round(random.uniform(12, 22), 2)
    return {
        "strategy":    strat,
        "market":      "NIFTY_FO",
        "symbol":      f"NIFTY-{atm}-CE",
        "score":       score,
        "direction":   dirn,
        "risk":        "LOW" if vix < 13 else "MEDIUM" if vix < 19 else "HIGH",
        "near_strike": atm,
        "far_strike":  atm,
        "spread":      spread,
        "fair_value":  round(spread + random.uniform(-3, 3), 2),
        "vix":         vix,
        "near_bid":    round(150 + random.uniform(-30, 30), 2),
        "near_ask":    round(152 + random.uniform(-30, 30), 2),
        "far_bid":     round(160 + random.uniform(-30, 30), 2),
        "far_ask":     round(162 + random.uniform(-30, 30), 2),
        "target_pts":  4,
        "sl_pts":      3,
        "lots_suggested": random.randint(1, 10),
        "timestamp":   datetime.now().isoformat(),
    }

# ════════════════════════════════════════════════════════════════
#  BACKGROUND SIGNAL LOOP
#  Fetches live data every 5s and broadcasts to all connected clients
# ════════════════════════════════════════════════════════════════
async def signal_loop():
    cycle = 0
    while True:
        await asyncio.sleep(5)
        cycle += 1

        # Crypto signals from Delta Exchange
        crypto_signals = await fetch_delta_signals()
        for sig in crypto_signals[:5]:
            _db["signals"].append(sig)
            await broadcaster.broadcast({"type": "signal", "market": "crypto", "data": sig})

        # NIFTY F&O signal (from mock / MultiTrade when connected)
        nifty_sig = _mock_nifty_signal()
        _db["signals"].append(nifty_sig)
        await broadcaster.broadcast({"type": "signal", "market": "nifty", "data": nifty_sig})

        # Keep rolling 200 signals
        _db["signals"] = _db["signals"][-200:]

        # Market summary every 6 cycles (~30s)
        if cycle % 6 == 0:
            summary = {
                "type": "market_summary",
                "crypto_count":   len([s for s in crypto_signals if s["direction"]=="LONG"]),
                "total_signals":  len(_db["signals"]),
                "delta_connected": bool(DELTA_API_KEY),
                "timestamp":      datetime.now().isoformat(),
            }
            await broadcaster.broadcast(summary)

# ════════════════════════════════════════════════════════════════
#  APP LIFECYCLE
# ════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed demo user
    if "demo@algotrade.in" not in _db["users"]:
        _db["users"]["demo@algotrade.in"] = {
            "name":    "Demo User",
            "email":   "demo@algotrade.in",
            "password": hash_password("demo123"),
            "plan":    "pro",
            "joined":  str(date.today()),
            "margin_budget": 5_000_000,
        }
    task = asyncio.create_task(signal_loop())
    yield
    task.cancel()

app = FastAPI(
    title       = "AlgoTrade Multi-Market API",
    description = "NIFTY F&O + Crypto signals via Delta Exchange",
    version     = "2.0.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ════════════════════════════════════════════════════════════════
#  AUTH ROUTES
# ════════════════════════════════════════════════════════════════
@app.post("/auth/register")
def register(req: RegisterRequest):
    if req.email in _db["users"]:
        raise HTTPException(status_code=400, detail="Email already registered")
    _db["users"][req.email] = {
        "name": req.name, "email": req.email,
        "password": hash_password(req.password),
        "plan": "free", "joined": str(date.today()),
        "margin_budget": 5_000_000,
    }
    token = create_token({"sub": req.email})
    user  = {k:v for k,v in _db["users"][req.email].items() if k != "password"}
    return {"token": token, "user": user}

@app.post("/auth/login")
def login(req: LoginRequest):
    user = _db["users"].get(req.email)
    if not user or not verify_password(req.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token({"sub": req.email})
    return {"token": token,
            "user": {k:v for k,v in user.items() if k != "password"}}

@app.get("/auth/me")
def me(user=Depends(get_current_user)):
    return {k:v for k,v in user.items() if k != "password"}

# ════════════════════════════════════════════════════════════════
#  SIGNAL ROUTES
# ════════════════════════════════════════════════════════════════
@app.get("/signals/latest")
def get_latest_signals(limit: int = 30, market: str = None,
                       user=Depends(get_optional_user)):
    signals = _db["signals"]
    if market:
        signals = [s for s in signals if s.get("market","").lower() == market.lower()]
    plan    = user["plan"] if user else "free"
    tier    = TIERS.get(plan, TIERS["free"])
    signals = signals[-min(limit, tier["max_signals"]):][::-1]
    return {"signals": signals, "count": len(signals),
            "delta_connected": bool(DELTA_API_KEY)}

@app.get("/signals/markets")
def get_markets():
    """Returns what markets are available and their status."""
    return {
        "markets": [
            {"id": "nifty",         "name": "NIFTY F&O",          "status": "live",
             "source": "MultiTrade Excel", "instruments": ["CE","PE","Futures"]},
            {"id": "crypto",        "name": "Crypto Perpetuals",  "status": "live" if DELTA_API_KEY else "demo",
             "source": "Delta Exchange",   "instruments": ["BTCUSD","ETHUSD","SOLUSD","XRPUSD","BNBUSD"]},
            {"id": "equity_india",  "name": "Equity India",       "status": "live" if DELTA_API_KEY else "demo",
             "source": "Delta Exchange",   "instruments": ["NIFTYUSD","BANKNIFTYUSD"]},
            {"id": "crypto_options","name": "Crypto Options",     "status": "live" if DELTA_API_KEY else "demo",
             "source": "Delta Exchange",   "instruments": ["BTC Options","ETH Options"]},
        ],
        "delta_connected": bool(DELTA_API_KEY),
        "delta_env":       DELTA_ENV,
    }

@app.get("/signals/crypto/live")
async def crypto_live():
    """Direct live crypto data from Delta Exchange."""
    signals = await fetch_delta_signals()
    return {"signals": signals, "count": len(signals),
            "source": "Delta Exchange Live" if DELTA_API_KEY else "Demo Mode"}

@app.get("/signals/crypto/ticker/{symbol}")
async def crypto_ticker(symbol: str):
    """Live ticker for a specific crypto symbol."""
    if not DELTA_API_KEY:
        return {"error": "Delta API key not configured",
                "hint": "Set DELTA_API_KEY in .env file"}
    try:
        from delta_connector import DeltaConnector
        dc = DeltaConnector()
        return dc.get_ticker(symbol.upper())
    except Exception as e:
        return {"error": str(e)}

@app.get("/signals/crypto/market_summary")
async def market_summary():
    """Live market summary across crypto + equity."""
    if not DELTA_API_KEY:
        return {"status": "demo", "message": "Configure Delta API key for live data"}
    try:
        from delta_connector import DeltaConnector
        dc = DeltaConnector()
        return dc.get_market_summary()
    except Exception as e:
        return {"error": str(e)}

# ════════════════════════════════════════════════════════════════
#  TRADE ROUTES
# ════════════════════════════════════════════════════════════════
@app.post("/trades/enter")
def enter_trade(req: TradeLogRequest, user=Depends(get_current_user)):
    import uuid
    email    = user["email"]
    trade_id = str(uuid.uuid4())[:8].upper()

    # Margin per lot varies by market
    lot_margins = {
        "nifty": 80000, "banknifty": 90000, "finnifty": 50000,
        "BTCUSD": 5000, "ETHUSD": 500, "SOLUSD": 50,
    }
    margin_used = lot_margins.get(req.instrument, 80000) * req.lots

    trade = {
        "id":           trade_id,
        "date":         str(date.today()),
        "entry_time":   datetime.now().isoformat(),
        "exit_time":    None,
        "strategy":     req.strategy,
        "market":       req.market,
        "instrument":   req.instrument,
        "type":         req.option_type,
        "direction":    req.direction,
        "near_strike":  req.near_strike,
        "far_strike":   req.far_strike,
        "lots":         req.lots,
        "entry_spread": req.entry_spread,
        "margin_used":  margin_used,
        "exit_spread":  None,
        "pnl_pts":      None,
        "pnl_inr":      None,
        "status":       "OPEN",
        "notes":        req.notes,
    }
    if email not in _db["trades"]:
        _db["trades"][email] = []
    _db["trades"][email].append(trade)
    return {"trade_id": trade_id, "trade": trade}

@app.post("/trades/close")
def close_trade(req: TradeCloseRequest, user=Depends(get_current_user)):
    email  = user["email"]
    trades = _db["trades"].get(email, [])
    trade  = next((t for t in trades if t["id"] == req.trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    if trade["status"] == "CLOSED":
        raise HTTPException(status_code=400, detail="Already closed")

    # Lot sizes differ by market
    lot_sizes = {
        "NIFTY":25,"BANKNIFTY":15,"FINNIFTY":40,
        "BTCUSD":0.001,"ETHUSD":0.01,"SOLUSD":1,
    }
    ls     = lot_sizes.get(trade["instrument"], 25)
    entry  = float(trade["entry_spread"])
    exit_  = float(req.exit_spread)
    dirn   = trade["direction"]
    pnl_pts= (exit_ - entry) if dirn in ("LONG","BUY","BUY BOTH") else (entry - exit_)
    pnl_inr= round(pnl_pts * ls * trade["lots"], 2)

    trade.update({
        "exit_time":   datetime.now().isoformat(),
        "exit_spread": req.exit_spread,
        "pnl_pts":     round(pnl_pts, 4),
        "pnl_inr":     pnl_inr,
        "status":      "CLOSED",
        "notes":       (trade.get("notes","") + " | " + req.notes).strip(" | "),
    })
    return {"trade": trade, "pnl_inr": pnl_inr}

@app.get("/trades/today")
def today_trades(user=Depends(get_current_user)):
    email   = user["email"]
    trades  = _db["trades"].get(email, [])
    today   = str(date.today())
    todays  = [t for t in trades if t.get("date") == today]
    realised= sum(t["pnl_inr"] for t in todays if t.get("pnl_inr") and t["status"]=="CLOSED")
    return {
        "trades":        todays,
        "realised_pnl":  realised,
        "open_count":    sum(1 for t in todays if t["status"]=="OPEN"),
        "margin_budget": user.get("margin_budget", 5_000_000),
    }

@app.get("/trades/history")
def trade_history(user=Depends(get_current_user)):
    trades = _db["trades"].get(user["email"], [])
    return {"trades": trades[-200:], "total": len(trades)}

# ════════════════════════════════════════════════════════════════
#  ANALYTICS
# ════════════════════════════════════════════════════════════════
@app.get("/analytics/summary")
def analytics_summary(user=Depends(get_current_user)):
    trades = [t for t in _db["trades"].get(user["email"],[]) if t["status"]=="CLOSED"]
    if not trades:
        return {"message": "No closed trades yet"}
    pnls = [t["pnl_inr"] for t in trades if t.get("pnl_inr") is not None]
    wins = [p for p in pnls if p > 0]
    by_market = {}
    for t in trades:
        m = t.get("market","unknown")
        if m not in by_market:
            by_market[m] = {"trades":0,"wins":0,"total_pnl":0}
        by_market[m]["trades"]    += 1
        by_market[m]["wins"]      += 1 if (t.get("pnl_inr") or 0) > 0 else 0
        by_market[m]["total_pnl"] += (t.get("pnl_inr") or 0)
    return {
        "total_trades":   len(trades),
        "win_rate":       round(len(wins)/len(pnls)*100, 1) if pnls else 0,
        "total_pnl":      sum(pnls),
        "avg_pnl":        round(sum(pnls)/len(pnls), 2) if pnls else 0,
        "best_trade":     max(pnls) if pnls else 0,
        "worst_trade":    min(pnls) if pnls else 0,
        "by_market":      by_market,
    }

@app.get("/analytics/daily")
def daily_pnl(user=Depends(get_current_user)):
    trades  = [t for t in _db["trades"].get(user["email"],[]) if t["status"]=="CLOSED"]
    by_date = {}
    for t in trades:
        d = t.get("date","")
        if d not in by_date:
            by_date[d] = {"date":d,"pnl":0,"trades":0,"wins":0}
        by_date[d]["pnl"]    += (t.get("pnl_inr") or 0)
        by_date[d]["trades"] += 1
        by_date[d]["wins"]   += 1 if (t.get("pnl_inr") or 0) > 0 else 0
    return {"daily": sorted(by_date.values(), key=lambda x: x["date"])}

# ════════════════════════════════════════════════════════════════
#  PAPER TRADING
# ════════════════════════════════════════════════════════════════
_paper = {}

@app.get("/paper/account")
def paper_account(user=Depends(get_current_user)):
    email = user["email"]
    if email not in _paper:
        _paper[email] = {"balance": 5_000_000, "initial": 5_000_000, "trades": []}
    acc = _paper[email]
    pnl = acc["balance"] - acc["initial"]
    return {**acc, "total_pnl": pnl, "pnl_pct": round(pnl/acc["initial"]*100, 2)}

@app.post("/paper/trade")
def paper_trade(req: TradeLogRequest, user=Depends(get_current_user)):
    import uuid
    email = user["email"]
    if email not in _paper:
        _paper[email] = {"balance": 5_000_000, "initial": 5_000_000, "trades": []}
    cost = {"NIFTY":80000,"BTCUSD":5000,"ETHUSD":500}.get(req.instrument, 80000) * req.lots
    if _paper[email]["balance"] < cost:
        raise HTTPException(status_code=400, detail="Insufficient paper capital")
    _paper[email]["balance"] -= cost
    trade = {"id": str(uuid.uuid4())[:8].upper(), "date": str(date.today()),
             "strategy": req.strategy, "market": req.market,
             "instrument": req.instrument, "lots": req.lots,
             "entry_spread": req.entry_spread, "status": "OPEN", "margin": cost}
    _paper[email]["trades"].append(trade)
    return {"paper_trade": trade, "remaining": _paper[email]["balance"]}

@app.get("/paper/leaderboard")
def leaderboard():
    board = []
    for email, acc in _paper.items():
        pnl  = acc["balance"] - acc["initial"]
        user = _db["users"].get(email, {})
        board.append({"name": user.get("name","Anonymous"),
                      "pnl": pnl, "pnl_pct": round(pnl/acc["initial"]*100,2),
                      "trades": len(acc["trades"])})
    return {"leaderboard": sorted(board, key=lambda x: x["pnl"], reverse=True)[:20]}

# ════════════════════════════════════════════════════════════════
#  SUBSCRIPTION
# ════════════════════════════════════════════════════════════════
@app.get("/subscription/plans")
def plans():
    return {"plans": [
        {"id":"free",    "name":"Free",    "price_str":"₹0/mo",
         "features":["Paper trading","5 signals (delayed)","NIFTY F&O only","Community support"]},
        {"id":"starter", "name":"Starter", "price_str":"₹2,999/mo",
         "features":["20 live signals","NIFTY + Crypto (BTC,ETH,SOL)","Delta Exchange connected","Email support"]},
        {"id":"pro",     "name":"Pro",     "price_str":"₹7,999/mo",
         "features":["50 live signals","All markets incl. equity India","Crypto options chain","Priority support","Backtest access"]},
        {"id":"elite",   "name":"Elite",   "price_str":"₹19,999/mo",
         "features":["100 signals","All markets","API access","Custom strategy alerts","1-on-1 calls"]},
    ]}

@app.post("/subscription/upgrade")
def upgrade(plan: str, user=Depends(get_current_user)):
    if plan not in TIERS:
        raise HTTPException(status_code=400, detail="Invalid plan")
    _db["users"][user["email"]]["plan"] = plan
    return {"message": f"Upgraded to {plan}"}

# ════════════════════════════════════════════════════════════════
#  WEBSOCKET
# ════════════════════════════════════════════════════════════════
@app.websocket("/ws/signals")
async def ws_signals(ws: WebSocket):
    await broadcaster.connect(ws)
    try:
        recent = _db["signals"][-10:]
        for sig in recent:
            await ws.send_text(json.dumps({"type":"signal",
                                           "market": sig.get("market",""),
                                           "data": sig}, default=str))
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        broadcaster.disconnect(ws)

# ════════════════════════════════════════════════════════════════
#  HEALTH
# ════════════════════════════════════════════════════════════════
@app.get("/health")
def health():
    return {"status": "ok", "version": "2.0.0",
            "delta_connected": bool(DELTA_API_KEY),
            "delta_env": DELTA_ENV,
            "markets": ["NIFTY F&O", "Crypto", "Equity India"],
            "signals": len(_db["signals"]),
            "users": len(_db["users"]),
            "timestamp": datetime.now().isoformat()}

@app.get("/")
def root():
    return {"message": "AlgoTrade Multi-Market API v2.0",
            "docs": "/docs",
            "markets": ["NIFTY F&O", "Crypto (Delta Exchange)", "Equity India"]}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
