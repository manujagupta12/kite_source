import pandas as pd
import time

# ========== CONFIGURATION ==========
FILE_PATH = r"C:\AlgoTrading\data\multitrade_feed.xls"
STRIKE = 58000          # Choose your strike
THRESHOLD = 3           # Entry threshold (points)
TARGET = 4              # Profit target (points)
STOPLOSS = 3            # Stop loss (points)
REFRESH = 2             # Seconds between checks

# ========== POSITION STATE ==========
position = None
entry_price = None


# ========== DATA EXTRACTION ==========
def load_data():
    raw = pd.read_excel(FILE_PATH, header=None)
    
    # Row 9 = headers, data starts row 10
    headers = raw.iloc[9].fillna("").tolist()
    df = raw.iloc[10:].copy()
    df.columns = headers
    
    # Drop empty columns and rows
    df = df.dropna(axis=1, how='all')
    df = df.dropna(axis=0, how='all')
    
    # Rename first 4 columns
    colnames = df.columns.tolist()
    colnames[0] = "Type"
    colnames[1] = "Strike"
    colnames[2] = "Strike2"
    colnames[3] = "Strike3"
    df.columns = colnames
    
    # Convert strike to numeric
    df["Strike"] = pd.to_numeric(df["Strike"], errors='coerce')
    
    # Keep only CE/PE rows
    df = df[df["Type"].isin(["CE", "PE"])].reset_index(drop=True)
    
    # Split NEAR and FAR (MultiTrade pattern: every 4th row)
    df_near = df.iloc[0::4].reset_index(drop=True)
    df_far = df.iloc[2::4].reset_index(drop=True)
    
    return df_near, df_far


# ========== SPREAD CALCULATION ==========
def calculate_spread(df_near, df_far, strike):
    near_row = df_near[df_near["Strike"] == strike]
    far_row = df_far[df_far["Strike"] == strike]
    
    if near_row.empty or far_row.empty:
        return None, None, None
    
    near_ask = float(near_row["ASK"].iloc[0])
    far_bid = float(far_row["BID"].iloc[0])
    
    spread = far_bid - near_ask
    
    # Fair value using theta difference
    near_theta = float(near_row["TETHA EROD 1ST"].iloc[0])
    far_theta = float(far_row["TETHA EROD 1ST"].iloc[0])
    
    theta_diff = far_theta - near_theta
    fair_value = theta_diff * 0.5
    
    return spread, fair_value, near_ask


# ========== SIGNAL LOGIC ==========
def get_signal(spread, fair_value):
    if spread < fair_value - THRESHOLD:
        return "LONG_CALENDAR"
    elif spread > fair_value + THRESHOLD:
        return "SHORT_CALENDAR"
    return "NO_TRADE"


# ========== EXIT LOGIC ==========
def check_exit(current_spread):
    global position, entry_price
    
    if position == "LONG_CALENDAR":
        pnl = current_spread - entry_price
        if pnl >= TARGET:
            return "EXIT", "TARGET HIT", pnl
        if pnl <= -STOPLOSS:
            return "EXIT", "STOPLOSS HIT", pnl
    
    if position == "SHORT_CALENDAR":
        pnl = entry_price - current_spread
        if pnl >= TARGET:
            return "EXIT", "TARGET HIT", pnl
        if pnl <= -STOPLOSS:
            return "EXIT", "STOPLOSS HIT", pnl
    
    return None, None, None


# ========== MAIN LOOP ==========
print("\n" + "="*50)
print("  CALENDAR SPREAD ALGO — MODEL B (SEMI-AUTO)")
print("  Strike:", STRIKE)
print("  Target:", TARGET, "pts | SL:", STOPLOSS, "pts")
print("="*50 + "\n")

while True:
    try:
        df_near, df_far = load_data()
        spread, fair_value, near_ask = calculate_spread(df_near, df_far, STRIKE)
        
        if spread is None:
            print("Waiting for data at strike", STRIKE)
            time.sleep(REFRESH)
            continue
        
        # ===== NO POSITION — CHECK FOR ENTRY =====
        if position is None:
            signal = get_signal(spread, fair_value)
            
            if signal == "LONG_CALENDAR":
                position = "LONG_CALENDAR"
                entry_price = spread
                print("\n" + "-"*40)
                print("🟢 ENTRY SIGNAL: LONG CALENDAR SPREAD")
                print(f"   Strike: {STRIKE}")
                print(f"   Spread: {spread:.2f}")
                print(f"   Fair Value: {fair_value:.2f}")
                print("   ACTION: BUY FAR CE + SELL NEAR CE")
                print("-"*40)
            
            elif signal == "SHORT_CALENDAR":
                position = "SHORT_CALENDAR"
                entry_price = spread
                print("\n" + "-"*40)
                print("🔴 ENTRY SIGNAL: SHORT CALENDAR SPREAD")
                print(f"   Strike: {STRIKE}")
                print(f"   Spread: {spread:.2f}")
                print(f"   Fair Value: {fair_value:.2f}")
                print("   ACTION: SELL FAR CE + BUY NEAR CE")
                print("-"*40)
            
            else:
                print(f"[{time.strftime('%H:%M:%S')}] Spread: {spread:.2f} | Fair: {fair_value:.2f} | NO TRADE")
        
        # ===== IN POSITION — CHECK FOR EXIT =====
        else:
            exit_signal, reason, pnl = check_exit(spread)
            
            if exit_signal == "EXIT":
                print("\n" + "="*40)
                print(f"⚪ EXIT SIGNAL: {reason}")
                print(f"   Position: {position}")
                print(f"   Entry: {entry_price:.2f}")
                print(f"   Current: {spread:.2f}")
                print(f"   P/L: {pnl:.2f} points")
                print("   ACTION: CLOSE BOTH LEGS")
                print("="*40 + "\n")
                position = None
                entry_price = None
            else:
                pnl = spread - entry_price if position == "LONG_CALENDAR" else entry_price - spread
                print(f"[{time.strftime('%H:%M:%S')}] IN {position} | Entry: {entry_price:.2f} | Now: {spread:.2f} | P/L: {pnl:.2f}")
        
        time.sleep(REFRESH)
    
    except Exception as e:
        print("Error:", e)
        time.sleep(REFRESH)
