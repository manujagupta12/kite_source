import time
import pandas as pd
import os
from datetime import datetime

# ================= CONFIG =================
FILE_PATH = r"C:\AlgoTrading\data\multitrade_feed.xls"

# Set to None for AUTO-DETECT nearest ATM strike
STRIKE = None

THRESHOLD = 3
TARGET = 4
STOPLOSS = 3
REFRESH = 2

# ================= STATE =================
ce_position = None
ce_entry = None
pe_position = None
pe_entry = None
last_ce_spread = None
last_pe_spread = None
last_modified = None


# ================= FORCE FRESH READ =================
def read_fresh_excel():
    """
    Force fresh read by checking file modification time.
    Returns None if file hasn't changed.
    """
    global last_modified

    try:
        current_modified = os.path.getmtime(FILE_PATH)

        if last_modified == current_modified:
            return None  # No change, skip re-read

        last_modified = current_modified

        # Read with no caching
        raw = pd.read_excel(
            FILE_PATH,
            header=None,
            engine="xlrd"
        )
        return raw

    except PermissionError:
        print("File locked by Excel. Waiting...")
        return None
    except Exception as e:
        print(f"Read error: {e}")
        return None


# ================= DATA EXTRACTION =================
def extract_data(raw):
    if raw is None or raw.empty:
        return None, None, None, None

    raw = raw.dropna(axis=0, how="all")
    raw = raw.dropna(axis=1, how="all")

    # Find header row (contains BID, ASK, LTP)
    header_idx = None
    for i in range(min(20, len(raw))):
        row_vals = [str(x).strip().upper() for x in raw.iloc[i].tolist()]
        if "BID" in row_vals and "ASK" in row_vals:
            header_idx = i
            break

    if header_idx is None:
        return None, None, None, None

    headers = raw.iloc[header_idx].fillna("").tolist()
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = headers

    df = df.dropna(axis=0, how="all")

    # Rename columns
    colnames = df.columns.tolist()
    if len(colnames) < 6:
        return None, None, None, None

    colnames[0] = "Type"
    colnames[1] = "Strike"
    colnames[2] = "Strike2"
    colnames[3] = "Strike3"
    df.columns = colnames

    # Clean data
    df["Type"] = df["Type"].astype(str).str.strip().str.upper()
    df["Strike"] = pd.to_numeric(df["Strike"], errors="coerce")
    df["BID"] = pd.to_numeric(df["BID"], errors="coerce")
    df["ASK"] = pd.to_numeric(df["ASK"], errors="coerce")
    df["LTP"] = pd.to_numeric(df["LTP"], errors="coerce")

    # Separate CE and PE
    df_ce = df[df["Type"] == "CE"].reset_index(drop=True)
    df_pe = df[df["Type"] == "PE"].reset_index(drop=True)

    if df_ce.empty or df_pe.empty:
        return None, None, None, None

    # Split NEAR / FAR for CE
    ce_near = df_ce.iloc[0::2].reset_index(drop=True)
    ce_far = df_ce.iloc[1::2].reset_index(drop=True)

    # Split NEAR / FAR for PE
    pe_near = df_pe.iloc[0::2].reset_index(drop=True)
    pe_far = df_pe.iloc[1::2].reset_index(drop=True)

    return ce_near, ce_far, pe_near, pe_far


# ================= AUTO DETECT STRIKE =================
def auto_detect_strike(ce_near):
    """
    Find the strike with highest volume (most liquid = ATM proxy)
    """
    if "VOLUME" in ce_near.columns:
        ce_near["VOLUME"] = pd.to_numeric(ce_near["VOLUME"], errors="coerce")
        idx = ce_near["VOLUME"].idxmax()
        return int(ce_near.loc[idx, "Strike"])
    else:
        # Return middle strike
        strikes = ce_near["Strike"].dropna().astype(int).tolist()
        return strikes[len(strikes) // 2] if strikes else None


# ================= SPREAD CALCULATION =================
def calc_spread(near_df, far_df, strike):
    near = near_df[near_df["Strike"] == strike]
    far = far_df[far_df["Strike"] == strike]

    if near.empty or far.empty:
        return None

    spread = float(far["BID"].iloc[0]) - float(near["ASK"].iloc[0])
    return spread


# ================= SIGNAL LOGIC =================
def get_signal(spread, fair=0):
    if spread < fair - THRESHOLD:
        return "LONG"
    if spread > fair + THRESHOLD:
        return "SHORT"
    return "WAIT"


def check_exit(position, entry, current):
    if position == "LONG":
        pnl = current - entry
    else:
        pnl = entry - current

    if pnl >= TARGET:
        return "TARGET", pnl
    if pnl <= -STOPLOSS:
        return "STOPLOSS", pnl
    return None, pnl


# ================= ALERT DISPLAY =================
def alert(msg, alert_type="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    symbols = {"ENTRY": ">>>", "EXIT": "<<<", "INFO": "---", "WARN": "!!!"}
    print(f"\n{symbols.get(alert_type, '---')} [{now}] {msg}")


# ================= MAIN LOOP =================
print("\n" + "=" * 60)
print(" NIFTY CALENDAR SPREAD — CE + PE DUAL ALERTS")
print(" Real-Time Live Excel Feed | Model B (Semi-Auto)")
print("=" * 60)

active_strike = STRIKE
cached_raw = None

while True:
    try:
        # Read fresh data only if file changed
        raw = read_fresh_excel()

        if raw is not None:
            cached_raw = raw

        if cached_raw is None:
            time.sleep(REFRESH)
            continue

        ce_near, ce_far, pe_near, pe_far = extract_data(cached_raw)

        if ce_near is None:
            print("Waiting for valid data...")
            time.sleep(REFRESH)
            continue

        # Auto-detect strike if not set
        if active_strike is None:
            active_strike = auto_detect_strike(ce_near)
            print(f"\n[AUTO] Detected ATM Strike: {active_strike}")

        # Calculate spreads
        ce_spread = calc_spread(ce_near, ce_far, active_strike)
        pe_spread = calc_spread(pe_near, pe_far, active_strike)

        now = datetime.now().strftime("%H:%M:%S")

        # ========== CE ALERTS ==========
        if ce_spread is not None:
            if ce_spread != last_ce_spread:
                print(f"[{now}] CE {active_strike} | Spread: {ce_spread:.2f}")
                last_ce_spread = ce_spread

            if ce_position is None:
                sig = get_signal(ce_spread)
                if sig == "LONG":
                    ce_position = "LONG"
                    ce_entry = ce_spread
                    alert(f"CE LONG ENTRY @ {ce_spread:.2f}", "ENTRY")
                    alert(f"ACTION: BUY FAR CE + SELL NEAR CE", "INFO")

                elif sig == "SHORT":
                    ce_position = "SHORT"
                    ce_entry = ce_spread
                    alert(f"CE SHORT ENTRY @ {ce_spread:.2f}", "ENTRY")
                    alert(f"ACTION: SELL FAR CE + BUY NEAR CE", "INFO")

            else:
                reason, pnl = check_exit(ce_position, ce_entry, ce_spread)
                if reason:
                    alert(f"CE EXIT ({reason}) | P/L: {pnl:.2f} pts", "EXIT")
                    alert(f"ACTION: CLOSE CE LEGS", "INFO")
                    ce_position = None
                    ce_entry = None

        # ========== PE ALERTS ==========
        if pe_spread is not None:
            if pe_spread != last_pe_spread:
                print(f"[{now}] PE {active_strike} | Spread: {pe_spread:.2f}")
                last_pe_spread = pe_spread

            if pe_position is None:
                sig = get_signal(pe_spread)
                if sig == "LONG":
                    pe_position = "LONG"
                    pe_entry = pe_spread
                    alert(f"PE LONG ENTRY @ {pe_spread:.2f}", "ENTRY")
                    alert(f"ACTION: BUY FAR PE + SELL NEAR PE", "INFO")

                elif sig == "SHORT":
                    pe_position = "SHORT"
                    pe_entry = pe_spread
                    alert(f"PE SHORT ENTRY @ {pe_spread:.2f}", "ENTRY")
                    alert(f"ACTION: SELL FAR PE + BUY NEAR PE", "INFO")

            else:
                reason, pnl = check_exit(pe_position, pe_entry, pe_spread)
                if reason:
                    alert(f"PE EXIT ({reason}) | P/L: {pnl:.2f} pts", "EXIT")
                    alert(f"ACTION: CLOSE PE LEGS", "INFO")
                    pe_position = None
                    pe_entry = None

        time.sleep(REFRESH)

    except KeyboardInterrupt:
        print("\n\nAlgo stopped.")
        break

    except Exception as e:
        print(f"Error: {e}")
        time.sleep(REFRESH)
