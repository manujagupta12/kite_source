"""
broker.py  —  AlgoTrade Order Execution Layer
==============================================
Unified interface for Dhan, Zerodha Kite, Upstox, or Paper (simulated) trading.

Setup:
  Dhan:    DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in .env
  Kite:    KITE_API_KEY + KITE_ACCESS_TOKEN in .env  (pip install kiteconnect)
  Upstox:  UPSTOX_API_KEY + UPSTOX_API_SECRET in .env
           UPSTOX_ACCESS_TOKEN — generated once via OAuth (auto-refreshed daily)
           Step 1: Add api_key + secret to .env
           Step 2: Visit http://localhost:8000/broker/upstox-auth in browser
           Step 3: Log in with Upstox → token saved automatically
  Paper:   always available — no credentials needed

Basket orders (multi-leg — Calendar Spread, Iron Condor):
  results = get_broker().place_basket([order1, order2])

Usage:
  from broker import get_broker, OrderRequest
  result = get_broker().place_from_signal(signal_dict, lots=1)
"""

import logging, os, time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("broker")

LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}


@dataclass
class OrderRequest:
    symbol:          str
    action:          str              # BUY | SELL
    quantity:        int
    exchange:        str = "NFO"
    instrument_type: str = "CE"       # CE | PE | FUT | EQ
    strike:          Optional[int]  = None
    expiry:          Optional[str]  = None   # DD-MMM-YYYY
    order_type:      str = "MARKET"
    price:           float = 0.0
    product:         str = "INTRADAY"
    validity:        str = "DAY"
    tag:             str = "AlgoTrade"


@dataclass
class OrderResult:
    ok:       bool
    order_id: Optional[str] = None
    broker:   str = "unknown"
    status:   str = ""
    message:  str = ""
    raw:      dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
#  DHAN BROKER
# ══════════════════════════════════════════════════════════════════════════

class DhanBroker:
    """Order placement via Dhan REST API v2. https://dhanhq.co/docs/v2/"""

    API_BASE = "https://api.dhan.co/v2"

    def __init__(self):
        self.client_id    = os.environ.get("DHAN_CLIENT_ID", "").strip()
        self.access_token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
        self._ok = bool(self.client_id and self.access_token)
        if not self._ok:
            log.warning("[Dhan] Credentials not set")

    @property
    def is_ready(self) -> bool:
        return self._ok

    def _headers(self):
        return {"access-token": self.access_token, "client-id": self.client_id,
                "Content-Type": "application/json"}

    def get_funds(self) -> dict:
        import requests
        try:
            return requests.get(f"{self.API_BASE}/funds", headers=self._headers(), timeout=8).json()
        except Exception as e:
            return {"error": str(e)}

    def place_order(self, order: OrderRequest) -> OrderResult:
        import requests
        if not self._ok:
            return OrderResult(ok=False, broker="dhan", message="Credentials not configured")

        payload = {
            "dhanClientId":     self.client_id,
            "transactionType":  "BUY" if order.action.upper() == "BUY" else "SELL",
            "exchangeSegment":  order.exchange,
            "productType":      order.product,
            "orderType":        order.order_type,
            "validity":         order.validity,
            "tradingSymbol":    self._build_symbol(order),
            "securityId":       "",
            "quantity":         order.quantity,
            "price":            order.price,
            "triggerPrice":     0,
            "disclosedQuantity": 0,
            "afterMarketOrder": False,
            "tag":              order.tag,
        }
        try:
            r    = requests.post(f"{self.API_BASE}/orders", json=payload,
                                 headers=self._headers(), timeout=10)
            data = r.json()
            if r.status_code in (200, 201) and data.get("orderId"):
                log.info(f"[Dhan] Order {data['orderId']} placed")
                return OrderResult(ok=True, order_id=str(data["orderId"]),
                                   broker="dhan", status="PENDING", raw=data)
            msg = data.get("remarks") or data.get("message") or str(data)
            log.warning(f"[Dhan] Order failed: {msg}")
            return OrderResult(ok=False, broker="dhan", message=msg, raw=data)
        except Exception as e:
            return OrderResult(ok=False, broker="dhan", message=str(e))

    def _build_symbol(self, order: OrderRequest) -> str:
        if order.instrument_type in ("CE", "PE") and order.strike and order.expiry:
            return f"{order.symbol}-{order.expiry}-{order.strike}-{order.instrument_type}"
        return order.symbol

    def place_from_signal(self, signal: dict, lots: int = 1) -> OrderResult:
        inst   = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
        dirn   = signal.get("direction", "LONG")
        near   = signal.get("near_strike") or signal.get("strike")
        otype  = signal.get("option_type", "CE")
        action = "BUY" if dirn in ("LONG", "BUY") else "SELL"
        qty    = lots * LOT_SIZES.get(inst, 15)
        return self.place_order(OrderRequest(
            symbol=inst, exchange="NFO", instrument_type=otype,
            strike=near, action=action, quantity=qty,
            order_type="MARKET", product="INTRADAY",
            tag=f"AT_{(signal.get('strategy') or '')[:10]}",
        ))

    def get_positions(self) -> list:
        import requests
        try:
            return requests.get(f"{self.API_BASE}/positions", headers=self._headers(), timeout=8).json()
        except Exception:
            return []

    def get_orders(self) -> list:
        import requests
        try:
            return requests.get(f"{self.API_BASE}/orders", headers=self._headers(), timeout=8).json()
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════════════
#  KITE BROKER
# ══════════════════════════════════════════════════════════════════════════

class KiteBroker:
    """Zerodha Kite Connect. pip install kiteconnect"""

    def __init__(self):
        self.api_key      = os.environ.get("KITE_API_KEY", "").strip()
        self.access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
        self._kite        = None
        if not (self.api_key and self.access_token):
            log.warning("[Kite] Credentials not set")
            return
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self.api_key)
            self._kite.set_access_token(self.access_token)
            log.info("[Kite] Ready")
        except ImportError:
            log.warning("[Kite] pip install kiteconnect")

    @property
    def is_ready(self) -> bool:
        return self._kite is not None

    def place_order(self, order: OrderRequest) -> OrderResult:
        if not self._kite:
            return OrderResult(ok=False, broker="kite", message="Kite not configured")
        kite = self._kite
        product_map = {"INTRADAY": kite.PRODUCT_MIS, "CNC": kite.PRODUCT_CNC,
                       "CARRYFORWARD": kite.PRODUCT_NRML}
        try:
            oid = kite.place_order(
                variety=kite.VARIETY_REGULAR,
                exchange=kite.EXCHANGE_NFO if order.exchange == "NFO" else kite.EXCHANGE_NSE,
                tradingsymbol=self._build_symbol(order),
                transaction_type=kite.TRANSACTION_TYPE_BUY if order.action == "BUY" else kite.TRANSACTION_TYPE_SELL,
                quantity=order.quantity,
                product=product_map.get(order.product, kite.PRODUCT_MIS),
                order_type=kite.ORDER_TYPE_MARKET if order.order_type == "MARKET" else kite.ORDER_TYPE_LIMIT,
                price=order.price if order.order_type != "MARKET" else None,
                tag=order.tag[:20],
            )
            log.info(f"[Kite] Order {oid} placed")
            return OrderResult(ok=True, order_id=str(oid), broker="kite", status="PENDING")
        except Exception as e:
            return OrderResult(ok=False, broker="kite", message=str(e))

    def _build_symbol(self, order: OrderRequest) -> str:
        if order.instrument_type in ("CE", "PE") and order.strike and order.expiry:
            try:
                from datetime import datetime
                for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
                    try:
                        dt = datetime.strptime(order.expiry, fmt)
                        return f"{order.symbol}{dt.strftime('%y%b').upper()}{order.strike}{order.instrument_type}"
                    except ValueError:
                        continue
            except Exception:
                pass
        return order.symbol

    def place_from_signal(self, signal: dict, lots: int = 1) -> OrderResult:
        inst   = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
        dirn   = signal.get("direction", "LONG")
        near   = signal.get("near_strike") or signal.get("strike")
        otype  = signal.get("option_type", "CE")
        action = "BUY" if dirn in ("LONG", "BUY") else "SELL"
        qty    = lots * LOT_SIZES.get(inst, 15)
        return self.place_order(OrderRequest(
            symbol=inst, exchange="NFO", instrument_type=otype,
            strike=near, action=action, quantity=qty,
        ))

    def get_positions(self):
        try: return self._kite.positions() if self._kite else {}
        except: return {}

    def get_orders(self):
        try: return self._kite.orders() if self._kite else []
        except: return []


# ══════════════════════════════════════════════════════════════════════════
#  PAPER BROKER
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
#  UPSTOX BROKER
# ══════════════════════════════════════════════════════════════════════════

_UPSTOX_TOKEN_FILE = None  # set lazily

def _upstox_token_path():
    import pathlib
    global _UPSTOX_TOKEN_FILE
    if _UPSTOX_TOKEN_FILE is None:
        _UPSTOX_TOKEN_FILE = pathlib.Path(__file__).parent.parent / "data" / "upstox_token.json"
    return _UPSTOX_TOKEN_FILE


class UpstoxBroker:
    """
    Upstox API v2 order placement.
    Docs: https://upstox.com/developer/api-documentation/

    Auth flow (one-time per day):
      1. Add UPSTOX_API_KEY + UPSTOX_API_SECRET to .env
      2. Visit http://localhost:8000/broker/upstox-auth  (opens Upstox login)
      3. Approve → token saved to data/upstox_token.json automatically

    Token is valid until 3:30 AM next day (Upstox refreshes daily).
    """

    API_BASE    = "https://api.upstox.com/v2"
    AUTH_URL    = "https://api.upstox.com/v2/login/authorization/dialog"
    TOKEN_URL   = "https://api.upstox.com/v2/login/authorization/token"
    REDIRECT    = "http://localhost:8000/broker/upstox-callback"

    # Upstox instrument type codes
    INST_MAP = {"CE": "CE", "PE": "PE", "FUT": "FUT", "EQ": "EQ"}

    def __init__(self):
        self.api_key    = os.environ.get("UPSTOX_API_KEY", "").strip()
        self.api_secret = os.environ.get("UPSTOX_API_SECRET", "").strip()
        self.access_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()

        # Try loading from saved token file if env not set
        if not self.access_token:
            self._load_saved_token()

        self._ok = bool(self.api_key and self.access_token)
        if not self._ok:
            if self.api_key:
                log.warning("[Upstox] API key found but no access token — visit /broker/upstox-auth")
            else:
                log.warning("[Upstox] UPSTOX_API_KEY not set")
        else:
            log.info("[Upstox] Broker ready")

    def _load_saved_token(self):
        import json
        tf = _upstox_token_path()
        if tf.exists():
            try:
                data = json.loads(tf.read_text())
                token = data.get("access_token", "")
                if token:
                    self.access_token = token
                    os.environ["UPSTOX_ACCESS_TOKEN"] = token
                    log.info("[Upstox] Token loaded from file")
            except Exception:
                pass

    @classmethod
    def save_token(cls, access_token: str) -> None:
        """Save token to file + env. Called by OAuth callback."""
        import json, time as _t
        tf = _upstox_token_path()
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text(json.dumps({
            "access_token": access_token,
            "saved_at": _t.strftime("%Y-%m-%dT%H:%M:%S"),
        }, indent=2))
        os.environ["UPSTOX_ACCESS_TOKEN"] = access_token
        log.info(f"[Upstox] Token saved to {tf}")

    @classmethod
    def get_auth_url(cls) -> str:
        """Returns the URL user must visit to authenticate."""
        api_key = os.environ.get("UPSTOX_API_KEY", "")
        return (f"{cls.AUTH_URL}?response_type=code"
                f"&client_id={api_key}"
                f"&redirect_uri={cls.REDIRECT}")

    @classmethod
    def exchange_code(cls, code: str) -> Optional[str]:
        """Exchange OAuth code for access_token. Called from callback endpoint."""
        import requests as _req
        api_key    = os.environ.get("UPSTOX_API_KEY", "")
        api_secret = os.environ.get("UPSTOX_API_SECRET", "")
        try:
            r = _req.post(cls.TOKEN_URL, data={
                "code":          code,
                "client_id":     api_key,
                "client_secret": api_secret,
                "redirect_uri":  cls.REDIRECT,
                "grant_type":    "authorization_code",
            }, headers={"accept": "application/json"}, timeout=15)
            r.raise_for_status()
            token = r.json().get("access_token", "")
            if token:
                cls.save_token(token)
            return token or None
        except Exception as e:
            log.error(f"[Upstox] Token exchange failed: {e}")
            return None

    @property
    def is_ready(self) -> bool:
        return self._ok

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "Api-Version":   "2.0",
        }

    def get_funds(self) -> dict:
        import requests as _req
        try:
            r = _req.get(f"{self.API_BASE}/user/fund-margin",
                         headers=self._headers(), timeout=8)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _get_instrument_key(self, order: OrderRequest) -> Optional[str]:
        """
        Resolve Upstox instrument key from symbol + strike + expiry + type.
        Upstox uses instrument_key like 'NSE_FO|104584' for each contract.

        Strategy: search their instruments API or use cached mapping.
        Falls back to symbol string if not found.
        """
        import requests as _req
        if not (order.strike and order.expiry and order.instrument_type in ("CE", "PE")):
            return f"NSE_EQ|{order.symbol}"

        # Try Upstox instrument search API
        try:
            r = _req.get(
                f"{self.API_BASE}/market-quote/search",
                params={"q": f"{order.symbol} {order.strike} {order.instrument_type}",
                        "asset_type": "FO"},
                headers=self._headers(), timeout=8,
            )
            data = r.json().get("data", [])
            # Filter to correct expiry
            for item in data:
                name = item.get("trading_symbol", "")
                if (str(order.strike) in name and
                    order.instrument_type in name and
                    order.symbol in name):
                    return item.get("instrument_key")
        except Exception:
            pass

        return None

    def place_order(self, order: OrderRequest) -> OrderResult:
        import requests as _req
        if not self._ok:
            return OrderResult(ok=False, broker="upstox", message="Not configured")

        instrument_key = self._get_instrument_key(order)
        payload = {
            "quantity":         order.quantity,
            "product":          "I" if order.product == "INTRADAY" else "D",
            "validity":         "DAY",
            "price":            order.price,
            "tag":              order.tag[:20],
            "instrument_token": instrument_key or f"NSE_FO|{order.symbol}",
            "order_type":       "MARKET" if order.order_type == "MARKET" else "LIMIT",
            "transaction_type": "BUY" if order.action.upper() == "BUY" else "SELL",
            "disclosed_quantity": 0,
            "trigger_price":    0,
            "is_amo":           False,
        }
        try:
            r    = _req.post(f"{self.API_BASE}/order/place",
                             json=payload, headers=self._headers(), timeout=10)
            data = r.json()
            if r.status_code in (200, 201) and data.get("data", {}).get("order_id"):
                oid = data["data"]["order_id"]
                log.info(f"[Upstox] Order {oid} placed")
                return OrderResult(ok=True, order_id=str(oid),
                                   broker="upstox", status="open", raw=data)
            msg = data.get("message") or str(data)
            return OrderResult(ok=False, broker="upstox", message=str(msg), raw=data)
        except Exception as e:
            return OrderResult(ok=False, broker="upstox", message=str(e))

    def place_basket(self, orders: list) -> list:
        return [self.place_order(o) for o in orders]

    def place_from_signal(self, signal: dict, lots: int = 1) -> OrderResult:
        inst   = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
        dirn   = signal.get("direction", "LONG")
        near   = signal.get("near_strike") or signal.get("strike")
        otype  = signal.get("option_type", "CE")
        action = "BUY" if dirn in ("LONG", "BUY") else "SELL"
        qty    = lots * LOT_SIZES.get(inst, 15)
        return self.place_order(OrderRequest(
            symbol=inst, exchange="NFO", instrument_type=otype,
            strike=near, action=action, quantity=qty,
            order_type="MARKET", product="INTRADAY",
            tag=f"AT_{(signal.get('strategy') or '')[:10]}",
        ))

    def get_positions(self) -> dict:
        import requests as _req
        try:
            r = _req.get(f"{self.API_BASE}/portfolio/short-term-positions",
                         headers=self._headers(), timeout=8)
            return r.json().get("data", [])
        except Exception as e:
            return {"error": str(e)}

    def get_orders(self) -> list:
        import requests as _req
        try:
            r = _req.get(f"{self.API_BASE}/order/retrieve-all",
                         headers=self._headers(), timeout=8)
            return r.json().get("data", [])
        except Exception:
            return []


# ══════════════════════════════════════════════════════════════════════════
#  BASKET ORDER HELPERS
# ══════════════════════════════════════════════════════════════════════════

def build_calendar_basket(signal: dict, lots: int = 1) -> list:
    """2-leg basket for Calendar Spread: SELL near, BUY far (or reverse for short)."""
    inst   = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
    otype  = signal.get("option_type", "CE")
    near_s = signal.get("near_strike") or signal.get("strike")
    far_s  = signal.get("far_strike")  or near_s
    qty    = lots * LOT_SIZES.get(inst, 15)
    dirn   = (signal.get("direction") or "LONG").upper()
    leg1_action = "SELL" if dirn in ("LONG", "BUY") else "BUY"
    leg2_action = "BUY"  if dirn in ("LONG", "BUY") else "SELL"
    return [
        OrderRequest(symbol=inst, exchange="NFO", instrument_type=otype,
                     strike=near_s, action=leg1_action, quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag=f"CAL_NEAR_{leg1_action}"),
        OrderRequest(symbol=inst, exchange="NFO", instrument_type=otype,
                     strike=far_s,  action=leg2_action, quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag=f"CAL_FAR_{leg2_action}"),
    ]


def build_iron_condor_basket(signal: dict, lots: int = 1) -> list:
    """4-leg basket for Iron Condor: SELL OTM CE+PE, BUY wings."""
    inst     = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
    atm      = signal.get("near_strike") or signal.get("atm") or 0
    step     = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(inst, 50)
    short_ce = signal.get("short_ce") or (atm + step * 4)
    short_pe = signal.get("short_pe") or (atm - step * 4)
    wing_ce  = signal.get("wing_ce")  or (short_ce + step * 2)
    wing_pe  = signal.get("wing_pe")  or (short_pe - step * 2)
    qty      = lots * LOT_SIZES.get(inst, 15)
    return [
        OrderRequest(symbol=inst, exchange="NFO", instrument_type="CE",
                     strike=short_ce, action="SELL", quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag="IC_SHORT_CE"),
        OrderRequest(symbol=inst, exchange="NFO", instrument_type="CE",
                     strike=wing_ce,  action="BUY",  quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag="IC_WING_CE"),
        OrderRequest(symbol=inst, exchange="NFO", instrument_type="PE",
                     strike=short_pe, action="SELL", quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag="IC_SHORT_PE"),
        OrderRequest(symbol=inst, exchange="NFO", instrument_type="PE",
                     strike=wing_pe,  action="BUY",  quantity=qty,
                     order_type="MARKET", product="INTRADAY", tag="IC_WING_PE"),
    ]


# ══════════════════════════════════════════════════════════════════════════
#  PAPER BROKER
# ══════════════════════════════════════════════════════════════════════════

class PaperBroker:
    """Simulates orders without hitting any API."""
    is_ready = True

    def place_order(self, order: OrderRequest) -> OrderResult:
        oid = f"PAPER_{int(time.time() * 1000)}"
        log.info(f"[Paper] {oid} {order.action} {order.symbol} x{order.quantity}")
        return OrderResult(ok=True, order_id=oid, broker="paper",
                           status="COMPLETE", message="Paper trade — no real order placed")

    def place_basket(self, orders: list) -> list:
        return [self.place_order(o) for o in orders]

    def place_from_signal(self, signal: dict, lots: int = 1) -> OrderResult:
        inst = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
        dirn = signal.get("direction", "LONG")
        return self.place_order(OrderRequest(
            symbol=inst, action="BUY" if dirn in ("LONG","BUY") else "SELL",
            quantity=lots * LOT_SIZES.get(inst, 15),
        ))

    def get_positions(self): return []
    def get_orders(self):    return []


# ══════════════════════════════════════════════════════════════════════════
#  FACTORY
# ══════════════════════════════════════════════════════════════════════════

def get_broker(prefer: Optional[str] = None):
    """Return best available broker: Dhan > Kite > Upstox > Paper."""
    choice = prefer or os.environ.get("BROKER", "").lower()
    if choice == "dhan":    b = DhanBroker();   return b if b.is_ready else PaperBroker()
    if choice == "kite":    b = KiteBroker();   return b if b.is_ready else PaperBroker()
    if choice == "upstox":  b = UpstoxBroker(); return b if b.is_ready else PaperBroker()
    if choice == "paper":   return PaperBroker()

    for Cls in [DhanBroker, KiteBroker, UpstoxBroker]:
        b = Cls()
        if b.is_ready:
            log.info(f"[Broker] Auto-selected {b.__class__.__name__}"); return b

    log.warning("[Broker] No credentials — PaperBroker active")
    return PaperBroker()


if __name__ == "__main__":
    b = get_broker()
    print(f"Active broker: {b.__class__.__name__}")
    if isinstance(b, PaperBroker):
        r = b.place_from_signal({"instrument":"BANKNIFTY","direction":"LONG",
                                  "near_strike":52000,"option_type":"CE"}, lots=1)
        print(f"Paper result: ok={r.ok} id={r.order_id}")
