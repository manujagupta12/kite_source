"""
credit_spread.py — Directional Credit Spreads (REPLACES Calendar_Spread)
=========================================================================
REPLACES: Calendar_Spread (100% failure — win rate 20-33%, all windows negative)

Calendar_Spread failure root causes:
  - No trend alignment: calendars are vega trades, need stable underlying
  - Wrong skew assumptions: Indian F&O has inverted skew calendar dynamics
  - DTE mismatch: front-month decayed slower than expected
  - No stop losses enforced (drawdowns up to 2711%)

New strategy: Trend-aligned Credit Spreads
==========================================
Two sub-strategies (auto-selected by regime):

1. BULL PUT SPREAD (in uptrend)
   - Sell OTM Put + Buy further OTM Put
   - Net credit
   - Profit: index stays flat or rises

2. BEAR CALL SPREAD (in downtrend)
   - Sell OTM Call + Buy further OTM Call
   - Net credit
   - Profit: index stays flat or falls

Entry rules:
  - ADX > 20 confirms trend direction
  - Short leg at 20-25 delta
  - Wing at 10 delta
  - DTE: 20-30 days
  - IVR > 25 (need some premium)

Exit rules:
  - Take profit: 60% of credit
  - Stop loss: -200% of credit (max loss = wing width - credit)
  - Time stop: 7 DTE
  - Trend reversal: if regime flips, exit immediately
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Literal
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, iv_rank
from risk_manager import RiskManager
from iron_condor import black_scholes_delta, bs_price, find_strike_by_delta

logger = logging.getLogger(__name__)


@dataclass
class CreditSpreadConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    short_delta: float = 0.22     # Short leg at ~22 delta
    long_delta: float = 0.10      # Long leg at ~10 delta (protection)
    dte_entry_min: int = 20
    dte_entry_max: int = 30
    dte_exit_time: int = 7        # Exit at 7 DTE
    profit_target_pct: float = 0.60   # 60% of credit
    stop_loss_multiplier: float = 2.0 # -200% of credit
    ivr_min: float = 25.0
    adx_min: float = 20.0         # Trend strength requirement


INSTRUMENT_SPREAD_PARAMS = {
    "NIFTY":     CreditSpreadConfig(instrument="NIFTY",     lot_size=50),
    "BANKNIFTY": CreditSpreadConfig(instrument="BANKNIFTY", lot_size=15, ivr_min=30.0),
    "FINNIFTY":  CreditSpreadConfig(instrument="FINNIFTY",  lot_size=40),
}


@dataclass
class SpreadPosition:
    spread_type: str       # "bull_put" or "bear_call"
    short_strike: int
    long_strike: int
    option_type: str       # "put" or "call"
    short_entry_price: float
    long_entry_price: float
    net_credit: float
    entry_date: str
    expiry_date: str
    lots: int = 1
    instrument: str = "NIFTY"
    entry_regime: Regime = Regime.UNKNOWN

    def _lot_size(self) -> int:
        return {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(self.instrument, 50)

    def current_pnl(self, S: float, T: float, r: float, sigma: float) -> float:
        lot = self._lot_size()
        short_now = bs_price(S, self.short_strike, T, r, sigma, self.option_type)
        long_now  = bs_price(S, self.long_strike,  T, r, sigma, self.option_type)
        # Sold short, bought long
        pnl = (self.short_entry_price - short_now - (long_now - self.long_entry_price))
        return pnl * self.lots * lot

    def max_profit_value(self) -> float:
        return self.net_credit * self.lots * self._lot_size()

    def max_loss_value(self) -> float:
        wing = abs(self.short_strike - self.long_strike)
        return (wing - self.net_credit) * self.lots * self._lot_size()


class CreditSpreadStrategy:
    """
    Directional credit spreads — replaces Calendar_Spread entirely.
    Regime-aligned: bull put in uptrend, bear call in downtrend.
    """

    def __init__(self, cfg: CreditSpreadConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()

    def select_spread_type(self, regime: Regime) -> Optional[str]:
        """Select spread type based on current regime."""
        if regime == Regime.TRENDING_UP:
            return "bull_put"      # Underlying rising → sell put spread (bullish)
        elif regime == Regime.TRENDING_DOWN:
            return "bear_call"     # Underlying falling → sell call spread (bearish)
        else:
            return None            # No spread in ranging or high vol

    def should_enter(self, S: float, T_years: float, ivr: float, regime: Regime) -> bool:
        dte = T_years * 365
        if ivr < self.cfg.ivr_min:
            return False
        if self.select_spread_type(regime) is None:
            return False
        if not (self.cfg.dte_entry_min <= dte <= self.cfg.dte_entry_max):
            return False
        return self.rm.allow_trade(f"CreditSpread_{self.cfg.instrument}")

    def enter(
        self, S: float, T: float, r: float, sigma: float,
        regime: Regime, entry_date: str = "", expiry_date: str = ""
    ) -> Optional[SpreadPosition]:
        spread_type = self.select_spread_type(regime)
        if not spread_type:
            return None

        step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)
        option_type = "put" if spread_type == "bull_put" else "call"

        # Short leg (higher delta = closer to money)
        short_k = find_strike_by_delta(S, T, r, sigma, self.cfg.short_delta, option_type, step)
        # Long leg (lower delta = further OTM)
        long_k = find_strike_by_delta(S, T, r, sigma, self.cfg.long_delta, option_type, step)

        # For put spread: short > long (short put OTM, long put further OTM)
        # For call spread: short < long (short call OTM, long call further OTM)
        if spread_type == "bull_put" and long_k >= short_k:
            long_k = short_k - step * 2
        if spread_type == "bear_call" and long_k <= short_k:
            long_k = short_k + step * 2

        short_price = bs_price(S, short_k, T, r, sigma, option_type)
        long_price  = bs_price(S, long_k,  T, r, sigma, option_type)
        net_credit = short_price - long_price

        if net_credit < 0.002 * S:
            logger.info(f"CreditSpread: credit {net_credit:.2f} too low")
            return None

        lots = max(1, self.rm.position_size(
            f"CreditSpread_{self.cfg.instrument}", self.cfg.instrument,
            S, net_credit, self.cfg.lot_size,
            stop_distance=net_credit * self.cfg.stop_loss_multiplier
        ))

        pos = SpreadPosition(
            spread_type=spread_type,
            short_strike=short_k,
            long_strike=long_k,
            option_type=option_type,
            short_entry_price=short_price,
            long_entry_price=long_price,
            net_credit=net_credit,
            entry_date=entry_date,
            expiry_date=expiry_date,
            lots=lots,
            instrument=self.cfg.instrument,
            entry_regime=regime,
        )
        self.rm.open_position(f"CreditSpread_{self.cfg.instrument}")
        logger.info(
            f"SPREAD ENTER {spread_type.upper()} {self.cfg.instrument}: "
            f"short={short_k} long={long_k} credit={net_credit:.2f} lots={lots}"
        )
        return pos

    def manage(
        self, pos: SpreadPosition, S: float, T: float,
        r: float, sigma: float, dte: float, current_regime: Regime
    ) -> Tuple[str, float]:
        """Returns (action, pnl)."""
        pnl = pos.current_pnl(S, T, r, sigma)
        max_profit = pos.max_profit_value()
        net_credit_value = pos.net_credit * pos.lots * pos._lot_size()

        # 1. Profit target: 60% of credit
        if pnl >= self.cfg.profit_target_pct * net_credit_value:
            self.rm.close_position(f"CreditSpread_{self.cfg.instrument}", pnl)
            logger.info(f"SPREAD EXIT PROFIT: PnL={pnl:,.0f}")
            return "EXIT_PROFIT", pnl

        # 2. Stop loss: -200% of credit
        if pnl <= -(self.cfg.stop_loss_multiplier * net_credit_value):
            self.rm.close_position(f"CreditSpread_{self.cfg.instrument}", pnl)
            logger.warning(f"SPREAD EXIT LOSS: PnL={pnl:,.0f}")
            return "EXIT_LOSS", pnl

        # 3. Time stop: 7 DTE
        if dte <= self.cfg.dte_exit_time:
            self.rm.close_position(f"CreditSpread_{self.cfg.instrument}", pnl)
            logger.info(f"SPREAD EXIT TIME: DTE={dte:.0f} PnL={pnl:,.0f}")
            return "EXIT_TIME", pnl

        # 4. Regime flip: exit if market direction reversed against the spread
        if pos.spread_type == "bull_put" and current_regime == Regime.TRENDING_DOWN:
            self.rm.close_position(f"CreditSpread_{self.cfg.instrument}", pnl)
            logger.info(f"SPREAD EXIT REGIME FLIP: PnL={pnl:,.0f}")
            return "EXIT_REGIME", pnl
        if pos.spread_type == "bear_call" and current_regime == Regime.TRENDING_UP:
            self.rm.close_position(f"CreditSpread_{self.cfg.instrument}", pnl)
            logger.info(f"SPREAD EXIT REGIME FLIP: PnL={pnl:,.0f}")
            return "EXIT_REGIME", pnl

        return "HOLD", 0.0
