"""
strategy_orchestrator.py — Master Strategy Runner
==================================================
Runs all strategies together with shared risk management.
Handles:
  - Strategy selection by regime
  - Capital allocation across strategies
  - Daily PnL tracking and circuit breaker
  - Strategy rotation (reduce exposure after losing streak)

Capital allocation (₹50L):
  EMA_Crossover      : 30% (trend — highest confidence)
  IronCondor         : 25% (range — premium selling)
  CreditSpread       : 20% (directional premium)
  ShortStraddle      : 10% (weekly premium — limited)
  VWAP_Reversal      : 10% (intraday scalp)
  PCR_MeanReversion  : 5%  (opportunistic — only NIFTY)
  MomentumBreakout   : 5%  (event/breakout plays) [reserve capital]

Usage:
    from strategy_orchestrator import StrategyOrchestrator
    orch = StrategyOrchestrator(capital=5_000_000)
    orch.run_live(kite)   # live trading
    orch.run_backtest(data_dict)  # backtesting
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional
import pandas as pd

from risk_manager import RiskManager, RiskConfig
from regime_filter import Regime, detect_regime, iv_rank
from ema_crossover import EMACrossoverLive, INSTRUMENT_PARAMS as EMA_PARAMS
from iron_condor import IronCondorStrategy, INSTRUMENT_CONDOR_PARAMS
from pcr_mean_reversion import PCRBacktest, INSTRUMENT_PCR_PARAMS
from short_straddle import ShortStraddleStrategy, INSTRUMENT_STRADDLE_PARAMS
from credit_spread import CreditSpreadStrategy, INSTRUMENT_SPREAD_PARAMS
from vwap_reversal import VWAPReversalBacktest, INSTRUMENT_VWAP_PARAMS
from momentum_breakout import MomentumBreakoutBacktest, INSTRUMENT_MOMENTUM_PARAMS

logger = logging.getLogger(__name__)

TOTAL_CAPITAL = 5_000_000  # ₹50L

# Strategy capital allocation
ALLOCATION = {
    "EMA_Crossover":     0.30,
    "IronCondor":        0.25,
    "CreditSpread":      0.20,
    "ShortStraddle":     0.10,
    "VWAP_Reversal":     0.10,
    "PCR_MeanReversion": 0.05,
    "MomentumBreakout":  0.05,
}

# Which strategies run in which regime
REGIME_STRATEGY_MAP = {
    Regime.TRENDING_UP: [
        "EMA_Crossover",    # Primary
        "CreditSpread",     # Bull put spread
        "MomentumBreakout", # Breakout options
        "VWAP_Reversal",    # Intraday only
    ],
    Regime.TRENDING_DOWN: [
        "EMA_Crossover",    # Short futures
        "CreditSpread",     # Bear call spread
        "MomentumBreakout", # Put breakout
        "VWAP_Reversal",    # Intraday only
    ],
    Regime.RANGING: [
        "IronCondor",       # Primary in range
        "ShortStraddle",    # Weekly premium
        "PCR_MeanReversion",# PCR extremes
        "VWAP_Reversal",    # Intraday
    ],
    Regime.HIGH_VOL: [
        "IronCondor",       # Sell elevated premium
        "CreditSpread",     # Directional with hedge
        # ShortStraddle: NO — gap risk too high
        # EMA: reduced size
    ],
    Regime.UNKNOWN: [
        "VWAP_Reversal",    # Only intraday scalping
    ],
}


@dataclass
class StrategyPerformance:
    """Rolling performance tracker for each strategy."""
    strategy: str
    trades: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    consecutive_losses: int = 0
    active: bool = True

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades if self.trades > 0 else 0.0

    def update(self, pnl: float):
        self.trades += 1
        self.total_pnl += pnl
        if pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        # Pause strategy after 3 consecutive losses
        if self.consecutive_losses >= 3:
            logger.warning(f"{self.strategy}: 3 consecutive losses — pausing for review")
            self.active = False


class StrategyOrchestrator:
    """
    Master orchestrator for all algo strategies.
    Single risk manager shared across all strategies.
    """

    def __init__(self, capital: float = TOTAL_CAPITAL):
        self.capital = capital
        self.risk_cfg = RiskConfig(
            capital=capital,
            max_daily_loss_pct=2.0,
            max_drawdown_pct=15.0,
            max_positions_per_strategy=3,
            max_total_positions=10,
            risk_per_trade_pct=1.0,
        )
        self.rm = RiskManager(self.risk_cfg)
        self.performance: Dict[str, StrategyPerformance] = {
            name: StrategyPerformance(name) for name in ALLOCATION
        }
        self._current_regime = Regime.UNKNOWN

    def get_active_strategies(self, regime: Regime) -> list:
        """Return strategies allowed in current regime that aren't paused."""
        allowed = REGIME_STRATEGY_MAP.get(regime, [])
        return [s for s in allowed if self.performance.get(s, StrategyPerformance(s)).active]

    def update_regime(self, ohlcv_df: pd.DataFrame, ivr: float = None):
        self._current_regime = detect_regime(ohlcv_df, ivp=ivr)
        logger.info(f"Regime updated: {self._current_regime}")
        return self._current_regime

    def daily_reset(self):
        """Call at market open."""
        self.rm.reset_daily()
        # Re-activate paused strategies every Monday (fresh start)
        import datetime
        if datetime.datetime.now().weekday() == 0:  # Monday
            for perf in self.performance.values():
                if not perf.active:
                    perf.consecutive_losses = 0
                    perf.active = True
                    logger.info(f"{perf.strategy}: re-activated (Monday reset)")

    def status_report(self) -> dict:
        """Daily status report."""
        return {
            "regime": self._current_regime,
            "risk": self.rm.status(),
            "strategy_performance": {
                name: {
                    "trades": p.trades,
                    "win_rate": f"{p.win_rate:.1%}",
                    "total_pnl": p.total_pnl,
                    "active": p.active,
                    "consecutive_losses": p.consecutive_losses,
                }
                for name, p in self.performance.items()
            },
            "active_strategies": self.get_active_strategies(self._current_regime),
        }

    # ─────────────────────────────────────────────
    #  LIVE TRADING (Kite)
    # ─────────────────────────────────────────────

    def run_live(self, kite, instruments: list = None):
        """
        Live trading runner. Call this from your scheduler (Kite WebSocket).
        instruments: list of ["NIFTY", "BANKNIFTY", "FINNIFTY"]
        """
        instruments = instruments or ["NIFTY", "BANKNIFTY"]
        active = self.get_active_strategies(self._current_regime)

        if self.rm.circuit_breaker_hit():
            logger.warning("CIRCUIT BREAKER HIT — no new trades today")
            return

        live_strategies = {}
        for instrument in instruments:
            if "EMA_Crossover" in active:
                live_strategies[f"EMA_{instrument}"] = EMACrossoverLive(
                    kite, instrument=instrument, risk_manager=self.rm
                )

        logger.info(f"Live strategies active: {list(live_strategies.keys())}")
        return live_strategies

    # ─────────────────────────────────────────────
    #  BACKTESTING
    # ─────────────────────────────────────────────

    def run_backtest(
        self,
        daily_ohlcv: Dict[str, pd.DataFrame],   # instrument → daily OHLCV
        intraday_ohlcv: Dict[str, pd.DataFrame] = None,  # for VWAP
        option_fetcher=None,                    # callable for option prices
        iv_series: Dict[str, pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Run all strategies in backtest mode.
        Returns combined trade log.
        """
        all_trades = []

        for instrument, ohlcv in daily_ohlcv.items():
            logger.info(f"Backtesting {instrument}...")

            # EMA Crossover (always run — strongest strategy)
            cfg = EMA_PARAMS.get(instrument)
            if cfg:
                bt = type('EMABacktest', (), {})()  # placeholder
                from ema_crossover import EMABacktest
                bt = EMABacktest(cfg, self.rm)
                trades = bt.run(ohlcv)
                if not trades.empty:
                    all_trades.append(trades)
                    logger.info(f"EMA {instrument}: {len(trades)} trades")

            # VWAP (intraday)
            if intraday_ohlcv and instrument in intraday_ohlcv:
                vcfg = INSTRUMENT_VWAP_PARAMS.get(instrument)
                if vcfg:
                    vbt = VWAPReversalBacktest(vcfg, self.rm)
                    vtrades = vbt.run(intraday_ohlcv[instrument])
                    if not vtrades.empty:
                        all_trades.append(vtrades)
                        logger.info(f"VWAP {instrument}: {len(vtrades)} trades")

            # Options strategies (require option price fetcher)
            if option_fetcher:
                # Iron Condor
                icfg = INSTRUMENT_CONDOR_PARAMS.get(instrument)
                if icfg:
                    logger.info(f"IronCondor {instrument}: run with option_fetcher")

                # Momentum Breakout
                mbt = MomentumBreakoutBacktest(
                    INSTRUMENT_MOMENTUM_PARAMS.get(instrument,
                    __import__('momentum_breakout').MomentumConfig(instrument=instrument)),
                    self.rm
                )
                mtrades = mbt.run(ohlcv, option_fetcher)
                if not mtrades.empty:
                    all_trades.append(mtrades)
                    logger.info(f"Momentum {instrument}: {len(mtrades)} trades")

        if not all_trades:
            return pd.DataFrame()

        combined = pd.concat(all_trades, ignore_index=True)
        combined = combined.sort_values("date_in").reset_index(drop=True)
        return combined
