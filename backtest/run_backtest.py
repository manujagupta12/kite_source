"""
backtest/run_backtest.py
========================
Main entry point. Downloads real NSE F&O Bhavcopy, runs all strategies,
produces walk-forward metrics and saves results to backtest_results.csv + report.md.

Usage:
    cd C:\AlgoTrading\kite_source\backtest
    pip install -r requirements.txt
    python run_backtest.py --from 2024-01-01 --to 2024-12-31

Results are written to:
    backtest/results/backtest_results.csv
    backtest/results/backtest_report.md
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

# Add backtest dir to path
sys.path.insert(0, str(Path(__file__).parent))

from data_fetcher import load_range, filter_nifty_options
from strategies import ALL_STRATEGIES
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


def main():
    parser = argparse.ArgumentParser(description="kite_source Backtester — real NSE F&O data")
    parser.add_argument("--from",  dest="start", default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--to",    dest="end",   default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--data",  dest="data",  default="./data/bhavcopy", help="Bhavcopy cache dir")
    parser.add_argument("--out",   dest="out",   default="./results", help="Output dir")
    parser.add_argument("--lot",   dest="lot",   type=float, default=50.0, help="Lot size (NIFTY=50)")
    parser.add_argument("--windows", dest="windows", type=int, default=4, help="Walk-forward windows")
    args = parser.parse_args()

    Path(args.data).mkdir(parents=True, exist_ok=True)
    Path(args.out).mkdir(parents=True, exist_ok=True)

    # ── Step 1: Download real Bhavcopy data ──────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 1: Downloading NSE F&O Bhavcopy data")
    log.info("=" * 60)

    raw_df = load_range(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        Path(args.data),
    )

    df_options = filter_nifty_options(raw_df)
    log.info("Options rows: %d | Symbols: %s", len(df_options),
             df_options["SYMBOL"].unique().tolist())

    if df_options.empty:
        log.error("No options data found. Check date range and internet connection.")
        sys.exit(1)

    # ── Step 2: In-sample / Out-of-sample split ───────────────────────────────
    log.info("=" * 60)
    log.info("STEP 2: Splitting data (70%% in-sample / 30%% out-of-sample)")
    log.info("=" * 60)

    in_df, out_df = in_sample_out_sample_split(df_options, in_sample_ratio=0.70)

    # ── Step 3: Run each strategy ─────────────────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 3: Running %d strategies", len(ALL_STRATEGIES))
    log.info("=" * 60)

    all_reports     = []
    all_wf_reports  = []
    all_trades_rows = []

    for strategy in ALL_STRATEGIES:
        log.info("--- Strategy: %s ---", strategy.name)

        # Generate signals on IN-SAMPLE data only
        try:
            signals_in  = strategy.generate(in_df)
            signals_out = strategy.generate(out_df)
        except Exception as e:
            log.error("Signal generation failed for %s: %s", strategy.name, e)
            continue

        log.info("  Signals: %d in-sample, %d out-of-sample", len(signals_in), len(signals_out))

        # Execute trades
        engine_in  = BacktestEngine(in_df)
        engine_out = BacktestEngine(out_df)

        trades_in  = engine_in.run(signals_in)
        trades_out = engine_out.run(signals_out)

        log.info("  Trades: %d in-sample, %d out-of-sample", len(trades_in), len(trades_out))

        # Full-period metrics (in-sample)
        if trades_in:
            report_in  = compute_metrics(trades_in,  f"{strategy.name} [IN-SAMPLE]",  args.lot)
            all_reports.append(report_in)
            _print_report(report_in)

        # Out-of-sample metrics (the honest number)
        if trades_out:
            report_out = compute_metrics(trades_out, f"{strategy.name} [OUT-OF-SAMPLE]", args.lot)
            all_reports.append(report_out)
            _print_report(report_out)

        # Walk-forward (on all data combined)
        all_trades = trades_in + trades_out
        if all_trades:
            wf = walk_forward_metrics(all_trades, strategy.name, args.windows, args.lot)
            all_wf_reports.extend(wf)

        # Collect raw trade rows for CSV
        for t in all_trades:
            row = vars(t)
            row["phase"] = "in-sample" if t.date_in < out_df["DATE"].min() else "out-of-sample"
            all_trades_rows.append(row)

    # ── Step 4: Save results ──────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("STEP 4: Saving results")
    log.info("=" * 60)

    # Raw trades CSV
    if all_trades_rows:
        trades_df = pd.DataFrame(all_trades_rows)
        trades_csv = Path(args.out) / "backtest_trades.csv"
        trades_df.to_csv(trades_csv, index=False)
        log.info("Trades saved: %s", trades_csv)

    # Summary CSV
    if all_reports:
        summary_rows = [r.as_dict() for r in all_reports]
        summary_df   = pd.DataFrame(summary_rows)
        summary_csv  = Path(args.out) / "backtest_results.csv"
        summary_df.to_csv(summary_csv, index=False)
        log.info("Summary saved: %s", summary_csv)

    # Walk-forward CSV
    if all_wf_reports:
        wf_rows = [r.as_dict() for r in all_wf_reports]
        wf_df   = pd.DataFrame(wf_rows)
        wf_csv  = Path(args.out) / "walkforward_results.csv"
        wf_df.to_csv(wf_csv, index=False)
        log.info("Walk-forward saved: %s", wf_csv)

    # Markdown report
    _write_markdown_report(all_reports, all_wf_reports, args, Path(args.out))

    log.info("=" * 60)
    log.info("BACKTEST COMPLETE. Results in: %s", args.out)
    log.info("=" * 60)


def _print_report(r):
    log.info(
        "  %-45s | WinRate=%s | PF=%s | DD=%s | Sharpe=%s | Trades=%d | Cred=%s",
        r.strategy[:45],
        r.win_rate, r.profit_factor, r.max_drawdown,
        r.sharpe_ratio, r.total_trades, r.signal_credibility,
    )


def _write_markdown_report(reports, wf_reports, args, out_dir: Path):
    lines = [
        "# kite_source — Backtest Report",
        "",
        f"**Period:** `{args.start}` → `{args.end}`",
        f"**Data:** Real NSE F&O Bhavcopy (no mocks, no random values)",
        f"**Lot size:** {args.lot} units",
        f"**Split:** 70% in-sample / 30% out-of-sample",
        f"**Walk-forward windows:** {args.windows}",
        "",
        "---",
        "",
        "## Performance Summary",
        "",
    ]

    if reports:
        # Table header
        cols = list(reports[0].as_dict().keys())
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in reports:
            d = r.as_dict()
            lines.append("| " + " | ".join(str(d[c]) for c in cols) + " |")
    else:
        lines.append("_No completed trades. Check data range or strategy thresholds._")

    lines += [
        "",
        "---",
        "",
        "## Walk-Forward Validation",
        "",
        "_Consistent performance across windows = signal is real, not curve-fitted._",
        "",
    ]

    if wf_reports:
        cols = ["Strategy", "Win Rate", "Profit Factor", "Sharpe Ratio", "Total Trades", "Signal Credibility"]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for r in wf_reports:
            d = r.as_dict()
            lines.append(
                f"| {d['Strategy']} | {d['Win Rate']} | {d['Profit Factor']} | "
                f"{d['Sharpe Ratio']} | {d['Total Trades']} | {d['Signal Credibility']} |"
            )

    lines += [
        "",
        "---",
        "",
        "## Signal Credibility Guide",
        "",
        "| Rating | Criteria |",
        "| --- | --- |",
        "| **STRONG** | Win rate >55%, Profit Factor >1.5, Sharpe >1.0, Trades ≥30 |",
        "| **MODERATE** | Win rate >50%, Profit Factor >1.2, Sharpe >0.5, Trades ≥20 |",
        "| **WEAK** | Win rate >45%, Profit Factor >1.0, Trades ≥10 |",
        "| **UNRELIABLE** | Anything below — do not trade live |",
        "",
        "---",
        "",
        "## Entry / Exit Signal Logic",
        "",
        "### PCR Mean-Reversion",
        "- **Entry:** PCR >1.4 → Buy ATM Call | PCR <0.7 → Buy ATM Put",
        "- **Exit:** 25% gain on premium (target) or 40% loss on premium (stop)",
        "- **Why:** Extreme PCR readings historically mean-revert. High PCR = excess fear = oversold.",
        "",
        "### ATM Strangle",
        "- **Entry:** Sell ATM CE + ATM PE on weekly expiry when both premiums >₹30",
        "- **Exit:** Collect 50% of net credit (target) or debit reaches 2× credit (stop)",
        "- **Why:** Time decay (theta) favours sellers when IV is elevated near expiry.",
        "",
        "### EMA Crossover (Futures)",
        "- **Entry:** EMA9 crosses EMA21 with RSI confirmation (>50 for BUY, <50 for SELL)",
        "- **Exit:** 2×ATR target, 1×ATR stop",
        "- **Why:** Trend-following on momentum; RSI filter reduces false crossovers.",
        "",
        "### OI Build-up",
        "- **Entry:** PE OI builds above ATM → support → BUY | CE OI builds below ATM → resistance → SELL",
        "- **Exit:** 1.5% spot move (target), 0.7% adverse move (stop)",
        "- **Why:** Large OI concentrations indicate institutional positioning and act as price magnets.",
        "",
        "### Calendar Spread",
        "- **Entry:** Near-month IV premium >10% over far-month at same strike",
        "- **Exit:** Near-month premium decays to 40% of entry value",
        "- **Why:** Near-month theta decays faster; IV skew normalises as expiry approaches.",
        "",
        "---",
        "",
        "## Risk Management Rules",
        "- Max position size set by `position_sizer.py` based on available margin",
        "- Hard stop on each trade (coded in engine.py)",
        "- Max hold: 30 calendar days (forced exit)",
        "- Transaction costs deducted: ₹20 brokerage/leg + 0.05% STT on sell side",
        "",
        "## Crash / Outage Behaviour",
        "- Strategy signals are stateless — re-running on same data produces same signals",
        "- No open positions are carried across sessions without explicit confirmation",
        "- `detect_atm()` uses dynamic strike range (not hardcoded) so circuit-breaker events don't break it",
        "",
        "---",
        "_Generated by kite_source backtest engine. Data source: NSE F&O Bhavcopy archives._",
    ]

    report_path = out_dir / "backtest_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("Markdown report saved: %s", report_path)


if __name__ == "__main__":
    main()
