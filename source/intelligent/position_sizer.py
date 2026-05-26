"""
DYNAMIC POSITION SIZER  —  position_sizer.py
=============================================
Shared module used by calendar.py and multistrategy.py.

HOW IT WORKS:
  1. At startup, asks the user for today's available margin
  2. For every signal, computes the OPTIMAL lot size dynamically based on:
       - Signal quality score (0-100)
       - Current VIX level
       - Market regime risk level (R1-R8)
       - Strategy risk type (calendar = low risk, straddle = high risk)
       - Already-deployed margin today
       - Win/loss streak (reduce size after losses, increase after wins)
  3. Can be as low as 1 lot or as high as the margin allows
  4. Supports trade churning — multiple entries and exits during the day
  5. User can update available margin any time via Ctrl+C menu

SIZING PHILOSOPHY:
  - NOT fixed lots. NOT fixed percentage. NOT 62 always.
  - Intelligent fractional Kelly-inspired sizing:
    * Strong signal + low VIX + good regime  →  larger size
    * Weak signal + high VIX + bad regime   →  1–2 lots only
    * After 2 consecutive losses             →  half size until win
    * After 3 consecutive wins              →  modestly increase size
    * Never exceed user's margin budget
    * Never deploy more than MAX_SINGLE_TRADE_PCT in one trade

USAGE:
  from position_sizer import PositionSizer
  sizer = PositionSizer()         # asks margin at startup
  lots  = sizer.get_lots("S1 CALENDAR", score=82, vix=14.5, regime="R2")
  sizer.record_trade(pnl_inr)     # call after each trade closes
  sizer.show_status()             # print current sizing state
"""

import os
from datetime import date, datetime

# ════════════════════════════════════════════════════════════════
#  STRATEGY RISK PROFILES
#  Each strategy has a base risk level that affects max sizing.
#  Lower number = lower risk = can deploy more capital per trade.
# ════════════════════════════════════════════════════════════════
STRATEGY_RISK = {
    "S1 CALENDAR":         {"risk": 1, "max_pct": 0.40, "name": "Calendar Spread"},
    "S2 IRON CONDOR":      {"risk": 1, "max_pct": 0.40, "name": "Iron Condor"},
    "S7 RATIO SPREAD":     {"risk": 2, "max_pct": 0.25, "name": "Ratio Spread"},
    "S3 SHORT STRADDLE":   {"risk": 3, "max_pct": 0.20, "name": "Short Straddle"},
    "S4 MOMENTUM":         {"risk": 3, "max_pct": 0.20, "name": "Momentum"},
    "S5 DELTA STRANGLE":   {"risk": 2, "max_pct": 0.25, "name": "Delta Strangle"},
    "S6 EXPIRY 0DTE":      {"risk": 4, "max_pct": 0.15, "name": "Expiry 0DTE"},
    "MANUAL":              {"risk": 2, "max_pct": 0.30, "name": "Manual"},
}

# VIX-based size multipliers
VIX_MULT = [
    (13,  1.00),   # VIX < 13 → full size
    (16,  0.85),   # VIX 13-16 → 85%
    (19,  0.65),   # VIX 16-19 → 65%
    (22,  0.40),   # VIX 19-22 → 40%
    (999, 0.15),   # VIX > 22  → 15% (extreme panic — tiny size only)
]

# Regime-based size multipliers (matches regime_engine.py)
REGIME_MULT = {
    "R1_DEAD":          0.10,   # dead market — almost no sizing
    "R2_SIDEWAYS_LOW":  1.00,   # ideal — full
    "R3_SIDEWAYS_HIGH_IV": 0.80,
    "R4_TRENDING_BULL": 0.70,
    "R5_TRENDING_BEAR": 0.70,
    "R6_HIGH_VOL":      0.45,
    "R7_EXPIRY":        0.55,
    "R8_EXTREME_PANIC": 0.05,   # near-zero — protect capital
}

# Margin per lot per instrument
MARGINS = {"NIFTY": 80000, "BANKNIFTY": 90000, "FINNIFTY": 50000}
LOT_SIZES = {"NIFTY": 25, "BANKNIFTY": 15, "FINNIFTY": 40}

# ════════════════════════════════════════════════════════════════
#  POSITION SIZER CLASS
# ════════════════════════════════════════════════════════════════
class PositionSizer:
    """
    Intelligent, dynamic lot sizer. Instantiate once at startup.
    """

    # Max % of available margin in a single trade
    MAX_SINGLE_TRADE_PCT = 0.50   # never more than 50% in one trade

    def __init__(self):
        self.available_margin  = 0.0    # set by user at startup
        self.deployed_margin   = 0.0    # currently in open trades
        self.total_pnl_today   = 0.0    # running P&L
        self.trade_history     = []     # list of pnl_inr values (recent trades)
        self.win_streak        = 0
        self.loss_streak       = 0
        self._setup_date       = str(date.today())

        # Ask margin from user
        self._ask_margin()

    # ── Startup margin input ─────────────────────────────────────
    def _ask_margin(self):
        print("\n" + "=" * 60)
        print("  POSITION SIZER — Margin Setup")
        print("=" * 60)
        print("  Enter the margin available for trading today.")
        print("  This determines maximum lot sizes dynamically.")
        print("  You can update this any time during the session.\n")

        while True:
            try:
                raw = input("  Available Margin today (₹): ").strip()
                # Accept formats: 5000000 / 50L / 50l / 50,00,000
                raw = raw.replace(",", "").upper()
                if raw.endswith("L"):
                    val = float(raw[:-1]) * 100_000
                elif raw.endswith("CR") or raw.endswith("C"):
                    val = float(raw.rstrip("CR")) * 10_000_000
                else:
                    val = float(raw)

                if val <= 0:
                    print("  Please enter a positive amount.")
                    continue

                self.available_margin = val
                self._print_margin_summary()
                break
            except (ValueError, KeyboardInterrupt):
                print("  Invalid input. Try: 5000000  or  50L  or  25L")

    def _print_margin_summary(self):
        m = self.available_margin
        print(f"\n  ✅ Margin set: ₹{m:,.0f}")
        for inst in ["NIFTY", "BANKNIFTY", "FINNIFTY"]:
            max_lots = int(m / MARGINS[inst])
            print(f"     {inst:<12} → Max {max_lots} lots "
                  f"(₹{MARGINS[inst]:,}/lot × {max_lots} = ₹{max_lots*MARGINS[inst]:,})")
        print(f"  ⚠  Sizing is DYNAMIC — actual lots vary by signal quality,")
        print(f"     VIX, regime, and risk profile. Range: 1 lot to {int(m/min(MARGINS.values()))} lots.")
        print()

    # ── Update margin mid-session ────────────────────────────────
    def update_margin(self, new_margin: float = None):
        """Call this to let user update margin during session."""
        if new_margin is None:
            try:
                raw = input(f"\n  Current margin: ₹{self.available_margin:,.0f}"
                            f"\n  New margin (Enter to keep): ").strip()
                if not raw:
                    return
                raw = raw.replace(",", "").upper()
                if raw.endswith("L"):
                    new_margin = float(raw[:-1]) * 100_000
                else:
                    new_margin = float(raw)
            except (ValueError, KeyboardInterrupt):
                return
        self.available_margin = new_margin
        print(f"  ✅ Margin updated to ₹{new_margin:,.0f}")
        self._print_margin_summary()

    # ── Core sizing function ─────────────────────────────────────
    def get_lots(self, strategy: str, score: int, vix: float,
                 regime_key: str = None, instrument: str = "NIFTY") -> int:
        """
        Returns the recommended number of lots for this signal.

        Parameters:
          strategy   : strategy name e.g. "S1 CALENDAR"
          score      : signal quality score 0-100
          vix        : current India VIX
          regime_key : regime engine key e.g. "R2_SIDEWAYS_LOW"
          instrument : "NIFTY", "BANKNIFTY", "FINNIFTY"

        Returns: int (minimum 1, maximum = margin-constrained)
        """
        margin_per_lot = MARGINS.get(instrument, 80000)
        free_margin    = max(0, self.available_margin - self.deployed_margin)

        if free_margin < margin_per_lot:
            return 0   # no margin left — cannot enter

        # ── Step 1: Base lots = free margin / margin per lot ─────
        absolute_max = int(free_margin / margin_per_lot)
        if absolute_max == 0:
            return 0

        # ── Step 2: Strategy max % cap ───────────────────────────
        strat_info = self._match_strategy(strategy)
        strat_max_pct = strat_info["max_pct"]

        # Single trade cannot use more than MAX_SINGLE_TRADE_PCT of total margin
        single_cap    = min(strat_max_pct, self.MAX_SINGLE_TRADE_PCT)
        cap_lots      = max(1, int((self.available_margin * single_cap) / margin_per_lot))

        # ── Step 3: Score multiplier (0-100 → 0.1 to 1.0) ───────
        # Below 40 = very weak, above 85 = very strong
        if score >= 85:
            score_mult = 1.00
        elif score >= 75:
            score_mult = 0.80
        elif score >= 65:
            score_mult = 0.60
        elif score >= 55:
            score_mult = 0.40
        elif score >= 45:
            score_mult = 0.25
        else:
            score_mult = 0.10   # very weak signal → 1-2 lots only

        # ── Step 4: VIX multiplier ───────────────────────────────
        vix_mult = 1.00
        if vix is not None:
            for threshold, mult in VIX_MULT:
                if vix < threshold:
                    vix_mult = mult
                    break

        # ── Step 5: Regime multiplier ────────────────────────────
        regime_mult = 1.00
        if regime_key:
            regime_mult = REGIME_MULT.get(regime_key, 0.70)

        # ── Step 6: Streak adjustment ────────────────────────────
        streak_mult = 1.00
        if self.loss_streak >= 3:
            streak_mult = 0.40   # cut to 40% after 3 losses in a row
        elif self.loss_streak == 2:
            streak_mult = 0.60
        elif self.loss_streak == 1:
            streak_mult = 0.80
        elif self.win_streak >= 4:
            streak_mult = 1.20   # modest boost after winning run (capped below)
        elif self.win_streak >= 2:
            streak_mult = 1.10

        # ── Step 7: Combine all multipliers ──────────────────────
        combined  = score_mult * vix_mult * regime_mult * streak_mult
        raw_lots  = cap_lots * combined
        lots      = max(1, round(raw_lots))   # always at least 1 lot

        # ── Step 8: Final hard cap ────────────────────────────────
        lots = min(lots, absolute_max, cap_lots)

        return int(lots)

    def _match_strategy(self, strategy: str) -> dict:
        """Fuzzy match strategy name to risk profile."""
        strategy_upper = strategy.upper()
        for key, val in STRATEGY_RISK.items():
            if key.upper() in strategy_upper or strategy_upper in key.upper():
                return val
        return STRATEGY_RISK["MANUAL"]

    # ── Margin tracking ──────────────────────────────────────────
    def deploy(self, lots: int, instrument: str = "NIFTY"):
        """Call when a trade is entered. Tracks deployed margin."""
        cost = lots * MARGINS.get(instrument, 80000)
        self.deployed_margin += cost

    def release(self, lots: int, instrument: str = "NIFTY"):
        """Call when a trade is closed. Frees up margin."""
        cost = lots * MARGINS.get(instrument, 80000)
        self.deployed_margin = max(0, self.deployed_margin - cost)

    # ── P&L and streak tracking ──────────────────────────────────
    def record_trade(self, pnl_inr: float, lots: int = 1,
                     instrument: str = "NIFTY"):
        """
        Call after every trade closes.
        Updates P&L, win/loss streak, and releases margin.
        """
        self.total_pnl_today += pnl_inr
        self.trade_history.append(pnl_inr)
        self.release(lots, instrument)

        # Update streaks
        if pnl_inr > 0:
            self.win_streak  += 1
            self.loss_streak  = 0
        elif pnl_inr < 0:
            self.loss_streak += 1
            self.win_streak   = 0
        # pnl == 0 doesn't change streak

    # ── Status display ───────────────────────────────────────────
    def show_status(self):
        free = max(0, self.available_margin - self.deployed_margin)
        trades = len(self.trade_history)
        wins   = sum(1 for p in self.trade_history if p > 0)
        win_rate = round(wins / trades * 100, 1) if trades > 0 else 0

        streak_msg = ""
        if self.win_streak >= 2:
            streak_msg = f"  🔥 Win streak: {self.win_streak}"
        elif self.loss_streak >= 2:
            streak_msg = f"  ❄  Loss streak: {self.loss_streak} — sizing reduced"

        print(f"""
  ┌─────────────────────────────────────────────────────┐
  │  POSITION SIZER STATUS  |  {datetime.now().strftime('%H:%M:%S')}
  │  Available Margin : ₹{self.available_margin:>12,.0f}
  │  Deployed         : ₹{self.deployed_margin:>12,.0f}
  │  Free             : ₹{free:>12,.0f}
  │  Today's P&L      : ₹{self.total_pnl_today:>+12,.0f}
  │  Trades Today     : {trades}  |  Win Rate: {win_rate}%{streak_msg}
  └─────────────────────────────────────────────────────┘""")

    def explain_lots(self, strategy: str, score: int, vix: float,
                     regime_key: str, instrument: str, lots: int):
        """
        Prints a breakdown of exactly WHY this many lots were recommended.
        Called alongside every signal so trader understands the sizing.
        """
        strat_info    = self._match_strategy(strategy)
        margin_per_lot = MARGINS.get(instrument, 80000)
        free           = max(0, self.available_margin - self.deployed_margin)
        abs_max        = int(free / margin_per_lot)
        cap_lots       = max(1, int((self.available_margin * strat_info["max_pct"]) / margin_per_lot))

        if score >= 85:   score_mult, sq = 1.00, "Very strong signal"
        elif score >= 75: score_mult, sq = 0.80, "Strong signal"
        elif score >= 65: score_mult, sq = 0.60, "Good signal"
        elif score >= 55: score_mult, sq = 0.40, "Moderate signal"
        elif score >= 45: score_mult, sq = 0.25, "Weak signal"
        else:             score_mult, sq = 0.10, "Very weak signal"

        vix_mult = 1.00
        vix_desc = "N/A"
        if vix is not None:
            for threshold, mult in VIX_MULT:
                if vix < threshold:
                    vix_mult = mult
                    vix_desc = f"VIX {vix:.1f} → {int(mult*100)}% size"
                    break

        regime_mult = REGIME_MULT.get(regime_key, 0.70)
        streak_mult = (0.40 if self.loss_streak>=3 else
                       0.60 if self.loss_streak==2 else
                       0.80 if self.loss_streak==1 else
                       1.20 if self.win_streak>=4 else
                       1.10 if self.win_streak>=2 else 1.00)

        margin_cost = lots * margin_per_lot
        print(f"""
  ┌─ LOT SIZING BREAKDOWN ──────────────────────────────┐
  │  Strategy cap    : {cap_lots} lots  ({int(strat_info['max_pct']*100)}% of ₹{self.available_margin:,.0f})
  │  Score ({score}/100) : ×{score_mult:.2f}  [{sq}]
  │  VIX factor      : ×{vix_mult:.2f}  [{vix_desc}]
  │  Regime factor   : ×{regime_mult:.2f}  [{regime_key or 'unknown'}]
  │  Streak factor   : ×{streak_mult:.2f}  [W:{self.win_streak} L:{self.loss_streak}]
  │  ─────────────────────────────────────────────────
  │  RECOMMENDED     : {lots} lot{"s" if lots!=1 else ""}  (₹{margin_cost:,.0f} margin)
  │  Free after entry: ₹{max(0,free-margin_cost):,.0f}
  └─────────────────────────────────────────────────────┘""")


# ════════════════════════════════════════════════════════════════
#  STANDALONE TEST
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    sizer = PositionSizer()

    print("\n=== SAMPLE SIZING SCENARIOS ===\n")

    scenarios = [
        ("S1 CALENDAR",     87, 13.5, "R2_SIDEWAYS_LOW",  "NIFTY"),
        ("S1 CALENDAR",     87, 13.5, "R2_SIDEWAYS_LOW",  "NIFTY"),   # 2nd trade same day
        ("S2 IRON CONDOR",  72, 16.2, "R3_SIDEWAYS_HIGH_IV", "NIFTY"),
        ("S3 SHORT STRADDLE",60,20.1, "R6_HIGH_VOL",      "NIFTY"),
        ("S4 MOMENTUM",     55, 14.0, "R4_TRENDING_BULL", "BANKNIFTY"),
        ("S1 CALENDAR",     42, 21.5, "R6_HIGH_VOL",      "NIFTY"),   # weak + high vix
        ("S6 EXPIRY 0DTE",  81, 15.0, "R7_EXPIRY",        "NIFTY"),
    ]

    for strat, score, vix, regime, inst in scenarios:
        lots = sizer.get_lots(strat, score, vix, regime, inst)
        print(f"  {strat:<25} Score:{score}  VIX:{vix}  → {lots} lot{'s' if lots!=1 else ''}")
        sizer.explain_lots(strat, score, vix, regime, inst, lots)
        sizer.deploy(lots, inst)

    sizer.show_status()
