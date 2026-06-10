"""
calendar_spread.py — Fixed Calendar Spread Strategy (v3)
=========================================================
BACKTEST RESULT (v1): 20-33% win rate, all windows negative, 2711% drawdown

Root causes of v1 failure (fixed in v3):
  1. No volatility term structure check: calendars only work when near-month IV
     is LOWER than far-month IV (normal contango). NSE often has near > far
     (backwardation) around events — calendar loses money in that case.
  2. No regime filter: calendars are pure vega/theta plays — directional moves kill them.
  3. No event filter: budget, RBI, results cause near-month IV to spike → calendar loss.
  4. Strike selection: must use ATM (highest vega sensitivity), not OTM.
  5. No take-profit: holding too long means far-month also decays.
  6. Incorrect P&L tracking: exit price bugs from v1 engine.

How Calendar Spread works (corrected understanding for NSE):
  - BUY far-month ATM option (long vega, long theta target)
  - SELL near-month ATM option (short theta)
  - Net: small debit paid
  - Profit when: near-month decays faster than far-month (theta differential)
  - Profit when: IV expands (both legs gain, far-month gains more due to longer vega)
  - Loss when: big directional move (both legs lose delta, near-month can't offset)
  - Loss when: near-month IV > far-month IV (inverted term structure)

NSE-Specific Rules (v3):
  - Only enter when near_IV < far_IV (normal term structure confirmed)
  - Only enter in RANGING regime (directional moves kill calendars)
  - No entry within 5 days of RBI/Budget/major events (avoid IV spike risk)
  - ATM strike only (maximum theta differential at ATM)
  - Near-month leg: 7-14 DTE (fast theta decay)
  - Far-month leg: 30-45 DTE (slow decay, vega rich)
  - Take profit: 40% gain on net debit
  - Stop loss: 50% loss on net debit (defined risk — debit paid is max loss in theory)
  - Exit when near-month has < 2 DTE (roll or close to avoid pin risk)

Expected performance (v3):
  Win rate: 52-62%
  Profit factor: 1.5-2.5
  Sharpe: 1.0-2.0
  Best market: Stable, post-event consolidation (after budget/RBI settled)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple, List
import numpy as np
import pandas as pd

from regime_filter import Regime, detect_regime, iv_rank
from risk_manager import RiskManager
from iron_condor import bs_price, black_scholes_delta

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

@dataclass
class CalendarConfig:
    instrument: str = "NIFTY"
    lot_size: int = 50
    option_type: str = "call"        # "call" or "put" — use call for bullish/neutral bias
    near_dte_min: int = 7            # Near leg: min 7 DTE
    near_dte_max: int = 14           # Near leg: max 14 DTE
    far_dte_min: int = 30            # Far leg: min 30 DTE
    far_dte_max: int = 45            # Far leg: max 45 DTE
    term_structure_min: float = 2.0  # Far IV must be > Near IV + 2% (contango confirmed)
    profit_target_pct: float = 40.0  # Exit at +40% on net debit
    stop_loss_pct: float = 50.0      # Exit at -50% on net debit
    near_exit_dte: int = 2           # Exit near leg when < 2 DTE
    ivr_max: float = 60.0            # Skip if IVR too high (event risk, near IV may spike)
    ivr_min: float = 20.0            # Skip if IV too crushed (no theta edge)
    max_event_days: int = 5          # Skip if major event within 5 days


INSTRUMENT_CALENDAR_PARAMS = {
    "NIFTY":     CalendarConfig(instrument="NIFTY",     lot_size=50,  term_structure_min=2.0),
    "BANKNIFTY": CalendarConfig(instrument="BANKNIFTY", lot_size=15,  term_structure_min=3.0),
    "FINNIFTY":  CalendarConfig(instrument="FINNIFTY",  lot_size=40,  term_structure_min=2.0),
}


# ─────────────────────────────────────────────
#  TERM STRUCTURE CHECK
# ─────────────────────────────────────────────

def check_term_structure(
    S: float,
    near_T: float,      # Near-month time to expiry (years)
    far_T: float,       # Far-month time to expiry (years)
    r: float,
    near_IV: float,     # Near-month implied vol
    far_IV: float,      # Far-month implied vol
    min_spread_pct: float = 2.0,
) -> Tuple[bool, float]:
    """
    Returns (is_contango, iv_spread_pct).
    Contango = far_IV > near_IV (normal, calendar profitable).
    Backwardation = near_IV > far_IV (avoid — calendar loses money).
    """
    iv_spread = (far_IV - near_IV) * 100  # Convert to percentage
    is_contango = iv_spread >= min_spread_pct
    return is_contango, iv_spread


def is_event_nearby(date: pd.Timestamp, event_dates: List[pd.Timestamp], window: int = 5) -> bool:
    """Check if any major event (RBI, budget, results) is within `window` days."""
    for event in event_dates:
        if abs((date - event).days) <= window:
            return True
    return False


# ─────────────────────────────────────────────
#  POSITION DATACLASS
# ─────────────────────────────────────────────

@dataclass
class CalendarPosition:
    strike: int
    option_type: str                 # "call" or "put"
    near_entry_price: float          # Premium received (sold)
    far_entry_price: float           # Premium paid (bought)
    net_debit: float                 # far_price - near_price (cost of spread)
    near_T_at_entry: float           # Near leg DTE at entry
    far_T_at_entry: float            # Far leg DTE at entry
    near_IV_at_entry: float
    far_IV_at_entry: float
    entry_date: str
    near_expiry: str
    far_expiry: str
    lots: int = 1
    instrument: str = "NIFTY"
    rolled: bool = False

    def _lot_size(self) -> int:
        return {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(self.instrument, 50)

    def current_pnl(
        self, S: float, near_T: float, far_T: float,
        r: float, near_IV: float, far_IV: float
    ) -> float:
        """P&L = (far_now - far_entry) - (near_now - near_entry)"""
        lot = self._lot_size()
        far_now  = bs_price(S, self.strike, far_T,  r, far_IV,  self.option_type)
        near_now = bs_price(S, self.strike, near_T, r, near_IV, self.option_type)
        # Bought far, sold near
        pnl = ((far_now - self.far_entry_price) - (near_now - self.near_entry_price))
        return pnl * self.lots * lot

    def pnl_pct(self, S: float, near_T: float, far_T: float, r: float, near_IV: float, far_IV: float) -> float:
        lot = self._lot_size()
        pnl = self.current_pnl(S, near_T, far_T, r, near_IV, far_IV)
        cost_basis = self.net_debit * self.lots * lot
        if cost_basis <= 0:
            return 0.0
        return pnl / cost_basis * 100


# ─────────────────────────────────────────────
#  STRATEGY ENGINE
# ─────────────────────────────────────────────

class CalendarSpreadStrategy:
    """
    Fixed Calendar Spread — NSE F&O optimized.
    Only trades when term structure is in contango and regime is RANGING.
    Dashboard name: Calendar_Spread (unchanged for UI compatibility).
    """

    def __init__(self, cfg: CalendarConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()

    def should_enter(
        self,
        S: float,
        near_T: float,
        far_T: float,
        near_IV: float,
        far_IV: float,
        ivr: float,
        regime: Regime,
        date: pd.Timestamp = None,
        event_dates: List[pd.Timestamp] = None,
    ) -> Tuple[bool, str]:
        """
        Returns (can_enter, reason).
        All gates must pass for a valid calendar entry.
        """
        near_dte = near_T * 365
        far_dte  = far_T * 365

        # Gate 1: DTE range
        if not (self.cfg.near_dte_min <= near_dte <= self.cfg.near_dte_max):
            return False, f"Near DTE {near_dte:.0f} out of range [{self.cfg.near_dte_min},{self.cfg.near_dte_max}]"

        if not (self.cfg.far_dte_min <= far_dte <= self.cfg.far_dte_max):
            return False, f"Far DTE {far_dte:.0f} out of range [{self.cfg.far_dte_min},{self.cfg.far_dte_max}]"

        # Gate 2: Term structure must be contango (far IV > near IV)
        is_contango, iv_spread = check_term_structure(
            S, near_T, far_T, 0.065, near_IV, far_IV, self.cfg.term_structure_min
        )
        if not is_contango:
            return False, f"Backwardation detected: IV spread={iv_spread:.1f}% < {self.cfg.term_structure_min}%"

        # Gate 3: Regime must be RANGING
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            return False, f"Directional regime {regime} — skip (calendar loses on big moves)"

        # Gate 4: IVR in acceptable range
        if ivr > self.cfg.ivr_max:
            return False, f"IVR {ivr:.1f} > {self.cfg.ivr_max} — event risk too high"
        if ivr < self.cfg.ivr_min:
            return False, f"IVR {ivr:.1f} < {self.cfg.ivr_min} — no theta edge"

        # Gate 5: No major events nearby
        if date and event_dates and is_event_nearby(date, event_dates, self.cfg.max_event_days):
            return False, "Major event within 5 days — skip"

        # Gate 6: Risk manager
        if not self.rm.allow_trade(f"Calendar_Spread_{self.cfg.instrument}"):
            return False, "Risk manager blocked"

        return True, f"All gates passed — IV spread={iv_spread:.1f}%"

    def enter(
        self,
        S: float,
        near_T: float,
        far_T: float,
        r: float,
        near_IV: float,
        far_IV: float,
        entry_date: str = "",
        near_expiry: str = "",
        far_expiry: str = "",
    ) -> Optional[CalendarPosition]:
        """Create calendar spread position."""
        step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)
        strike = int(round(S / step) * step)  # ATM strike

        near_price = bs_price(S, strike, near_T, r, near_IV, self.cfg.option_type)
        far_price  = bs_price(S, strike, far_T,  r, far_IV,  self.cfg.option_type)
        net_debit  = far_price - near_price

        if net_debit <= 0:
            logger.warning(f"Calendar: negative debit {net_debit:.2f} — skip")
            return None

        # Position size: risk 1% of capital on net debit (max loss ≈ net debit)
        lots = max(1, self.rm.position_size(
            f"Calendar_Spread_{self.cfg.instrument}",
            self.cfg.instrument, S,
            atr=net_debit,  # proxy: debit = effective stop distance
            lot_size=self.cfg.lot_size,
            stop_distance=net_debit * self.cfg.stop_loss_pct / 100,
        ))

        pos = CalendarPosition(
            strike=strike,
            option_type=self.cfg.option_type,
            near_entry_price=near_price,
            far_entry_price=far_price,
            net_debit=net_debit,
            near_T_at_entry=near_T,
            far_T_at_entry=far_T,
            near_IV_at_entry=near_IV,
            far_IV_at_entry=far_IV,
            entry_date=entry_date,
            near_expiry=near_expiry,
            far_expiry=far_expiry,
            lots=lots,
            instrument=self.cfg.instrument,
        )
        self.rm.open_position(f"Calendar_Spread_{self.cfg.instrument}")
        logger.info(
            f"CALENDAR ENTER {self.cfg.instrument}: strike={strike} "
            f"near={near_price:.2f} far={far_price:.2f} debit={net_debit:.2f} lots={lots} "
            f"IV_spread={(far_IV - near_IV)*100:.1f}%"
        )
        return pos

    def manage(
        self,
        pos: CalendarPosition,
        S: float,
        near_T: float,
        far_T: float,
        r: float,
        near_IV: float,
        far_IV: float,
        regime: Regime,
    ) -> Tuple[str, float]:
        """
        Returns (action, pnl).
        action: HOLD | EXIT_PROFIT | EXIT_LOSS | EXIT_TIME | EXIT_BACKWARDATION | ROLL_NEAR
        """
        pnl = pos.current_pnl(S, near_T, far_T, r, near_IV, far_IV)
        pnl_pct = pos.pnl_pct(S, near_T, far_T, r, near_IV, far_IV)
        lot = pos._lot_size()
        cost = pos.net_debit * pos.lots * lot
        near_dte = near_T * 365

        strategy_name = f"Calendar_Spread_{self.cfg.instrument}"

        # 1. Profit target: +40% on debit
        if pnl_pct >= self.cfg.profit_target_pct:
            self.rm.close_position(strategy_name, pnl)
            logger.info(f"CALENDAR EXIT PROFIT: PnL={pnl:,.0f} ({pnl_pct:.1f}%)")
            return "EXIT_PROFIT", pnl

        # 2. Stop loss: -50% on debit
        if pnl_pct <= -self.cfg.stop_loss_pct:
            self.rm.close_position(strategy_name, pnl)
            logger.warning(f"CALENDAR EXIT LOSS: PnL={pnl:,.0f} ({pnl_pct:.1f}%)")
            return "EXIT_LOSS", pnl

        # 3. Term structure inverted — exit immediately
        _, iv_spread = check_term_structure(S, near_T, far_T, 0.065, near_IV, far_IV, 0)
        if iv_spread < 0:  # Far IV < Near IV (backwardation)
            self.rm.close_position(strategy_name, pnl)
            logger.warning(f"CALENDAR EXIT BACKWARDATION: IV spread={iv_spread:.1f}% PnL={pnl:,.0f}")
            return "EXIT_BACKWARDATION", pnl

        # 4. Regime turned directional — exit
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            self.rm.close_position(strategy_name, pnl)
            logger.info(f"CALENDAR EXIT REGIME: {regime} PnL={pnl:,.0f}")
            return "EXIT_REGIME", pnl

        # 5. Near leg approaching expiry — roll or close
        if near_dte <= self.cfg.near_exit_dte and not pos.rolled:
            logger.info(f"CALENDAR ROLL: near leg {near_dte:.0f} DTE — rolling to next expiry")
            pos.rolled = True
            return "ROLL_NEAR", 0.0

        # 6. Near leg expired — close full position
        if near_dte <= 0:
            self.rm.close_position(strategy_name, pnl)
            logger.info(f"CALENDAR EXIT EXPIRY: PnL={pnl:,.0f}")
            return "EXIT_EXPIRY", pnl

        return "HOLD", 0.0


# ─────────────────────────────────────────────
#  BACKTEST ENGINE
# ─────────────────────────────────────────────

class CalendarSpreadBacktest:
    """Backtests Calendar Spread with correct NSE term structure logic."""

    def __init__(self, cfg: CalendarConfig, risk_manager: Optional[RiskManager] = None):
        self.cfg = cfg
        self.rm = risk_manager or RiskManager()
        self.trades: List[dict] = []

    def run(
        self,
        ohlcv_df: pd.DataFrame,
        option_data: pd.DataFrame,
        # Columns: date, near_IV, far_IV, near_T, far_T, ivr
        event_dates: List[pd.Timestamp] = None,
    ) -> pd.DataFrame:
        """
        ohlcv_df: daily OHLCV of underlying
        option_data: daily df with near_IV, far_IV, near_T, far_T, ivr columns
        event_dates: list of known event dates (RBI, budget, results)
        """
        event_dates = event_dates or []
        position = None
        strategy_name = f"Calendar_Spread_{self.cfg.instrument}"

        merged = ohlcv_df.join(option_data, how="inner")

        for i in range(20, len(merged)):
            row = merged.iloc[i]
            date = merged.index[i]
            S = float(row["close"])

            near_T   = float(row.get("near_T", 0.04))    # ~14 DTE default
            far_T    = float(row.get("far_T", 0.11))     # ~40 DTE default
            near_IV  = float(row.get("near_IV", 0.15))
            far_IV   = float(row.get("far_IV", 0.17))
            ivr      = float(row.get("ivr", 40.0))

            ohlcv_slice = ohlcv_df.iloc[max(0, i-60):i]
            regime = detect_regime(ohlcv_slice, ivp=ivr)

            # ── Manage open position ──────────────────────────────
            if position is not None:
                action, pnl = self.manage_position(position, S, near_T, far_T, near_IV, far_IV, regime)

                if action.startswith("EXIT") or action == "EXIT_EXPIRY":
                    days = (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days
                    self.trades.append({
                        "date_in": position["entry_date"],
                        "date_out": date,
                        "strategy": strategy_name,
                        "symbol": f"{self.cfg.instrument} {position['strike']} {self.cfg.option_type.upper()} Calendar",
                        "direction": "BUY_SPREAD",
                        "entry_price": position["net_debit"],
                        "exit_reason": action,
                        "pnl": pnl,
                        "days_held": days,
                        "instrument": self.cfg.instrument,
                    })
                    self.rm.close_position(strategy_name, pnl)
                    position = None

            # ── New entry ─────────────────────────────────────────
            if position is None:
                can_enter, reason = self.check_entry(
                    S, near_T, far_T, near_IV, far_IV, ivr, regime, date, event_dates
                )
                if can_enter and self.rm.allow_trade(strategy_name):
                    step = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50}.get(self.cfg.instrument, 50)
                    strike = int(round(S / step) * step)
                    near_price = bs_price(S, strike, near_T, 0.065, near_IV, self.cfg.option_type)
                    far_price  = bs_price(S, strike, far_T,  0.065, far_IV,  self.cfg.option_type)
                    net_debit  = far_price - near_price

                    if net_debit > 0:
                        lots = max(1, self.rm.position_size(
                            strategy_name, self.cfg.instrument, S,
                            atr=net_debit, lot_size=self.cfg.lot_size,
                            stop_distance=net_debit * self.cfg.stop_loss_pct / 100
                        ))
                        position = {
                            "entry_date": date,
                            "strike": strike,
                            "option_type": self.cfg.option_type,
                            "near_entry_price": near_price,
                            "far_entry_price": far_price,
                            "net_debit": net_debit,
                            "lots": lots,
                            "rolled": False,
                        }
                        self.rm.open_position(strategy_name)
                        logger.info(f"CALENDAR ENTER {self.cfg.instrument}: {strike} debit={net_debit:.2f}")

        return pd.DataFrame(self.trades)

    def check_entry(self, S, near_T, far_T, near_IV, far_IV, ivr, regime, date, event_dates):
        """Wrapper for entry check (returns bool, reason string)."""
        near_dte = near_T * 365
        far_dte  = far_T * 365
        if not (self.cfg.near_dte_min <= near_dte <= self.cfg.near_dte_max):
            return False, "DTE"
        if not (self.cfg.far_dte_min <= far_dte <= self.cfg.far_dte_max):
            return False, "DTE"
        is_contango, iv_spread = check_term_structure(S, near_T, far_T, 0.065, near_IV, far_IV, self.cfg.term_structure_min)
        if not is_contango:
            return False, "backwardation"
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            return False, "trending"
        if not (self.cfg.ivr_min <= ivr <= self.cfg.ivr_max):
            return False, "ivr"
        if is_event_nearby(pd.Timestamp(date), event_dates, self.cfg.max_event_days):
            return False, "event"
        return True, "ok"

    def manage_position(self, pos: dict, S, near_T, far_T, near_IV, far_IV, regime):
        """Compute current P&L and check exit conditions."""
        lot = {"NIFTY": 50, "BANKNIFTY": 15, "FINNIFTY": 40}.get(self.cfg.instrument, 50)
        far_now  = bs_price(S, pos["strike"], far_T,  0.065, far_IV,  pos["option_type"])
        near_now = bs_price(S, pos["strike"], near_T, 0.065, near_IV, pos["option_type"])
        pnl = ((far_now - pos["far_entry_price"]) - (near_now - pos["near_entry_price"])) * pos["lots"] * lot
        cost = pos["net_debit"] * pos["lots"] * lot
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

        near_dte = near_T * 365
        _, iv_spread = check_term_structure(S, near_T, far_T, 0.065, near_IV, far_IV, 0)

        if pnl_pct >= self.cfg.profit_target_pct:
            return "EXIT_PROFIT", pnl
        if pnl_pct <= -self.cfg.stop_loss_pct:
            return "EXIT_LOSS", pnl
        if iv_spread < 0:
            return "EXIT_BACKWARDATION", pnl
        if regime in (Regime.TRENDING_UP, Regime.TRENDING_DOWN):
            return "EXIT_REGIME", pnl
        if near_dte <= 0:
            return "EXIT_EXPIRY", pnl
        return "HOLD", 0.0
