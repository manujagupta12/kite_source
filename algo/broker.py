"""
broker.py  —  AlgoTrade Order Execution Layer
==============================================
Unified interface for Dhan, Zerodha Kite, or Paper (simulated) trading.

Setup:
  Dhan:  DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN in .env
  Kite:  KITE_API_KEY + KITE_ACCESS_TOKEN in .env
         pip install kiteconnect
  Paper: always available — no credentials needed

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

class PaperBroker:
    """Simulates orders without hitting any API."""
    is_ready = True

    def place_order(self, order: OrderRequest) -> OrderResult:
        oid = f"PAPER_{int(time.time() * 1000)}"
        log.info(f"[Paper] Simulated: {oid} {order.action} {order.symbol} x{order.quantity}")
        return OrderResult(ok=True, order_id=oid, broker="paper",
                           status="COMPLETE", message="Paper trade — no real order placed")

    def place_from_signal(self, signal: dict, lots: int = 1) -> OrderResult:
        inst = signal.get("instrument") or signal.get("symbol", "BANKNIFTY")
        dirn = signal.get("direction", "LONG")
        return self.place_order(OrderRequest(
            symbol=inst, action="BUY" if dirn in ("LONG", "BUY") else "SELL",
            quantity=lots * LOT_SIZES.get(inst, 15),
        ))

    def get_positions(self): return []
    def get_orders(self):    return []


# ══════════════════════════════════════════════════════════════════════════
#  FACTORY
# ══════════════════════════════════════════════════════════════════════════

def get_broker(prefer: Optional[str] = None):
    """Return best available broker: Dhan > Kite > Paper."""
    choice = prefer or os.environ.get("BROKER", "").lower()

    if choice == "dhan":
        b = DhanBroker(); return b if b.is_ready else PaperBroker()
    if choice == "kite":
        b = KiteBroker(); return b if b.is_ready else PaperBroker()
    if choice == "paper":
        return PaperBroker()

    dhan = DhanBroker()
    if dhan.is_ready:
        log.info("[Broker] Using Dhan"); return dhan

    kite = KiteBroker()
    if kite.is_ready:
        log.info("[Broker] Using Kite"); return kite

    log.warning("[Broker] No credentials — using PaperBroker")
    return PaperBroker()


if __name__ == "__main__":
    b = get_broker()
    print(f"Active broker: {b.__class__.__name__}")
    if isinstance(b, DhanBroker) and b.is_ready:
        print("Funds:", b.get_funds())
    elif isinstance(b, PaperBroker):
        r = b.place_from_signal({"instrument": "BANKNIFTY", "direction": "LONG",
                                 "near_strike": 52000, "option_type": "CE"}, lots=1)
        print(f"Paper result: ok={r.ok} id={r.order_id}")
