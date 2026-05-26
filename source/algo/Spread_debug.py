"""
SPREAD DEBUG — Run once to see exactly what the algo sees.
Paste the output so the final fix can be applied.
"""
import shutil
import pandas as pd
import numpy as np

FILEPATH = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH = r"C:\AlgoTrading\data\_temp_read.xls"

shutil.copy2(FILEPATH, TEMPPATH)
raw = pd.read_excel(TEMPPATH, header=None, engine="xlrd")

print(f"Shape: {raw.shape}")

# ── MAIN TABLE (CE near, rows after row 2) ──────────────────────────
print("\n=== MAIN TABLE (near CE) — col 0=Near Strike, col 3=BID, col 4=ASK ===")
header_row = 2
data = raw.iloc[header_row + 1:].copy()
near_rows = []
for idx, row in data.iterrows():
    try:
        s = float(row.iloc[0])
        if 21000 < s < 27000:
            near_rows.append({
                "raw_idx":    idx,
                "near_strike": int(s),
                "far_strike":  int(float(row.iloc[1])),
                "near_bid":   float(row.iloc[3]),
                "near_ask":   float(row.iloc[4]),
                "near_ltp":   float(row.iloc[5]),
            })
    except Exception:
        pass

print(f"Near CE rows found: {len(near_rows)}")
for r in near_rows[:8]:
    print(f"  {r}")

# ── SECONDARY TABLE (far CE/PE, cols 29-34) ─────────────────────────
print("\n=== SECONDARY TABLE (far CE/PE) — col 29=Type, 30=Strike, 31=BID ===")
far_ce, far_pe = [], []
for idx, row in raw.iterrows():
    try:
        t = str(row.iloc[29]).strip().upper()
        if t not in ("CE", "PE"):
            continue
        s = float(row.iloc[30])
        if not (21000 < s < 27000):
            continue
        entry = {
            "raw_idx": idx,
            "type":    t,
            "strike":  int(s),
            "bid":     float(row.iloc[31]),
            "ask":     float(row.iloc[32]),
        }
        if t == "CE":
            far_ce.append(entry)
        else:
            far_pe.append(entry)
    except Exception:
        pass

print(f"Far CE rows: {len(far_ce)}  |  Far PE rows: {len(far_pe)}")
for r in far_ce[:5]:
    print(f"  CE {r}")
for r in far_pe[:5]:
    print(f"  PE {r}")

# ── SPREAD ATTEMPT at ATM 23800 ──────────────────────────────────────
print("\n=== SPREAD CALCULATION ATTEMPT at ATM=23800 ===")
atm = 23800
near_match = [r for r in near_rows if r["near_strike"] == atm]
far_match_ce = [r for r in far_ce if r["strike"] == atm]

print(f"Near CE match for {atm}: {near_match}")
print(f"Far  CE match for {atm}: {far_match_ce}")

if near_match and far_match_ce:
    spread = round(far_match_ce[0]["bid"] - near_match[0]["near_ask"], 2)
    print(f"CE Spread = Far BID {far_match_ce[0]['bid']} - Near ASK {near_match[0]['near_ask']} = {spread}")
else:
    # Show available strikes in each table to find the mismatch
    near_strikes = sorted(set(r["near_strike"] for r in near_rows))
    far_strikes_ce = sorted(set(r["strike"] for r in far_ce))
    far_strikes_pe = sorted(set(r["strike"] for r in far_pe))
    print(f"MISMATCH — Near strikes : {near_strikes}")
    print(f"MISMATCH — Far CE strikes: {far_strikes_ce}")
    print(f"MISMATCH — Far PE strikes: {far_strikes_pe}")

    # Also check far_strike column in main table
    far_col_strikes = sorted(set(r["far_strike"] for r in near_rows))
    print(f"Far strike col in main table: {far_col_strikes}")

# ── FULL RAW ROWS 8-15 to see secondary table area ───────────────────
print("\n=== RAW ROWS 8–15 (secondary table area) ===")
for i in range(8, min(16, len(raw))):
    non_empty = [(j, raw.iloc[i, j]) for j in range(len(raw.columns))
                 if str(raw.iloc[i, j]).strip() not in ["nan", "", "None", "NaT"]]
    print(f"Row {i:2d}: {non_empty}")

# ── VIX SCAN ──────────────────────────────────────────────────────────
print("\n=== VIX SCAN (all rows) ===")
for i in range(len(raw)):
    for j in range(len(raw.columns)):
        cell = str(raw.iloc[i, j]).upper()
        if "VIX" in cell:
            print(f"  Row {i}, Col {j}: '{raw.iloc[i,j]}'  "
                  f"| neighbours: {[raw.iloc[i, k] for k in range(max(0,j-2), min(len(raw.columns), j+3))]}")

print("\n--- DONE ---")