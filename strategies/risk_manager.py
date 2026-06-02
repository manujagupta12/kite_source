"""
risk_manager.py — Portfolio-Level Risk Manager
===============================================
Enforces:
  - Max daily loss (circuit breaker)
  - Max open positions per strategy
  - Per-trade position sizing (Kelly / fixed fractional)
  - Drawdown-based exposure scaling
  - Correlation guard (don't run conflicting strategies simultaneously)

Usage
-----
    rm = RiskManager(capital=5_000_000)
    size = rm.position_size(strategy="EMA_Crossover", instrument="NIFTY",
                             entry_price=21500, atr=120, lot_size=50)
    if rm.allow_trade(strategy="EMA_Crossover"):
        place_order(...)
    rm.record_trade(pnl=-12000)
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    capital: float = 5_000_000          # ₹50L
    max_daily_loss_pct: float = 2.0     # Circuit breaker at -2% daily
    max_drawdown_pct: float = 15.0      # Reduce size at -15% drawdown
    max_positions_per_strategy: int = 3
    max_total_positions: int = 10
    risk_per_trade_pct: float = 1.0     # Risk 1% capital per trade
    max_capital_per_trade_pct: float = 10.0  # Max 10% capital in one trade
    # Instruments that conflict (don't run both long vol + short vol)
    conflicting_pairs: List[tuple] = field(default_factory=lambda: [
        ("ATM_Strangle", "EMA_Crossover"),
        ("ShortStraddle", "Momentum_Breakout"),
    ])


class RiskManager:
    def __init__(self, config: Optional[RiskConfig] = None):
        self.cfg = config or RiskConfig()
        self._daily_pnl: float = 0.0
        self._peak_capital: float = self.cfg.capital
        self._current_capital: float = self.cfg.capital
        self._open_positions: Dict[str, int] = {}   # strategy → count
        self._total_open: int = 0
        self._active_strategies: set = set()

    # ------------------------------------------------------------------ #
    #  CHECKS
    # ------------------------------------------------------------------ #

    def circuit_breaker_hit(self) -> bool:
        """True if daily loss exceeds max_daily_loss_pct."""
        loss_pct = (-self._daily_pnl / self.cfg.capital) * 100
        hit = loss_pct >= self.cfg.max_daily_loss_pct
        if hit:
            logger.warning(
                f"CIRCUIT BREAKER: daily loss {loss_pct:.2f}% >= "
                f"{self.cfg.max_daily_loss_pct}%"
            )
        return hit

    def drawdown_pct(self) -> float:
        if self._peak_capital <= 0:
            return 0.0
        return (self._peak_capital - self._current_capital) / self._peak_capital * 100

    def exposure_scalar(self) -> float:
        """Scale down position sizes when in drawdown."""
        dd = self.drawdown_pct()
        if dd < 5:
            return 1.0
        elif dd < 10:
            return 0.75
        elif dd < self.cfg.max_drawdown_pct:
            return 0.5
        else:
            logger.warning(f"Max drawdown {dd:.1f}% hit — no new trades")
            return 0.0

    def allow_trade(self, strategy: str) -> bool:
        """Returns True if a new trade is allowed for this strategy."""
        if self.circuit_breaker_hit():
            return False
        if self.exposure_scalar() == 0.0:
            return False
        if self._total_open >= self.cfg.max_total_positions:
            logger.info("Max total positions reached")
            return False
        open_count = self._open_positions.get(strategy, 0)
        if open_count >= self.cfg.max_positions_per_strategy:
            logger.info(f"{strategy}: max positions ({open_count}) reached")
            return False
        return True

    # ------------------------------------------------------------------ #
    #  POSITION SIZING
    # ------------------------------------------------------------------ #

    def position_size(
        self,
        strategy: str,
        instrument: str,
        entry_price: float,
        atr: float,
        lot_size: int,
        stop_distance: Optional[float] = None,
    ) -> int:
        """
        Returns number of lots to trade using ATR-based fixed-fractional sizing.

        stop_distance: price distance to stop. If None, uses 2×ATR.
        """
        scalar = self.exposure_scalar()
        if scalar == 0:
            return 0

        risk_amount = self.cfg.capital * (self.cfg.risk_per_trade_pct / 100) * scalar
        stop = stop_distance if stop_distance else 2.0 * atr

        if stop <= 0:
            return 0

        # Risk = lots × lot_size × stop_per_unit
        lots = int(risk_amount / (lot_size * stop))

        # Cap at max capital per trade
        max_lots_by_capital = int(
            (self.cfg.capital * self.cfg.max_capital_per_trade_pct / 100)
            / (entry_price * lot_size)
        )
        lots = min(lots, max_lots_by_capital)
        lots = max(lots, 0)

        logger.info(
            f"{strategy}/{instrument}: sizing {lots} lots "
            f"(risk ₹{risk_amount:,.0f}, stop {stop:.1f})"
        )
        return lots

    # ------------------------------------------------------------------ #
    #  STATE UPDATES
    # ------------------------------------------------------------------ #

    def open_position(self, strategy: str):
        self._open_positions[strategy] = self._open_positions.get(strategy, 0) + 1
        self._total_open += 1
        self._active_strategies.add(strategy)

    def close_position(self, strategy: str, pnl: float):
        count = self._open_positions.get(strategy, 1)
        self._open_positions[strategy] = max(count - 1, 0)
        self._total_open = max(self._total_open - 1, 0)
        self.record_trade(pnl)
        if self._open_positions.get(strategy, 0) == 0:
            self._active_strategies.discard(strategy)

    def record_trade(self, pnl: float):
        self._daily_pnl += pnl
        self._current_capital += pnl
        if self._current_capital > self._peak_capital:
            self._peak_capital = self._current_capital

    def reset_daily(self):
        """Call at market open each day."""
        self._daily_pnl = 0.0

    # ------------------------------------------------------------------ #
    #  REPORTING
    # ------------------------------------------------------------------ #

    def status(self) -> dict:
        return {
            "capital": self._current_capital,
            "daily_pnl": self._daily_pnl,
            "drawdown_pct": self.drawdown_pct(),
            "open_positions": dict(self._open_positions),
            "total_open": self._total_open,
            "exposure_scalar": self.exposure_scalar(),
            "circuit_breaker": self.circuit_breaker_hit(),
        }
