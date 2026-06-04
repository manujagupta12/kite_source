"""
multistrategy_patch.py — Drop-in patch for your existing multistrategy.py
==========================================================================
Add these lines into your existing multistrategy.py to wire all strategies
into the live dashboard.

STEP 1: Add imports at top of multistrategy.py
STEP 2: Initialize SignalAdapter once at startup
STEP 3: Call adapter.on_bar() inside your existing bar loop
STEP 4: Replace your existing signal dict building with SignalStore.get_all_active()

This is NOT a full replacement — it's a patch. Your existing code stays.
"""

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Add these imports at the TOP of multistrategy.py
# ─────────────────────────────────────────────────────────────────────────────

from algo.signal_adapter import SignalAdapter, SignalStore
from strategies.risk_manager import RiskManager, RiskConfig

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Initialize once, near the top of your main() or run() function
# ─────────────────────────────────────────────────────────────────────────────

def init_signal_pipeline(broker, available_margin: float = 5_000_000):
    """
    Call this once at startup before your market loop begins.
    Returns (signal_store, adapter) — keep both alive for the session.
    """
    risk_config = RiskConfig(
        capital=available_margin,
        max_daily_loss_pct=2.0,
        max_drawdown_pct=15.0,
        max_positions_per_strategy=3,
        max_total_positions=10,
        risk_per_trade_pct=1.0,
        max_capital_per_trade_pct=10.0,
    )
    rm = RiskManager(risk_config)
    signal_store = SignalStore()
    adapter = SignalAdapter(broker, rm, signal_store)
    return signal_store, adapter, rm


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Call inside your existing bar/tick loop
# ─────────────────────────────────────────────────────────────────────────────

def on_each_bar(adapter, ohlcv_df, instrument, pcr_df=None,
                iv_series=None, option_chain=None):
    """
    Replace or supplement your existing signal generation with this call.
    ohlcv_df     : DataFrame of daily (or 5-min for VWAP) bars
    instrument   : "NIFTY" | "BANKNIFTY" | "FINNIFTY"
    pcr_df       : DataFrame with 'pcr' column indexed by date (optional)
    iv_series    : Daily IV/VIX series (optional but recommended)
    option_chain : dict with keys: dte, iv, near_dte, far_dte, near_iv, far_iv,
                   expiry, near_expiry, far_expiry (optional)

    Returns list of new signal dicts generated this bar.
    """
    return adapter.on_bar(
        ohlcv_df=ohlcv_df,
        instrument=instrument,
        pcr_df=pcr_df,
        iv_series=iv_series,
        option_chain=option_chain,
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Replace your /signals/ endpoint response with this
# ─────────────────────────────────────────────────────────────────────────────

def get_active_signals(signal_store: SignalStore) -> list:
    """
    Use in your Flask/FastAPI /signals/ route:

        @app.get("/signals/")
        def signals():
            return {"signals": get_active_signals(signal_store)}
    """
    return signal_store.get_all_active()


# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Wire WebSocket push (optional but gives instant card updates)
# ─────────────────────────────────────────────────────────────────────────────

def wire_websocket(signal_store: SignalStore, socketio):
    """
    If you use Flask-SocketIO or similar, wire the push callback here.

    Example (Flask-SocketIO):
        from flask_socketio import SocketIO
        socketio = SocketIO(app)
        wire_websocket(signal_store, socketio)
    """
    def push_to_ws(signal: dict):
        socketio.emit("new_signal", signal, namespace="/signals")

    signal_store.set_ws_callback(push_to_ws)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Daily reset (call at 9:15 AM IST each day)
# ─────────────────────────────────────────────────────────────────────────────

def daily_market_open_reset(rm: RiskManager):
    """
    Schedule this at 9:15 AM IST.
    Resets daily P&L counter and re-activates paused strategies.
    """
    rm.reset_daily()
    import logging
    logging.getLogger(__name__).info("Daily risk reset complete — market open")


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLE — Minimal full integration (paste into your multistrategy.py)
# ─────────────────────────────────────────────────────────────────────────────

"""
EXAMPLE INTEGRATION (pseudocode — adapt to your existing loop structure):

from algo.multistrategy_patch import init_signal_pipeline, on_each_bar, get_active_signals

# --- At startup ---
signal_store, adapter, rm = init_signal_pipeline(broker, available_margin=5_000_000)

# --- In your scheduled bar loop (runs every 5 min or on daily close) ---
for instrument in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
    ohlcv = fetch_ohlcv(instrument)        # your existing data fetch
    pcr   = fetch_pcr(instrument)          # optional
    iv    = fetch_iv_series(instrument)    # optional
    oc    = fetch_option_chain(instrument) # optional

    new_sigs = on_each_bar(adapter, ohlcv, instrument, pcr, iv, oc)
    # new_sigs are already in signal_store + auto-placed if configured

# --- In your /signals/ API route ---
@app.get("/signals/")
def signals_endpoint():
    return {"signals": get_active_signals(signal_store)}

# --- Cancel a signal from frontend ---
@app.post("/signals/<signal_id>/cancel")
def cancel_signal(signal_id):
    signal_store.cancel(signal_id)
    return {"status": "cancelled"}
"""
