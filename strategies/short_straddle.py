"""
short_straddle.py — Managed Short Straddle (REPLACES ATM_Strangle)
====================================================================
REPLACES: ATM_Strangle (100% failure — PF < 1, 4834% drawdown)

ATM_Strangle failure root causes:
  - No IV filter: selling premium when IV was already low (no edge)
  - No delta management: gamma risk unmanaged
  - Stop loss too wide / not enforced
  - BANKNIFTY: massive gap risk with only 15-share lot

New strategy: Managed Short Straddle with strict rules
======================================================
Entry:
  - Sell ATM Call + ATM Put (straddle, not strangle — more premium)
  - Only when IVR > 50 (selling expensive premium)
  - Regime: RANGING only (avoids directional moves)
  - Entry on weekly expiry (Thursday), hold max 3-4 days

Exit rules (ALL enforced strictly):
  1. Take profit: close at 25% of credit (faster profit booking)
  2. Stop loss: close at -150% of credit (hard limit)
  3. Delta stop: if net delta > 0.30, exit immediately (directional breach)
  4. Time stop: must exit by Wednesday EOD (1 day before expiry)
  5. Gap risk: if overnight gap > 1% of index, exit at open

Adjustments:
  - If net delta > 0.15 intraday: add a hedge (buy OTM option)
  - This converts it to a butterfly or broken-wing butterfly

Note: This replaces ATM_Strangle entirely. The strangle is riskier
with wider strikes and less premium. Straddle with tight stop is safer.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, iv_rank
from risk_manager import RiskManager
from iron_condor import black_scholes_delta, bs_price

logger = logging.getLogger(__name__)


@dataclass
class ShortStraddleConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    ivr_min: float = 50.0          # Only sell when IVR > 50
    dte_entry: int = 4             # Enter on Mon/Tue (4-5 DTE before Thurs expiry)
    dte_exit_latest: int = 1       # Must exit by 1 DTE
    profit_target_pct: float = 0.25    # Exit at 25% of credit
    stop_loss_multiplier: float = 1.5  # Stop at -150% of credit
    delta_stop: float = 0.30       # Exit if abs(net_delta) > 0.30
    delta_hedge: float = 0.15      # Add hedge if abs(net_delta) > 0.15
    gap_pct_stop: float = 0.01    # Exit if overnight gap > 1%
    max_loss_per_trade_pct: float = 0.5  # Max 0.5% of capital per trade loss


INSTRUMENT_STRADDLE_PARAMS = {
    "NIFTY":     ShortStraddleConfig(instrument="NIFTY",     lot_size=50),
    "BANKNIFTY": ShortStraddleConfig(instrument="BANKNIFTY", lot_size=15, ivr_min=55.0),
    "FINNIFTY":  ShortStraddleConfig(instrument="FINNIFTY",  lot_size=40),
}


@dataclass
class StraddlePosition:
    short_call_strike: int
    short_put_strike: int
    call_entry_price: float
    put_entry_price: float
    entry_credit: float        # = call_price + put_price
    entry_date: str
    expiry_date: str
    lots: int = 1
    instrument: str = "NIFTY"
    net_delta_at_entry: float = 0.0
    hedged: bool = False

    def _lot_size(self) -> int:
        return {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(self.instrument, 50)

    def current_pnl(self, S: float, T: float, r: float, sigma: float) -> float:
        lot = self._lot_size()
        call_now = bs_price(S, self.short_call_strike, T, r, sigma, "call")
        put_now  = bs_price(S, self.short_put_strike,  T, r, sigma, "put")
        # Sold both → profit when premiums decay
        pnl = (self.call_entry_price - call_now + self.put_entry_price - put_now) * self.lots * lot
        return pnl

    def net_delta(self, S: float, T: float, r: float, sigma: float) -> float:
        call_delta = black_scholes_delta(S, self.short_call_strike, T, r, sigma, "call")
        put_delta  = black_scholes_delta(S, self.short_put_strike,  T, r, sigma, "put")
        # Short call = -call_delta, short put = -put_delta
        return -(call_delta + put_delta)  # net portfolio delta


class ShortStraddleStrategy:
    """
    Managed Short Straddle — replaces ATM Strangle.
    Only trades in RANGING regime with high IVR.
    Strict loss controls prevent the catastrophic drawdowns seen in v1.
    """

    def __init__(self, cfg: ShortStraddleConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()

    def should_enter(self, S: float, T_years: float, ivr: float, regime: Regime,
                     prev_close: float = None) -> bool:
        """Gate all entry conditions."""
        dte = T_years * 365

        # IV must be elevated
        if ivr < self.cfg.ivr_min:
            logger.debug(f"Straddle skip: IVR {ivr:.1f} < {self.cfg.ivr_min}")
            return False

        # Regime must be RANGING
        if regime != Regime.RANGING:
            logger.debug(f"Straddle skip: regime {regime}")
            return False

        # DTE check
        if dte > self.cfg.dte_entry + 1:
            logger.debug(f"Straddle skip: DTE {dte:.0f} too far")
            return False

        # Gap check
        if prev_close and abs(S - prev_close) / prev_close > self.cfg.gap_pct_stop:
            logger.debug(f"Straddle skip: gap {(S-prev_close)/prev_close:.2%}")
            return False

        return self.rm.allow_trade(f"ShortStraddle_{self.cfg.instrument}")

    def enter(self, S: float, T: float, r: float, sigma: float,
              entry_date: str = "", expiry_date: str = "") -> Optional[StraddlePosition]:
        """Create ATM short straddle."""
        step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)
        strike = int(round(S / step) * step)

        call_price = bs_price(S, strike, T, r, sigma, "call")
        put_price  = bs_price(S, strike, T, r, sigma, "put")
        credit = call_price + put_price

        if credit < 0.005 * S:
            logger.info(f"Straddle: credit {credit:.2f} too low")
            return None

        max_loss_budget = self.rm.cfg.capital * (self.cfg.max_loss_per_trade_pct / 100)
        max_loss_per_lot = credit * self.cfg.stop_loss_multiplier * self.cfg.lot_size
        lots = max(1, min(int(max_loss_budget / max_loss_per_lot), 3))

        pos = StraddlePosition(
            short_call_strike=strike,
            short_put_strike=strike,
            call_entry_price=call_price,
            put_entry_price=put_price,
            entry_credit=credit,
            entry_date=entry_date,
            expiry_date=expiry_date,
            lots=lots,
            instrument=self.cfg.instrument,
        )
        pos.net_delta_at_entry = pos.net_delta(S, T, r, sigma)
        self.rm.open_position(f"ShortStraddle_{self.cfg.instrument}")
        logger.info(
            f"STRADDLE ENTER {self.cfg.instrument}: ATM={strike} "
            f"credit={credit:.2f} lots={lots} delta={pos.net_delta_at_entry:.3f}"
        )
        return pos

    def manage(
        self, pos: StraddlePosition, S: float, T: float,
        r: float, sigma: float, dte: float,
        prev_close: float = None
    ) -> Tuple[str, float]:
        """
        Returns (action, pnl).
        action: HOLD | EXIT_PROFIT | EXIT_LOSS | EXIT_DELTA | EXIT_TIME | EXIT_GAP | HEDGE
        """
        lot = pos._lot_size()
        pnl = pos.current_pnl(S, T, r, sigma)
        credit_value = pos.entry_credit * pos.lots * lot
        net_delta = pos.net_delta(S, T, r, sigma)

        # 1. Gap risk at open
        if prev_close and abs(S - prev_close) / prev_close > self.cfg.gap_pct_stop:
            self.rm.close_position(f"ShortStraddle_{self.cfg.instrument}", pnl)
            logger.warning(f"STRADDLE EXIT GAP: gap={abs(S-prev_close)/prev_close:.2%} PnL={pnl:,.0f}")
            return "EXIT_GAP", pnl

        # 2. Profit target
        if pnl >= self.cfg.profit_target_pct * credit_value:
            self.rm.close_position(f"ShortStraddle_{self.cfg.instrument}", pnl)
            logger.info(f"STRADDLE EXIT PROFIT: PnL={pnl:,.0f}")
            return "EXIT_PROFIT", pnl

        # 3. Hard stop loss
        if pnl <= -(self.cfg.stop_loss_multiplier * credit_value):
            self.rm.close_position(f"ShortStraddle_{self.cfg.instrument}", pnl)
            logger.warning(f"STRADDLE EXIT LOSS: PnL={pnl:,.0f}")
            return "EXIT_LOSS", pnl

        # 4. Delta stop (directional breach — exit before it gets worse)
        if abs(net_delta) > self.cfg.delta_stop:
            self.rm.close_position(f"ShortStraddle_{self.cfg.instrument}", pnl)
            logger.warning(f"STRADDLE EXIT DELTA: net_delta={net_delta:.3f}")
            return "EXIT_DELTA", pnl

        # 5. Time stop
        if dte <= self.cfg.dte_exit_latest:
            self.rm.close_position(f"ShortStraddle_{self.cfg.instrument}", pnl)
            logger.info(f"STRADDLE EXIT TIME: {dte:.0f} DTE PnL={pnl:,.0f}")
            return "EXIT_TIME", pnl

        # 6. Delta hedge (doesn't exit, adds a protective hedge)
        if abs(net_delta) > self.cfg.delta_hedge and not pos.hedged:
            logger.info(f"STRADDLE HEDGE: net_delta={net_delta:.3f} — buying protective option")
            pos.hedged = True
            return "HEDGE", 0.0

        return "HOLD", 0.0
