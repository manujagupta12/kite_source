"""
signal_adapter.py — Strategy Engine → Dashboard Signal Bridge
=============================================================
Converts outputs from all strategy engines (ema_crossover, iron_condor, etc.)
into the standard signal dict format that the dashboard frontend expects.

Signal card format (matches existing frontend PlaceOrderModal):
{
    "id":           str   — unique signal ID (strategy+instrument+timestamp)
    "strategy":     str   — display name shown on card
    "instrument":   str   — NIFTY / BANKNIFTY / FINNIFTY
    "direction":    str   — "BUY" / "SELL"
    "signal_type":  str   — "CE" / "PE" / "FUT" / "SPREAD"
    "strike":       int   — option strike (None for futures)
    "expiry":       str   — expiry date string
    "entry_price":  float — suggested entry
    "stop_loss":    float — stop loss price
    "target":       float — target price
    "lots":         int   — recommended lots
    "score":        int   — signal strength 0-100
    "regime":       str   — current market regime
    "timestamp":    str   — IST timestamp signal was generated
    "expires_at":   str   — signal auto-expires after 30 min
    "auto_place":   bool  — whether to auto-place via broker.py
    "placed":       bool  — whether order has been placed
    "status":       str   — "ACTIVE" / "EXPIRED" / "PLACED" / "CANCELLED"
}

Usage (in multistrategy.py):
    from algo.signal_adapter import SignalAdapter
    adapter = SignalAdapter(broker, risk_manager)
    adapter.register_all_strategies()
    adapter.start()   # starts background loop — pushes signals via WebSocket
"""

from __future__ import annotations
import uuid
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.regime_filter import Regime, detect_regime, iv_rank
from strategies.risk_manager import RiskManager
from strategies.ema_crossover import generate_signals as ema_signals, INSTRUMENT_PARAMS as EMA_PARAMS
from strategies.iron_condor import IronCondorStrategy, INSTRUMENT_CONDOR_PARAMS
from strategies.pcr_mean_reversion import generate_pcr_signals, INSTRUMENT_PCR_PARAMS
from strategies.short_straddle import ShortStraddleStrategy, INSTRUMENT_STRADDLE_PARAMS
from strategies.credit_spread import CreditSpreadStrategy, INSTRUMENT_SPREAD_PARAMS
from strategies.calendar_spread import CalendarSpreadStrategy, INSTRUMENT_CALENDAR_PARAMS
from strategies.vwap_reversal import compute_vwap_signals, INSTRUMENT_VWAP_PARAMS
from strategies.momentum_breakout import compute_breakout_signals, INSTRUMENT_MOMENTUM_PARAMS

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

SIGNAL_TTL_MINUTES = 30       # Signals expire after 30 min
AUTO_PLACE_STRATEGIES = {     # Set True to auto-place, False for card-only
    "EMA_Crossover":      True,
    "IronCondor":         True,
    "PCR_MeanReversion":  True,
    "ShortStraddle":      True,
    "CreditSpread":       True,
    "Calendar_Spread":    True,
    "VWAP_Reversal":      True,
    "Momentum_Breakout":  True,
}


def _now_ist() -> datetime:
    return datetime.now(IST)


def _signal_id(strategy: str, instrument: str) -> str:
    return f"{strategy}_{instrument}_{int(time.time())}"


def _expires_at(minutes: int = SIGNAL_TTL_MINUTES) -> str:
    return (_now_ist() + timedelta(minutes=minutes)).isoformat()


class SignalStore:
    """
    Thread-safe in-memory signal store.
    Frontend polls GET /signals/ which reads from here.
    WebSocket pushes new signals on write.
    """

    def __init__(self):
        self._signals: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._on_new_signal: Optional[Callable] = None  # WebSocket push callback

    def set_ws_callback(self, callback: Callable):
        """Register a WebSocket push callback (set in app.py)."""
        self._on_new_signal = callback

    def add(self, signal: dict) -> str:
        with self._lock:
            sid = signal["id"]
            self._signals[sid] = signal
        if self._on_new_signal:
            try:
                self._on_new_signal(signal)
            except Exception as e:
                logger.warning(f"WS push failed: {e}")
        logger.info(f"SIGNAL: [{signal['strategy']}] {signal['direction']} {signal['instrument']} score={signal['score']}")
        return sid

    def get_all_active(self) -> List[dict]:
        now = _now_ist()
        with self._lock:
            active = []
            for sid, sig in list(self._signals.items()):
                try:
                    expires = datetime.fromisoformat(sig["expires_at"])
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=IST)
                    if now > expires or sig["status"] in ("EXPIRED", "CANCELLED"):
                        sig["status"] = "EXPIRED"
                    if sig["status"] == "ACTIVE":
                        active.append(sig)
                except Exception:
                    active.append(sig)
            return active

    def mark_placed(self, signal_id: str, order_id: str):
        with self._lock:
            if signal_id in self._signals:
                self._signals[signal_id]["placed"] = True
                self._signals[signal_id]["status"] = "PLACED"
                self._signals[signal_id]["order_id"] = order_id

    def mark_expired(self, signal_id: str):
        with self._lock:
            if signal_id in self._signals:
                self._signals[signal_id]["status"] = "EXPIRED"

    def cancel(self, signal_id: str):
        with self._lock:
            if signal_id in self._signals:
                self._signals[signal_id]["status"] = "CANCELLED"

    def is_expired(self, signal_id: str) -> bool:
        sig = self._signals.get(signal_id)
        if not sig:
            return True
        try:
            expires = datetime.fromisoformat(sig["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=IST)
            return _now_ist() > expires
        except Exception:
            return False


# ─────────────────────────────────────────────
#  SIGNAL BUILDERS (one per strategy type)
# ─────────────────────────────────────────────

def build_ema_signal(
    instrument: str, direction: int, entry: float,
    atr: float, lots: int, regime: Regime, score: int = 75
) -> dict:
    cfg = EMA_PARAMS.get(instrument)
    stop = entry - 2.5 * atr if direction == 1 else entry + 2.5 * atr
    target = entry + 4.0 * atr if direction == 1 else entry - 4.0 * atr
    return {
        "id":           _signal_id("EMA_Crossover", instrument),
        "strategy":     "EMA_Crossover",
        "instrument":   instrument,
        "direction":    "BUY" if direction == 1 else "SELL",
        "signal_type":  "FUT",
        "strike":       None,
        "expiry":       None,
        "entry_price":  round(entry, 2),
        "stop_loss":    round(stop, 2),
        "target":       round(target, 2),
        "lots":         lots,
        "score":        score,
        "regime":       regime.value,
        "timestamp":    _now_ist().isoformat(),
        "expires_at":   _expires_at(),
        "auto_place":   AUTO_PLACE_STRATEGIES["EMA_Crossover"],
        "placed":       False,
        "status":       "ACTIVE",
    }


def build_options_signal(
    strategy: str, instrument: str, direction: str,
    strike: int, option_type: str, expiry: str,
    entry: float, stop: float, target: float,
    lots: int, regime: Regime, score: int = 70
) -> dict:
    signal_type = option_type.upper()  # "CE" or "PE"
    return {
        "id":           _signal_id(strategy, instrument),
        "strategy":     strategy,
        "instrument":   instrument,
        "direction":    direction,
        "signal_type":  signal_type,
        "strike":       strike,
        "expiry":       expiry,
        "entry_price":  round(entry, 2),
        "stop_loss":    round(stop, 2),
        "target":       round(target, 2),
        "lots":         lots,
        "score":        score,
        "regime":       regime.value,
        "timestamp":    _now_ist().isoformat(),
        "expires_at":   _expires_at(),
        "auto_place":   AUTO_PLACE_STRATEGIES.get(strategy, False),
        "placed":       False,
        "status":       "ACTIVE",
    }


def build_spread_signal(
    strategy: str, instrument: str,
    legs: List[dict],   # [{strike, option_type, action, price}, ...]
    net_credit: float, stop: float, target: float,
    lots: int, regime: Regime, score: int = 65
) -> dict:
    return {
        "id":           _signal_id(strategy, instrument),
        "strategy":     strategy,
        "instrument":   instrument,
        "direction":    "SELL",   # All spreads are net-short or neutral
        "signal_type":  "SPREAD",
        "strike":       None,
        "expiry":       None,
        "legs":         legs,     # Multi-leg — frontend renders each leg
        "entry_price":  round(net_credit, 2),
        "stop_loss":    round(stop, 2),
        "target":       round(target, 2),
        "lots":         lots,
        "score":        score,
        "regime":       regime.value,
        "timestamp":    _now_ist().isoformat(),
        "expires_at":   _expires_at(),
        "auto_place":   AUTO_PLACE_STRATEGIES.get(strategy, False),
        "placed":       False,
        "status":       "ACTIVE",
    }


# ─────────────────────────────────────────────
#  SIGNAL ADAPTER (main class)
# ─────────────────────────────────────────────

class SignalAdapter:
    """
    Runs all strategies on each bar and pushes signals to SignalStore.
    Plug into multistrategy.py main loop by calling on_bar() each candle.

    Integration in multistrategy.py:
        adapter = SignalAdapter(broker, risk_manager, signal_store)
        # In your bar loop:
        new_signals = adapter.on_bar(ohlcv_data, instrument)
        # new_signals are already in SignalStore — frontend gets them via API/WS
    """

    def __init__(self, broker, risk_manager: RiskManager, signal_store: SignalStore):
        self.broker = broker
        self.rm = risk_manager
        self.store = signal_store
        self._last_signal: Dict[str, str] = {}   # strategy+instrument → last signal direction

    def on_bar(self, ohlcv_df: pd.DataFrame, instrument: str,
               pcr_df: pd.DataFrame = None, iv_series: pd.Series = None,
               option_chain: dict = None) -> List[dict]:
        """
        Call on every bar close. Pass all available data.
        Returns list of new signals generated this bar.
        """
        if self.rm.circuit_breaker_hit():
            return []

        ivr = iv_rank(iv_series) if iv_series is not None else 40.0
        regime = detect_regime(ohlcv_df.iloc[-60:], ivp=ivr)
        new_signals = []

        # 1. EMA Crossover
        new_signals += self._check_ema(ohlcv_df, instrument, regime, ivr)

        # 2. VWAP Reversal (intraday — only on 5-min data)
        if len(ohlcv_df) > 50 and hasattr(ohlcv_df.index, 'hour'):
            new_signals += self._check_vwap(ohlcv_df, instrument, regime)

        # 3. Momentum Breakout
        new_signals += self._check_momentum(ohlcv_df, instrument, regime, option_chain)

        # 4. PCR Mean Reversion
        if pcr_df is not None and iv_series is not None:
            new_signals += self._check_pcr(pcr_df, ohlcv_df, iv_series, instrument, regime)

        # 5. Options strategies (require option chain)
        if option_chain:
            new_signals += self._check_iron_condor(ohlcv_df, instrument, regime, ivr, option_chain)
            new_signals += self._check_short_straddle(ohlcv_df, instrument, regime, ivr, option_chain)
            new_signals += self._check_credit_spread(ohlcv_df, instrument, regime, ivr, option_chain)
            new_signals += self._check_calendar_spread(ohlcv_df, instrument, regime, ivr, option_chain)

        # Add to store + auto-place if configured
        for sig in new_signals:
            self.store.add(sig)
            if sig["auto_place"] and not sig["placed"]:
                self._auto_place(sig)

        return new_signals

    # ── EMA ───────────────────────────────────────────────────────────────

    def _check_ema(self, df, instrument, regime, ivr) -> list:
        if regime == Regime.HIGH_VOL:
            return []
        cfg = EMA_PARAMS.get(instrument)
        if not cfg:
            return []
        try:
            signals_df = ema_signals(df, cfg)
            latest = signals_df.iloc[-1]
            sig = int(latest.get("signal", 0))
            if sig == 0:
                return []

            key = f"EMA_{instrument}"
            direction_str = "BUY" if sig == 1 else "SELL"
            if self._last_signal.get(key) == direction_str:
                return []    # Don't repeat same direction

            price = float(df["close"].iloc[-1])
            atr = float(latest.get("atr", price * 0.005))
            score = 80 if float(latest.get("adx", 0)) > 28 else 70
            lots = self.rm.position_size(f"EMA_Crossover_{instrument}", instrument,
                                          price, atr, cfg.lot_size)
            if lots == 0:
                return []

            self._last_signal[key] = direction_str
            return [build_ema_signal(instrument, sig, price, atr, lots, regime, score)]
        except Exception as e:
            logger.warning(f"EMA signal check failed {instrument}: {e}")
            return []

    # ── VWAP ──────────────────────────────────────────────────────────────

    def _check_vwap(self, df, instrument, regime) -> list:
        cfg = INSTRUMENT_VWAP_PARAMS.get(instrument)
        if not cfg or not self.rm.allow_trade(f"VWAP_Reversal_{instrument}"):
            return []
        try:
            df_v = compute_vwap_signals(df, cfg)
            latest = df_v.iloc[-1]
            price = float(latest["close"])
            vwap = float(latest["vwap"])
            vwap_std = float(latest.get("vwap_std", price * 0.003))
            stop_dist = cfg.vwap_std_stop * vwap_std

            if latest.get("long_signal"):
                stop = price - stop_dist
                target = vwap
                lots = self.rm.position_size(f"VWAP_Reversal_{instrument}", instrument,
                                              price, stop_dist, cfg.lot_size)
                if lots == 0:
                    return []
                return [build_options_signal(
                    "VWAP_Reversal", instrument, "BUY", None, "FUT", "INTRADAY",
                    price, stop, target, lots, regime, score=72
                )]
            elif latest.get("short_signal"):
                stop = price + stop_dist
                target = vwap
                lots = self.rm.position_size(f"VWAP_Reversal_{instrument}", instrument,
                                              price, stop_dist, cfg.lot_size)
                if lots == 0:
                    return []
                return [build_options_signal(
                    "VWAP_Reversal", instrument, "SELL", None, "FUT", "INTRADAY",
                    price, stop, target, lots, regime, score=72
                )]
        except Exception as e:
            logger.warning(f"VWAP check failed {instrument}: {e}")
        return []

    # ── Momentum Breakout ─────────────────────────────────────────────────

    def _check_momentum(self, df, instrument, regime, option_chain) -> list:
        cfg = INSTRUMENT_MOMENTUM_PARAMS.get(instrument)
        if not cfg or not self.rm.allow_trade(f"Momentum_Breakout_{instrument}"):
            return []
        try:
            df_m = compute_breakout_signals(df, cfg)
            latest = df_m.iloc[-1]
            price = float(df["close"].iloc[-1])
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)

            if latest.get("breakout_up") and regime in (Regime.TRENDING_UP, Regime.RANGING):
                strike = int(round((price + cfg.otm_strikes * step) / step) * step)
                entry = option_chain.get((strike, "call", cfg.dte_target), price * 0.02) if option_chain else price * 0.02
                stop = entry * (1 - cfg.stop_loss_pct / 100)
                target = entry * (1 + cfg.profit_target_pct / 100)
                lots = max(1, self.rm.position_size(f"Momentum_Breakout_{instrument}", instrument,
                                                     price, entry * 0.4, cfg.lot_size))
                return [build_options_signal(
                    "Momentum_Breakout", instrument, "BUY", strike, "CE",
                    f"WEEKLY+{cfg.dte_target}DTE", entry, stop, target, lots, regime, score=75
                )]

            if latest.get("breakout_dn") and regime in (Regime.TRENDING_DOWN, Regime.RANGING):
                strike = int(round((price - cfg.otm_strikes * step) / step) * step)
                entry = option_chain.get((strike, "put", cfg.dte_target), price * 0.02) if option_chain else price * 0.02
                stop = entry * (1 - cfg.stop_loss_pct / 100)
                target = entry * (1 + cfg.profit_target_pct / 100)
                lots = max(1, self.rm.position_size(f"Momentum_Breakout_{instrument}", instrument,
                                                     price, entry * 0.4, cfg.lot_size))
                return [build_options_signal(
                    "Momentum_Breakout", instrument, "BUY", strike, "PE",
                    f"WEEKLY+{cfg.dte_target}DTE", entry, stop, target, lots, regime, score=75
                )]
        except Exception as e:
            logger.warning(f"Momentum check failed {instrument}: {e}")
        return []

    # ── PCR ───────────────────────────────────────────────────────────────

    def _check_pcr(self, pcr_df, ohlcv_df, iv_series, instrument, regime) -> list:
        if not self.rm.allow_trade(f"PCR_MeanReversion_{instrument}"):
            return []
        try:
            from strategies.pcr_mean_reversion import INSTRUMENT_PCR_PARAMS, generate_pcr_signals
            cfg = INSTRUMENT_PCR_PARAMS.get(instrument)
            if not cfg:
                return []
            sig_df = generate_pcr_signals(pcr_df, ohlcv_df, iv_series, cfg)
            if sig_df.empty:
                return []
            latest = sig_df.iloc[-1]
            if latest.get("signal", 0) == 0:
                return []

            price = float(ohlcv_df["close"].iloc[-1])
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)
            direction = latest["direction"]
            opt_type = "call" if direction == "bullish" else "put"
            strike = int(round((price + (cfg.otm_distance if opt_type == "call" else -cfg.otm_distance)) / step) * step)
            entry = price * 0.015   # fallback if no option chain
            stop = entry * (1 - cfg.stop_loss_pct / 100)
            target = entry * (1 + cfg.profit_target_pct / 100)
            score = int(latest.get("score", 65))
            lots = max(1, self.rm.position_size(f"PCR_MeanReversion_{instrument}", instrument,
                                                 price, entry * 0.25, cfg.lot_size))
            return [build_options_signal(
                "PCR_MeanReversion", instrument, "BUY", strike,
                "CE" if opt_type == "call" else "PE",
                f"{cfg.dte_entry_max}DTE", entry, stop, target, lots, regime, score
            )]
        except Exception as e:
            logger.warning(f"PCR check failed {instrument}: {e}")
        return []

    # ── Iron Condor ───────────────────────────────────────────────────────

    def _check_iron_condor(self, ohlcv_df, instrument, regime, ivr, option_chain) -> list:
        if not self.rm.allow_trade(f"IronCondor_{instrument}"):
            return []
        try:
            cfg = INSTRUMENT_CONDOR_PARAMS.get(instrument)
            if not cfg:
                return []
            S = float(ohlcv_df["close"].iloc[-1])
            T = option_chain.get("dte", 30) / 365
            sigma = option_chain.get("iv", 0.15)
            r = 0.065
            ic = IronCondorStrategy(cfg, self.rm)
            if not ic.should_enter(S, T, ivr, regime):
                return []
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)
            expiry = option_chain.get("expiry", "")
            legs = [
                {"strike": int(round((S + cfg.wing_width) / step) * step),   "option_type": "CE", "action": "SELL"},
                {"strike": int(round((S + cfg.wing_width * 2) / step) * step),"option_type": "CE", "action": "BUY"},
                {"strike": int(round((S - cfg.wing_width) / step) * step),   "option_type": "PE", "action": "SELL"},
                {"strike": int(round((S - cfg.wing_width * 2) / step) * step),"option_type": "PE", "action": "BUY"},
            ]
            net_credit = S * 0.005   # approximation
            stop = net_credit * cfg.stop_loss_multiplier
            target = net_credit * cfg.profit_target_pct
            return [build_spread_signal(
                "IronCondor", instrument, legs, net_credit, stop, target,
                1, regime, score=70
            )]
        except Exception as e:
            logger.warning(f"IronCondor check failed {instrument}: {e}")
        return []

    # ── Short Straddle ────────────────────────────────────────────────────

    def _check_short_straddle(self, ohlcv_df, instrument, regime, ivr, option_chain) -> list:
        if not self.rm.allow_trade(f"ShortStraddle_{instrument}"):
            return []
        try:
            cfg = INSTRUMENT_STRADDLE_PARAMS.get(instrument)
            if not cfg:
                return []
            S = float(ohlcv_df["close"].iloc[-1])
            T = option_chain.get("dte", 4) / 365
            sigma = option_chain.get("iv", 0.15)
            strat = ShortStraddleStrategy(cfg, self.rm)
            if not strat.should_enter(S, T, ivr, regime):
                return []
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)
            strike = int(round(S / step) * step)
            expiry = option_chain.get("expiry", "WEEKLY")
            legs = [
                {"strike": strike, "option_type": "CE", "action": "SELL"},
                {"strike": strike, "option_type": "PE", "action": "SELL"},
            ]
            net_credit = S * 0.008
            stop = net_credit * cfg.stop_loss_multiplier
            target = net_credit * cfg.profit_target_pct
            return [build_spread_signal(
                "ShortStraddle", instrument, legs, net_credit, stop, target,
                1, regime, score=68
            )]
        except Exception as e:
            logger.warning(f"ShortStraddle check failed {instrument}: {e}")
        return []

    # ── Credit Spread ─────────────────────────────────────────────────────

    def _check_credit_spread(self, ohlcv_df, instrument, regime, ivr, option_chain) -> list:
        if not self.rm.allow_trade(f"CreditSpread_{instrument}"):
            return []
        try:
            cfg = INSTRUMENT_SPREAD_PARAMS.get(instrument)
            if not cfg:
                return []
            S = float(ohlcv_df["close"].iloc[-1])
            T = option_chain.get("dte", 25) / 365
            sigma = option_chain.get("iv", 0.15)
            strat = CreditSpreadStrategy(cfg, self.rm)
            if not strat.should_enter(S, T, ivr, regime):
                return []
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)
            spread_type = "bull_put" if regime == Regime.TRENDING_UP else "bear_call"
            opt_type = "PE" if spread_type == "bull_put" else "CE"
            short_strike = int(round((S - 200 if spread_type == "bull_put" else S + 200) / step) * step)
            long_strike = int(round((short_strike - cfg.wing_width if spread_type == "bull_put" else short_strike + cfg.wing_width) / step) * step)
            expiry = option_chain.get("expiry", "")
            legs = [
                {"strike": short_strike, "option_type": opt_type, "action": "SELL"},
                {"strike": long_strike,  "option_type": opt_type, "action": "BUY"},
            ]
            net_credit = S * 0.003
            stop = net_credit * cfg.stop_loss_multiplier
            target = net_credit * cfg.profit_target_pct
            return [build_spread_signal(
                "CreditSpread", instrument, legs, net_credit, stop, target,
                1, regime, score=67
            )]
        except Exception as e:
            logger.warning(f"CreditSpread check failed {instrument}: {e}")
        return []

    # ── Calendar Spread ───────────────────────────────────────────────────

    def _check_calendar_spread(self, ohlcv_df, instrument, regime, ivr, option_chain) -> list:
        if not self.rm.allow_trade(f"Calendar_Spread_{instrument}"):
            return []
        try:
            cfg = INSTRUMENT_CALENDAR_PARAMS.get(instrument)
            if not cfg:
                return []
            S = float(ohlcv_df["close"].iloc[-1])
            near_T = option_chain.get("near_dte", 10) / 365
            far_T  = option_chain.get("far_dte", 35) / 365
            near_IV = option_chain.get("near_iv", 0.15)
            far_IV  = option_chain.get("far_iv", 0.17)
            strat = CalendarSpreadStrategy(cfg, self.rm)
            can_enter, reason = strat.should_enter(
                S, near_T, far_T, near_IV, far_IV,
                ivr, regime, pd.Timestamp.now()
            )
            if not can_enter:
                return []
            step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(instrument, 50)
            strike = int(round(S / step) * step)
            near_expiry = option_chain.get("near_expiry", "")
            far_expiry  = option_chain.get("far_expiry", "")
            opt_label = cfg.option_type.upper()
            legs = [
                {"strike": strike, "option_type": opt_label, "action": "SELL", "expiry": near_expiry},
                {"strike": strike, "option_type": opt_label, "action": "BUY",  "expiry": far_expiry},
            ]
            net_debit = S * 0.004
            stop = net_debit * (cfg.stop_loss_pct / 100)
            target = net_debit * (cfg.profit_target_pct / 100)
            return [build_spread_signal(
                "Calendar_Spread", instrument, legs, net_debit, stop, target,
                1, regime, score=65
            )]
        except Exception as e:
            logger.warning(f"Calendar check failed {instrument}: {e}")
        return []

    # ── Auto-place ────────────────────────────────────────────────────────

    def _auto_place(self, signal: dict):
        """Place order via broker.py and mark signal as placed."""
        try:
            if signal["signal_type"] == "SPREAD":
                legs = signal.get("legs", [])
                order_ids = []
                for leg in legs:
                    result = self.broker.place_order(
                        exchange="NFO",
                        tradingsymbol=self._build_symbol(
                            signal["instrument"], leg["strike"],
                            leg["option_type"], signal.get("expiry", "")
                        ),
                        transaction_type=leg["action"],
                        quantity=signal["lots"] * self._lot_size(signal["instrument"]),
                        order_type="MARKET",
                        product="MIS",
                        tag=signal["id"][:20],
                    )
                    if result.get("order_id"):
                        order_ids.append(result["order_id"])
                if order_ids:
                    self.store.mark_placed(signal["id"], ",".join(order_ids))
                    logger.info(f"AUTO-PLACED {signal['strategy']} {signal['instrument']}: orders {order_ids}")
            else:
                result = self.broker.place_order(
                    exchange="NFO",
                    tradingsymbol=self._build_symbol(
                        signal["instrument"], signal.get("strike"),
                        signal.get("signal_type"), signal.get("expiry", "")
                    ),
                    transaction_type=signal["direction"],
                    quantity=signal["lots"] * self._lot_size(signal["instrument"]),
                    order_type="MARKET",
                    product="MIS",
                    tag=signal["id"][:20],
                )
                if result.get("order_id"):
                    self.store.mark_placed(signal["id"], result["order_id"])
                    logger.info(f"AUTO-PLACED {signal['strategy']} {signal['instrument']}: {result['order_id']}")
        except Exception as e:
            logger.error(f"Auto-place failed for {signal['id']}: {e}")

    def _build_symbol(self, instrument: str, strike, opt_type: str, expiry: str) -> str:
        """Build NSE tradingsymbol. Override with your existing symbol builder."""
        if not strike or opt_type == "FUT":
            return f"{instrument}FUT"
        return f"{instrument}{expiry}{strike}{opt_type}"

    def _lot_size(self, instrument: str) -> int:
        return {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(instrument, 50)
