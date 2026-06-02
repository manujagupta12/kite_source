"""
backtest/metrics.py
===================
Computes all required performance metrics from trade results.
No approximations — uses actual entry/exit prices from Bhavcopy data.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List


@dataclass
class TradeResult:
    date_in:      pd.Timestamp
    date_out:     pd.Timestamp
    strategy:     str
    symbol:       str
    direction:    str
    entry_price:  float
    exit_price:   float
    exit_reason:  str   # "TARGET", "STOP", "EXPIRY", "SIGNAL_FLIP"
    pnl:          float
    pnl_pct:      float
    score:        float


@dataclass
class PerformanceReport:
    strategy:        str
    period_start:    str
    period_end:      str
    total_trades:    int
    wins:            int
    losses:          int
    win_rate:        float    # %
    avg_win:         float
    avg_loss:        float
    profit_factor:   float
    max_drawdown:    float    # %
    sharpe_ratio:    float
    calmar_ratio:    float
    total_pnl:       float
    avg_pnl_trade:   float
    best_trade:      float
    worst_trade:     float
    avg_hold_days:   float
    signal_credibility: str  # STRONG / MODERATE / WEAK / UNRELIABLE

    def as_dict(self) -> dict:
        return {
            "Strategy":           self.strategy,
            "Backtest Period":    f"{self.period_start} → {self.period_end}",
            "Total Trades":       self.total_trades,
            "Win Rate":           f"{self.win_rate:.1f}%",
            "Profit Factor":      f"{self.profit_factor:.2f}",
            "Max Drawdown":       f"{self.max_drawdown:.2f}%",
            "Sharpe Ratio":       f"{self.sharpe_ratio:.2f}",
            "Calmar Ratio":       f"{self.calmar_ratio:.2f}",
            "Total P&L":          f"₹{self.total_pnl:,.0f}",
            "Avg P&L / Trade":    f"₹{self.avg_pnl_trade:,.0f}",
            "Best Trade":         f"₹{self.best_trade:,.0f}",
            "Worst Trade":        f"₹{self.worst_trade:,.0f}",
            "Avg Hold (days)":    f"{self.avg_hold_days:.1f}",
            "Signal Credibility": self.signal_credibility,
        }


def compute_metrics(trades: List[TradeResult], strategy_name: str, lot_value: float = 50.0) -> PerformanceReport:
    """
    Compute full performance metrics from a list of TradeResult objects.

    Args:
        trades:        List of completed trades
        strategy_name: Name of the strategy
        lot_value:     Lot size multiplier (NIFTY = 50 units per lot)
    """
    if not trades:
        return _empty_report(strategy_name)

    df = pd.DataFrame([vars(t) for t in trades])
    df.sort_values("date_in", inplace=True)

    total  = len(df)
    pnls   = df["pnl"].values * lot_value

    wins   = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]

    win_rate     = 100.0 * len(wins) / total if total > 0 else 0
    avg_win      = wins["pnl"].mean() * lot_value    if not wins.empty   else 0
    avg_loss     = losses["pnl"].mean() * lot_value  if not losses.empty else 0

    gross_profit = wins["pnl"].sum()   * lot_value
    gross_loss   = abs(losses["pnl"].sum()) * lot_value
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)

    # Drawdown
    cumulative = np.cumsum(pnls)
    running_max = np.maximum.accumulate(cumulative)
    drawdowns   = (running_max - cumulative) / (running_max + 1e-9) * 100
    max_drawdown = float(drawdowns.max())

    # Sharpe ratio (annualised, risk-free ≈ 6.5% for India)
    if len(pnls) >= 2:
        daily_returns = pnls / (abs(df["entry_price"].mean()) * lot_value + 1e-9)
        sharpe = (daily_returns.mean() - 0.065 / 252) / (daily_returns.std() + 1e-9) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Calmar = annualised return / max drawdown
    if len(df) >= 2:
        days_total = (df["date_out"].max() - df["date_in"].min()).days or 1
        ann_return = (pnls.sum() / days_total) * 252
        calmar = ann_return / (max_drawdown / 100 + 1e-9)
    else:
        calmar = 0.0

    # Avg hold time
    df["hold_days"] = (df["date_out"] - df["date_in"]).dt.days
    avg_hold = df["hold_days"].mean()

    total_pnl    = pnls.sum()
    avg_pnl      = pnls.mean()
    best_trade   = pnls.max()
    worst_trade  = pnls.min()

    period_start = str(df["date_in"].min().date())
    period_end   = str(df["date_out"].max().date())

    credibility  = _signal_credibility(win_rate, profit_factor, sharpe, total)

    return PerformanceReport(
        strategy        = strategy_name,
        period_start    = period_start,
        period_end      = period_end,
        total_trades    = total,
        wins            = len(wins),
        losses          = len(losses),
        win_rate        = round(win_rate, 2),
        avg_win         = round(avg_win, 2),
        avg_loss        = round(avg_loss, 2),
        profit_factor   = round(profit_factor, 2),
        max_drawdown    = round(max_drawdown, 2),
        sharpe_ratio    = round(sharpe, 2),
        calmar_ratio    = round(calmar, 2),
        total_pnl       = round(total_pnl, 2),
        avg_pnl_trade   = round(avg_pnl, 2),
        best_trade      = round(best_trade, 2),
        worst_trade     = round(worst_trade, 2),
        avg_hold_days   = round(avg_hold, 1),
        signal_credibility = credibility,
    )


def _signal_credibility(win_rate: float, pf: float, sharpe: float, n: int) -> str:
    """
    Classify signal credibility:
      STRONG      — win_rate>55%, PF>1.5, Sharpe>1.0, n>30
      MODERATE    — win_rate>50%, PF>1.2, Sharpe>0.5, n>20
      WEAK        — win_rate>45%, PF>1.0, n>10
      UNRELIABLE  — anything else (overfitting risk / insufficient trades)
    """
    if n < 10:
        return "UNRELIABLE (insufficient trades)"
    if win_rate > 55 and pf > 1.5 and sharpe > 1.0 and n >= 30:
        return "STRONG"
    if win_rate > 50 and pf > 1.2 and sharpe > 0.5 and n >= 20:
        return "MODERATE"
    if win_rate > 45 and pf > 1.0 and n >= 10:
        return "WEAK"
    return "UNRELIABLE (random noise)"


def _empty_report(strategy_name: str) -> PerformanceReport:
    return PerformanceReport(
        strategy=strategy_name, period_start="N/A", period_end="N/A",
        total_trades=0, wins=0, losses=0, win_rate=0, avg_win=0, avg_loss=0,
        profit_factor=0, max_drawdown=0, sharpe_ratio=0, calmar_ratio=0,
        total_pnl=0, avg_pnl_trade=0, best_trade=0, worst_trade=0,
        avg_hold_days=0, signal_credibility="UNRELIABLE (no trades)",
    )


def walk_forward_metrics(
    all_trades: List[TradeResult],
    strategy_name: str,
    n_windows: int = 4,
    lot_value: float = 50.0,
) -> list[PerformanceReport]:
    """
    Split trades into n_windows chronological windows.
    Returns per-window metrics for walk-forward validation.
    """
    if not all_trades:
        return []

    df = pd.DataFrame([vars(t) for t in all_trades])
    df.sort_values("date_in", inplace=True)

    window_size = len(df) // n_windows
    reports = []

    for i in range(n_windows):
        start_idx = i * window_size
        end_idx   = (i + 1) * window_size if i < n_windows - 1 else len(df)
        window_df = df.iloc[start_idx:end_idx]

        window_trades = [
            TradeResult(**row)
            for _, row in window_df.iterrows()
        ]
        report = compute_metrics(window_trades, f"{strategy_name}_W{i+1}", lot_value)
        reports.append(report)

    return reports
