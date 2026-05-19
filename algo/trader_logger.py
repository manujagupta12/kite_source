"""
TRADE LOGGER  —  trade_logger.py
==================================
CSV-based trade log for all algo strategies.
Used by multistrategy.py and Calendaralgofinal.py.

Log location: C:\\AlgoTrading\\logs\\trades_YYYYMMDD.csv

CSV columns:
  time, strategy, instrument, type (CE/PE), direction,
  near_strike, far_strike, lots, entry_spread, exit_spread,
  pnl_pts, pnl_inr, status, notes

Public API used by algo scripts:
  load_today()                      — load today's CSV into memory
  get_daily_pnl()                   — returns (realised, open_count, total_trades)
  interactive_input(mode)           — terminal menu to enter/close trades
  print_daily_summary()             — print P&L summary table
"""

import os
import csv
from datetime import datetime, date

# ── Log directory ─────────────────────────────────────────────
LOG_DIR  = r"C:\AlgoTrading\logs"
LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}

# ── In-memory trade list (loaded from today's CSV) ────────────
_trades: list[dict] = []
_log_path: str = ""


def _get_log_path() -> str:
    """Return today's log file path, creating the directory if needed."""
    global _log_path
    os.makedirs(LOG_DIR, exist_ok=True)
    _log_path = os.path.join(LOG_DIR, f"trades_{date.today().strftime('%Y%m%d')}.csv")
    return _log_path


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


FIELDNAMES = [
    "time", "strategy", "instrument", "type", "direction",
    "near_strike", "far_strike", "lots", "entry_spread",
    "exit_spread", "pnl_pts", "pnl_inr", "status", "notes"
]


def _write_all():
    """Overwrite the CSV with current in-memory trades."""
    path = _get_log_path()
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        w.writerows(_trades)


def _append_row(row: dict):
    """Append one row to the CSV."""
    path = _get_log_path()
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            w.writeheader()
        w.writerow(row)


# ════════════════════════════════════════════════════════════════
#  PUBLIC API
# ════════════════════════════════════════════════════════════════
def load_today():
    """Load today's trade log from CSV into memory. Call once at startup."""
    global _trades
    _trades = []
    path = _get_log_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                _trades.append(dict(row))
        print(f"  [Logger] Loaded {len(_trades)} trades from {path}")
    except Exception as e:
        print(f"  [Logger] Could not load {path}: {e}")


def get_daily_pnl() -> tuple[float, int, int]:
    """
    Returns (realised_pnl_inr, open_trade_count, total_trades).
    realised_pnl_inr — sum of P&L for CLOSED trades today.
    open_trade_count — number of OPEN trades.
    total_trades     — total entries today.
    """
    realised = 0.0
    open_count = 0
    for t in _trades:
        status = str(t.get("status", "")).upper()
        if status == "CLOSED":
            try:
                realised += float(t.get("pnl_inr") or 0)
            except (TypeError, ValueError):
                pass
        elif status == "OPEN":
            open_count += 1
    return round(realised, 2), open_count, len(_trades)


def _input_trade(mode: str = "Enter Trade"):
    """Prompt user to enter a new trade."""
    print(f"\n  ── {mode} ──────────────────────────────")
    try:
        strategy  = input("  Strategy (e.g. S1 CALENDAR): ").strip() or "MANUAL"
        instr     = input("  Instrument (NIFTY/BANKNIFTY/FINNIFTY) [BANKNIFTY]: ").strip().upper() or "BANKNIFTY"
        typ       = input("  Type (CE/PE/BOTH) [CE]: ").strip().upper() or "CE"
        direction = input("  Direction (LONG/SHORT) [LONG]: ").strip().upper() or "LONG"
        n_strike  = input("  Near Strike: ").strip() or "0"
        f_strike  = input("  Far  Strike [same]: ").strip() or n_strike
        lots      = input("  Lots [1]: ").strip() or "1"
        spread    = input("  Entry Spread (pts): ").strip() or "0"
        notes     = input("  Notes (optional): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  [Logger] Trade entry cancelled.")
        return

    ls      = LOT_SIZES.get(instr, 25)
    try:
        lots_n   = int(lots)
        spread_f = float(spread)
    except ValueError:
        lots_n, spread_f = 1, 0.0

    row = {
        "time":         _ts(),
        "strategy":     strategy,
        "instrument":   instr,
        "type":         typ,
        "direction":    direction,
        "near_strike":  n_strike,
        "far_strike":   f_strike,
        "lots":         lots_n,
        "entry_spread": round(spread_f, 2),
        "exit_spread":  "",
        "pnl_pts":      "",
        "pnl_inr":      "",
        "status":       "OPEN",
        "notes":        notes,
    }
    _trades.append(row)
    _append_row(row)
    print(f"\n  ✅ Trade logged — {instr} {typ} {direction} x{lots_n} @ {spread_f:.2f}pts")


def _close_trade():
    """Prompt user to close an open trade."""
    open_trades = [(i, t) for i, t in enumerate(_trades)
                   if str(t.get("status", "")).upper() == "OPEN"]
    if not open_trades:
        print("\n  No open trades to close.")
        return

    print(f"\n  ── Close Trade ─────────────────────────")
    print(f"  {'#':<3}  {'Strategy':<20} {'Instrument':<12} {'Dir':<6} {'Entry':>8}  {'Lots':>4}")
    print("  " + "─" * 60)
    for idx, (i, t) in enumerate(open_trades, 1):
        print(f"  {idx:<3}  {t.get('strategy',''):<20} "
              f"{t.get('instrument',''):<12} {t.get('direction',''):<6} "
              f"{t.get('entry_spread',''):>8}  {t.get('lots',''):>4}")

    try:
        sel = input(f"\n  Select trade to close (1–{len(open_trades)}): ").strip()
        idx_sel = int(sel) - 1
        if not (0 <= idx_sel < len(open_trades)):
            print("  Invalid selection.")
            return
        orig_idx, trade = open_trades[idx_sel]
        exit_spread = float(input("  Exit Spread (pts): ").strip() or "0")
        notes       = input("  Notes (optional): ").strip()
    except (EOFError, KeyboardInterrupt, ValueError):
        print("\n  [Logger] Close cancelled.")
        return

    ls   = LOT_SIZES.get(trade.get("instrument", "BANKNIFTY"), 25)
    lots = int(trade.get("lots") or 1)
    try:
        entry = float(trade.get("entry_spread") or 0)
    except ValueError:
        entry = 0.0

    dirn    = str(trade.get("direction", "")).upper()
    pnl_pts = round((exit_spread - entry) if dirn in ("LONG", "BUY") else (entry - exit_spread), 2)
    pnl_inr = round(pnl_pts * ls * lots, 0)

    trade.update({
        "exit_spread": round(exit_spread, 2),
        "pnl_pts":     pnl_pts,
        "pnl_inr":     int(pnl_inr),
        "status":      "CLOSED",
        "notes":       (str(trade.get("notes", "")) + " " + notes).strip(),
    })
    _trades[orig_idx] = trade
    _write_all()

    emoji = "✅" if pnl_inr >= 0 else "❌"
    print(f"\n  {emoji} Trade closed — P&L: {pnl_pts:+.2f}pts  (₹{int(pnl_inr):+,})")


def interactive_input(mode: str = "Enter Trade"):
    """
    Called by algo scripts on Ctrl+C menu.
    mode: "Enter Trade" or "Close Trade"
    """
    if "close" in mode.lower():
        _close_trade()
    else:
        _input_trade(mode)


def print_daily_summary():
    """Print a formatted P&L summary of today's trades."""
    realised, open_count, total = get_daily_pnl()

    print(f"""
  ╔══════════════════════════════════════════════════════╗
  ║  TRADE LOG SUMMARY  —  {date.today().strftime('%d %b %Y')}
  ╠══════════════════════════════════════════════════════╣
  ║  Total trades  : {total:<4}
  ║  Open          : {open_count:<4}
  ║  Closed        : {total - open_count:<4}
  ║  Realised P&L  : ₹{realised:>+12,.0f}
  ╚══════════════════════════════════════════════════════╝""")

    if not _trades:
        return

    closed = [t for t in _trades if str(t.get("status", "")).upper() == "CLOSED"]
    if not closed:
        print("  No closed trades today.")
        return

    print(f"\n  {'Time':<8} {'Strategy':<18} {'Instr':<10} {'Dir':<6} "
          f"{'Entry':>7} {'Exit':>7} {'P&L pts':>8} {'P&L ₹':>10}")
    print("  " + "─" * 78)
    for t in closed:
        pnl = float(t.get("pnl_pts") or 0)
        inr = int(float(t.get("pnl_inr") or 0))
        col = "+" if pnl >= 0 else ""
        print(f"  {t.get('time',''):<8} {t.get('strategy',''):<18} "
              f"{t.get('instrument',''):<10} {t.get('direction',''):<6} "
              f"{t.get('entry_spread',''):>7} {t.get('exit_spread',''):>7} "
              f"{col}{pnl:>7.2f}pts  ₹{inr:>+9,}")

    wins  = [t for t in closed if float(t.get("pnl_inr") or 0) > 0]
    if closed:
        wr = round(len(wins) / len(closed) * 100, 0)
        print(f"\n  Win rate: {int(wr)}%  ({len(wins)}/{len(closed)} trades)")
    print(f"  Log file: {_get_log_path()}\n")


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    print("trade_logger.py — self test")
    load_today()
    r, o, t = get_daily_pnl()
    print(f"  Today: {t} trades, {o} open, ₹{r:,.0f} realised")
    print_daily_summary()