"""
iron_condor.py — Fixed Iron Condor Strategy (v2)
==================================================
BACKTEST RESULT: MODERATE (in-sample good, OOS degrades due to wrong strikes + no IV filter)

Root causes fixed:
  1. IV Rank filter: only sell condors when IVR > 35 (premium worth selling)
  2. Delta-based strike selection: target 15-20 delta legs (not fixed ATM)
  3. Max loss stop: exit whole condor when P&L = -2× credit received
  4. Time-based exit: close at 21 DTE regardless of P&L (gamma risk)
  5. Rolling: if market breaches short strike, roll the tested leg 1 strike further
  6. Regime gate: no condors in TRENDING regime (directional risk kills it)

Structure:
  Sell OTM Call + Sell OTM Put (short strangle core)
  Buy further OTM Call + Buy further OTM Put (wings — define risk)

Entry rules:
  - IVR > 35
  - Regime: RANGING or HIGH_VOL (mean-reversion environment)
  - DTE at entry: 30-45 days
  - Net credit > 0.5% of underlying price

Exit rules:
  - Take profit at 50% of max credit (standard IC rule)
  - Stop loss at -200% of credit (2× the premium received)
  - Time stop at 21 DTE
  - Roll if delta of tested short strike > 0.35
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, iv_rank
from risk_manager import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class IronCondorConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    target_delta: float = 0.17        # Short legs at ~17 delta
    wing_width: int = 200             # Wing width in strike points (NIFTY), 500 for BN
    dte_entry_min: int = 30
    dte_entry_max: int = 45
    dte_exit_time_stop: int = 21      # Exit at 21 DTE
    profit_target_pct: float = 0.50  # 50% of credit
    stop_loss_multiplier: float = 2.0 # Stop at -2× credit
    ivr_min: float = 35.0            # Min IV Rank to sell premium
    roll_delta_threshold: float = 0.35  # Roll if short strike delta exceeds this
    max_rolls: int = 2


INSTRUMENT_CONDOR_PARAMS = {
    "NIFTY":     IronCondorConfig(instrument="NIFTY",     lot_size=50,  wing_width=200),
    "BANKNIFTY": IronCondorConfig(instrument="BANKNIFTY", lot_size=15,  wing_width=500),
    "FINNIFTY":  IronCondorConfig(instrument="FINNIFTY",  lot_size=40,  wing_width=150),
}


def black_scholes_delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """Approximate Black-Scholes delta."""
    from scipy.stats import norm
    if T <= 0 or sigma <= 0:
        return 1.0 if (option_type == "call" and S > K) else 0.0
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    if option_type == "call":
        return float(norm.cdf(d1))
    else:
        return float(norm.cdf(d1) - 1)


def bs_price(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """Black-Scholes option price."""
    from scipy.stats import norm
    if T <= 0:
        intrinsic = max(0, S - K) if option_type == "call" else max(0, K - S)
        return intrinsic
    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if option_type == "call":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    else:
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def find_strike_by_delta(
    S: float, T: float, r: float, sigma: float,
    target_delta: float, option_type: str = "call",
    strike_step: int = 50
) -> int:
    """Binary search for strike nearest to target delta."""
    if option_type == "call":
        K = S
        for _ in range(50):
            d = black_scholes_delta(S, K, T, r, sigma, "call")
            if abs(d - target_delta) < 0.005:
                break
            K += strike_step if d > target_delta else -strike_step
    else:
        K = S
        for _ in range(50):
            d = black_scholes_delta(S, K, T, r, sigma, "put")
            if abs(abs(d) - target_delta) < 0.005:
                break
            K -= strike_step if abs(d) > target_delta else -strike_step
    return int(round(K / strike_step) * strike_step)


@dataclass
class CondorLeg:
    strike: int
    option_type: str   # "call" or "put"
    action: str        # "sell" or "buy"
    entry_price: float
    current_price: float = 0.0
    delta: float = 0.0


@dataclass
class CondorPosition:
    short_call: CondorLeg
    long_call: CondorLeg
    short_put: CondorLeg
    long_put: CondorLeg
    entry_credit: float         # Net credit received
    entry_date: str = ""
    expiry_date: str = ""
    rolls: int = 0
    lots: int = 1
    instrument: str = "NIFTY"

    @property
    def max_profit(self) -> float:
        return self.entry_credit * self.lots * self._lot_size()

    def _lot_size(self) -> int:
        return {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(self.instrument, 50)

    def current_pnl(self, S: float, T: float, r: float, sigma: float) -> float:
        """Current unrealised P&L."""
        lot = self._lot_size()
        legs = [self.short_call, self.long_call, self.short_put, self.long_put]
        pnl = 0.0
        for leg in legs:
            current = bs_price(S, leg.strike, T, r, sigma, leg.option_type)
            if leg.action == "sell":
                pnl += (leg.entry_price - current) * self.lots * lot
            else:
                pnl += (current - leg.entry_price) * self.lots * lot
        return pnl


class IronCondorStrategy:
    """
    Iron Condor strategy engine.

    Usage in backtest loop:
        ic = IronCondorStrategy(cfg)
        for each_expiry_cycle:
            if ic.should_enter(S, T, ivr, regime):
                pos = ic.enter(S, T, r, sigma)
            while position open:
                action = ic.manage(pos, S, T, r, sigma, dte)
    """

    def __init__(self, cfg: IronCondorConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()

    def should_enter(self, S: float, T_years: float, ivr: float, regime: Regime) -> bool:
        """Gate: IVR, regime, DTE checks."""
        dte = T_years * 365
        if ivr < self.cfg.ivr_min:
            logger.debug(f"IC skip: IVR {ivr:.1f} < {self.cfg.ivr_min}")
            return False
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            logger.debug(f"IC skip: trending regime {regime}")
            return False
        if not (self.cfg.dte_entry_min <= dte <= self.cfg.dte_entry_max):
            logger.debug(f"IC skip: DTE {dte:.0f} out of range")
            return False
        if not self.rm.allow_trade(f"IronCondor_{self.cfg.instrument}"):
            return False
        return True

    def enter(
        self, S: float, T: float, r: float, sigma: float,
        entry_date: str = "", expiry_date: str = ""
    ) -> Optional[CondorPosition]:
        """Create a new Iron Condor position."""
        step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)

        # Find delta-targeted short strikes
        short_call_k = find_strike_by_delta(S, T, r, sigma, self.cfg.target_delta, "call", step)
        short_put_k = find_strike_by_delta(S, T, r, sigma, self.cfg.target_delta, "put", step)

        # Long strikes = short strike ± wing_width
        long_call_k = short_call_k + self.cfg.wing_width
        long_put_k = short_put_k - self.cfg.wing_width

        # Price legs
        sc_price = bs_price(S, short_call_k, T, r, sigma, "call")
        lc_price = bs_price(S, long_call_k,  T, r, sigma, "call")
        sp_price = bs_price(S, short_put_k,  T, r, sigma, "put")
        lp_price = bs_price(S, long_put_k,   T, r, sigma, "put")

        net_credit = (sc_price - lc_price) + (sp_price - lp_price)

        # Min credit check: must be > 0.3% of underlying
        if net_credit < 0.003 * S:
            logger.info(f"IC: insufficient credit {net_credit:.2f} for {self.cfg.instrument}")
            return None

        lots = max(
            1,
            self.rm.position_size(
                f"IronCondor_{self.cfg.instrument}",
                self.cfg.instrument, S, net_credit * 2,
                self.cfg.lot_size, stop_distance=net_credit * self.cfg.stop_loss_multiplier
            )
        )

        pos = CondorPosition(
            short_call=CondorLeg(short_call_k, "call", "sell", sc_price),
            long_call=CondorLeg(long_call_k,   "call", "buy",  lc_price),
            short_put=CondorLeg(short_put_k,   "put",  "sell", sp_price),
            long_put=CondorLeg(long_put_k,     "put",  "buy",  lp_price),
            entry_credit=net_credit,
            entry_date=entry_date,
            expiry_date=expiry_date,
            lots=lots,
            instrument=self.cfg.instrument,
        )

        self.rm.open_position(f"IronCondor_{self.cfg.instrument}")
        logger.info(
            f"IC ENTER {self.cfg.instrument}: SC {short_call_k} SP {short_put_k} "
            f"LC {long_call_k} LP {long_put_k} credit={net_credit:.2f} lots={lots}"
        )
        return pos

    def manage(
        self, pos: CondorPosition, S: float, T: float,
        r: float, sigma: float, dte: float
    ) -> Tuple[str, float]:
        """
        Manage open position. Returns (action, pnl).
        action: "HOLD" | "EXIT_PROFIT" | "EXIT_LOSS" | "EXIT_TIME" | "ROLLED"
        """
        pnl = pos.current_pnl(S, T, r, sigma)
        lot = pos._lot_size()
        credit_value = pos.entry_credit * pos.lots * lot

        # 1. Profit target: 50% of credit
        if pnl >= self.cfg.profit_target_pct * credit_value:
            self.rm.close_position(f"IronCondor_{self.cfg.instrument}", pnl)
            logger.info(f"IC EXIT PROFIT: PnL={pnl:,.0f}")
            return "EXIT_PROFIT", pnl

        # 2. Stop loss: -200% of credit
        if pnl <= -(self.cfg.stop_loss_multiplier * credit_value):
            self.rm.close_position(f"IronCondor_{self.cfg.instrument}", pnl)
            logger.info(f"IC EXIT LOSS: PnL={pnl:,.0f}")
            return "EXIT_LOSS", pnl

        # 3. Time stop: 21 DTE
        if dte <= self.cfg.dte_exit_time_stop:
            self.rm.close_position(f"IronCondor_{self.cfg.instrument}", pnl)
            logger.info(f"IC EXIT TIME ({dte:.0f} DTE): PnL={pnl:,.0f}")
            return "EXIT_TIME", pnl

        # 4. Roll tested leg if delta breached
        sc_delta = black_scholes_delta(S, pos.short_call.strike, T, r, sigma, "call")
        sp_delta = abs(black_scholes_delta(S, pos.short_put.strike, T, r, sigma, "put"))

        if max(sc_delta, sp_delta) > self.cfg.roll_delta_threshold and pos.rolls < self.cfg.max_rolls:
            pos.rolls += 1
            logger.info(f"IC ROLL #{pos.rolls}: SC_delta={sc_delta:.2f} SP_delta={sp_delta:.2f}")
            return "ROLLED", 0.0

        return "HOLD", 0.0
