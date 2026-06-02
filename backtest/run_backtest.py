"""
backtest/run_backtest.py
========================
Full-platform backtester: NIFTY + BANKNIFTY + FINNIFTY + Equity
5-year dataset, ₹50L margin, walk-forward validation.

Usage:
    cd C:/AlgoTrading/kite_source/backtest
    pip install -r requirements.txt
    python run_backtest.py --from 2020-01-01 --to 2024-12-31 --margin 5000000

Results saved to backtest/results/
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from data_fetcher import load_range, filter_options, LOT_SIZES
from strategies import FO_STRATEGIES, EQUITY_STRATEGIES, ALL_STRATEGIES
from engine import BacktestEngine, in_sample_out_sample_split
from metrics import compute_metrics, walk_forward_metrics, TradeResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("results/backtest.log"),
    ],
)
log = logging.getLogger(__name__)

# ── Per-instrument lot sizes ──────────────────────────────────────────────────
INSTRUMENT_LOT = {
    "NIFTY":     50,
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
    "EQUITY":     1,   # shares — sized by capital allocation below
}

# ── Equity position sizing: % of margin per trade ───────────────────────────
EQUITY_RISK_PCT = 0.02   # risk 2% of margin per equity trade


def equity_lot_value(entry_price: float, margin: float) -> int:
    """How many shares to buy with 2% of margin at this price."""
    capital = margin * EQUITY_RISK_PCT
    return max(1, int(capital / entry_price))


def main():
    parser = argparse.ArgumentParser(description="kite_source Full-Platform Backtester")
    parser.add_argument("--from",    dest="start",   default="2020-01-01")
    parser.add_argument("--to",      dest="end",     default="2024-12-31")
    parser.add_argument("--margin",  dest="margin",  type=float, default=5_000_000,
                        help="Available margin in ₹ (default: ₹50L = 5000000)")
    parser.add_argument("--data",    dest="data",    default="./data/bhavcopy")
    parser.add_argument("--out",     dest="out",     default="./results")
    parser.add_argument("--windows", dest="windows", type=int, default=5,
                        help="Walk-forward windows (default 5 for 5-year dataset)")
    parser.add_argument("--no-equity", dest="no_eq", action="store_true",
                        help="Skip equity Bhavcopy download (faster)")
    args = parser.parse_args()

    Path(args.out).mkdir(parents=True, exist_ok=True)

    log.info("=" * 65)
    log.info("kite_source Full-Platform Backtest")
    log.info("Period : %s → %s", args.start, args.end)
    log.info("Margin : ₹%s", f"{args.margin:,.0f}")
    log.info("Windows: %d walk-forward", args.windows)
    log.info("=" * 65)

    # ── Step 1: Download data ─────────────────────────────────────────────────
    log.info("STEP 1: Downloading NSE Bhavcopy (F&O + Equity)")
    df_fo, df_eq = load_range(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        Path(args.data),
        include_equity=not args.no_eq,
    )

    df_options = filter_options(df_fo)
    log.info("Options rows: %d | Equity rows: %d", len(df_options), len(df_eq))

    if df_options.empty:
        log.error("No options data. Check dates and internet.")
        sys.exit(1)

    # ── Step 2: Split ─────────────────────────────────────────────────────────
    log.info("STEP 2: 70%%/30%% in-sample / out-of-sample split")
    in_fo,  out_fo  = in_sample_out_sample_split(df_options)
    in_eq,  out_eq  = (in_sample_out_sample_split(df_eq)
                       if not df_eq.empty else (pd.DataFrame(), pd.DataFrame()))

    # ── Step 3: Run strategies ────────────────────────────────────────────────
    log.info("STEP 3: Running %d strategies across all instruments", len(ALL_STRATEGIES))

    all_reports, all_wf_reports, all_trade_rows = [], [], []

    for strategy in FO_STRATEGIES:
        log.info("── %s", strategy.name)
        try:
            sigs_in  = strategy.generate(in_fo)
            sigs_out = strategy.generate(out_fo)
        except Exception as e:
            log.error("Signal generation failed for %s: %s", strategy.name, e)
            continue

        log.info("   Signals: %d in / %d out", len(sigs_in), len(sigs_out))

        eng_in  = BacktestEngine(in_fo)
        eng_out = BacktestEngine(out_fo)
        trades_in  = eng_in.run(sigs_in)
        trades_out = eng_out.run(sigs_out)

        log.info("   Trades:  %d in / %d out", len(trades_in), len(trades_out))

        # Compute lot value per instrument
        for phase, trades in [("in-sample", trades_in), ("out-of-sample", trades_out)]:
            if not trades:
                continue
            # Group by instrument and compute separately with correct lot size
            by_inst = {}
            for t in trades:
                inst = _instrument_from_trade(t)
                by_inst.setdefault(inst, []).append(t)

            for inst, inst_trades in by_inst.items():
                lot_val = INSTRUMENT_LOT.get(inst, 50)
                report  = compute_metrics(
                    inst_trades,
                    f"{strategy.name}_{inst} [{phase.upper()}]",
                    lot_val,
                )
                all_reports.append(report)
                _log_report(report)

            for t in trades:
                row = vars(t).copy()
                row["phase"]      = phase
                row["instrument"] = _instrument_from_trade(t)
                all_trade_rows.append(row)

        # Walk-forward on combined trades
        all_trades = trades_in + trades_out
        if all_trades:
            wf = walk_forward_metrics(all_trades, strategy.name, args.windows,
                                      INSTRUMENT_LOT.get("NIFTY", 50))
            all_wf_reports.extend(wf)

    # ── Equity strategies ─────────────────────────────────────────────────────
    if not df_eq.empty:
        for strategy in EQUITY_STRATEGIES:
            log.info("── %s (Equity)", strategy.name)
            try:
                sigs_in  = strategy.generate(in_eq)
                sigs_out = strategy.generate(out_eq)
            except Exception as e:
                log.error("Equity signal failed for %s: %s", strategy.name, e)
                continue

            log.info("   Signals: %d in / %d out", len(sigs_in), len(sigs_out))

            # For equity: size lots by margin
            for sig in sigs_in + sigs_out:
                sig.lot_size = equity_lot_value(sig.entry_price, args.margin)

            eng_in  = BacktestEngine(in_eq  if not in_eq.empty  else pd.DataFrame())
            eng_out = BacktestEngine(out_eq if not out_eq.empty else pd.DataFrame())
            trades_in  = eng_in.run(sigs_in)   if not in_eq.empty  else []
            trades_out = eng_out.run(sigs_out)  if not out_eq.empty else []

            for phase, trades in [("in-sample", trades_in), ("out-of-sample", trades_out)]:
                if not trades:
                    continue
                report = compute_metrics(trades, f"{strategy.name} [{phase.upper()}]", 1)
                all_reports.append(report)
                _log_report(report)
                for t in trades:
                    row = vars(t).copy()
                    row["phase"] = phase
                    row["instrument"] = "EQUITY"
                    all_trade_rows.append(row)

            all_trades = trades_in + trades_out
            if all_trades:
                wf = walk_forward_metrics(all_trades, strategy.name, args.windows, 1)
                all_wf_reports.extend(wf)

    # ── Step 4: Save ─────────────────────────────────────────────────────────
    log.info("STEP 4: Saving results")
    out_p = Path(args.out)

    if all_trade_rows:
        pd.DataFrame(all_trade_rows).to_csv(out_p / "backtest_trades.csv", index=False)
        log.info("Trades: %d rows", len(all_trade_rows))

    if all_reports:
        pd.DataFrame([r.as_dict() for r in all_reports]).to_csv(
            out_p / "backtest_results.csv", index=False)

    if all_wf_reports:
        pd.DataFrame([r.as_dict() for r in all_wf_reports]).to_csv(
            out_p / "walkforward_results.csv", index=False)

    _write_markdown(all_reports, all_wf_reports, args, out_p)

    log.info("=" * 65)
    log.info("DONE. Results in: %s", args.out)
    log.info("=" * 65)


def _instrument_from_trade(t: TradeResult) -> str:
    sym = (t.symbol or "").upper()
    if "BANKNIFTY" in sym:
        return "BANKNIFTY"
    if "FINNIFTY" in sym:
        return "FINNIFTY"
    if "NIFTY" in sym:
        return "NIFTY"
    return "EQUITY"


def _log_report(r):
    log.info(
        "   %-50s WR=%-6s PF=%-5s DD=%-8s Sh=%-5s N=%d Cred=%s",
        r.strategy[:50], r.win_rate, r.profit_factor,
        r.max_drawdown, r.sharpe_ratio, r.total_trades,
        r.signal_credibility[:10],
    )


def _write_markdown(reports, wf_reports, args, out_dir: Path):
    margin_cr = args.margin / 100_000

    lines = [
        "# kite_source — Full Platform Backtest Report",
        "",
        f"**Period:** `{args.start}` → `{args.end}` (5 years)",
        f"**Available margin:** ₹{margin_cr:.0f}L (₹{args.margin:,.0f})",
        f"**Instruments:** NIFTY · BANKNIFTY · FINNIFTY · Equity (top 30 stocks)",
        f"**Lot sizes:** NIFTY=50 · BANKNIFTY=15 · FINNIFTY=40 · Equity=by capital",
        f"**Split:** 70% in-sample / 30% out-of-sample (strict chronological)",
        f"**Walk-forward windows:** {args.windows}",
        "**Data source:** Real NSE F&O Bhavcopy — no mocks, no random values",
        "",
        "---",
        "",
        "## Performance Summary",
        "",
    ]

    if reports:
        cols = list(reports[0].as_dict().keys())
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in reports:
            d = r.as_dict()
            lines.append("| " + " | ".join(str(d[c]) for c in cols) + " |")
    else:
        lines.append("_No trades completed._")

    lines += ["", "---", "", "## Walk-Forward Validation", "",
              "| Strategy | Period | Trades | Win Rate | Profit Factor | Sharpe | Credibility |",
              "| --- | --- | --- | --- | --- | --- | --- |"]

    for r in wf_reports:
        d = r.as_dict()
        lines.append(
            f"| {d['Strategy']} | {d['Backtest Period']} | {d['Total Trades']} "
            f"| {d['Win Rate']} | {d['Profit Factor']} | {d['Sharpe Ratio']} "
            f"| {d['Signal Credibility']} |"
        )

    lines += [
        "", "---", "",
        "## Position Sizing Logic",
        "",
        f"| Instrument | Lot Size | Margin per lot (approx) |",
        "| --- | --- | --- |",
        f"| NIFTY | 50 units | ~₹{50*23000/5:,.0f} (5× leverage) |",
        f"| BANKNIFTY | 15 units | ~₹{15*52000/5:,.0f} (5× leverage) |",
        f"| FINNIFTY | 40 units | ~₹{40*23000/5:,.0f} (5× leverage) |",
        f"| Equity | 2% of margin / price | ~₹{args.margin*0.02:,.0f} per trade |",
        "",
        "With ₹50L margin:",
        f"- Can run {int(args.margin / (50*23000/5))} concurrent NIFTY lots",
        f"- Can run {int(args.margin / (15*52000/5))} concurrent BANKNIFTY lots",
        f"- Equity: ₹1,00,000 per trade (2% risk rule)",
        "",
        "---",
        "",
        "## Signal Credibility Scale",
        "",
        "| Rating | Criteria |",
        "| --- | --- |",
        "| STRONG | Win rate >55%, PF >1.5, Sharpe >1.0, ≥30 trades |",
        "| MODERATE | Win rate >50%, PF >1.2, Sharpe >0.5, ≥20 trades |",
        "| WEAK | Win rate >45%, PF >1.0, ≥10 trades |",
        "| UNRELIABLE | Below thresholds — do not trade live |",
        "",
        "---",
        "_Generated by kite_source backtester. All data from NSE Bhavcopy archives._",
    ]

    (out_dir / "backtest_report.md").write_text("\n".join(lines), encoding="utf-8")
    log.info("Report saved.")


if __name__ == "__main__":
    main()