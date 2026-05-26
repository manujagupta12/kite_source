"""
DIAGNOSTIC SCRIPT — Run this ONCE to map your exact Excel structure.
Paste the output here so the algo can be fixed precisely.
"""
import shutil
import pandas as pd

FILEPATH = r"C:\AlgoTrading\data\multitrade_feed.xls"
TEMPPATH = r"C:\AlgoTrading\data\_temp_read.xls"

print("Reading Excel...")
shutil.copy2(FILEPATH, TEMPPATH)
raw = pd.read_excel(TEMPPATH, header=None, engine="xlrd")

print(f"\nTotal rows: {len(raw)}  |  Total columns: {len(raw.columns)}")

print("\n--- RAW ROWS 0–12 (full) ---")
for i in range(min(13, len(raw))):
    vals = raw.iloc[i].tolist()
    non_empty = [(j, v) for j, v in enumerate(vals) if str(v).strip() not in ["nan", "", "None"]]
    print(f"Row {i:2d}: {non_empty}")

print("\n--- SEARCHING FOR HEADER ROW (BID + ASK) ---")
for i in range(min(30, len(raw))):
    vals = [str(x).strip().upper() for x in raw.iloc[i].tolist()]
    if "BID" in vals and "ASK" in vals:
        print(f"Found at Row {i}: {raw.iloc[i].tolist()}")
        print(f"\nColumn index of BID : {vals.index('BID')}")
        print(f"Column index of ASK : {vals.index('ASK')}")
        print(f"All non-empty in this row:")
        for j, v in enumerate(raw.iloc[i].tolist()):
            if str(v).strip() not in ["nan", "", "None"]:
                print(f"  col[{j}] = '{v}'")
        break

print("\n--- FIRST 5 DATA ROWS AFTER HEADER ---")
header_idx = None
for i in range(min(30, len(raw))):
    vals = [str(x).strip().upper() for x in raw.iloc[i].tolist()]
    if "BID" in vals and "ASK" in vals:
        header_idx = i
        break

if header_idx is not None:
    headers = raw.iloc[header_idx].fillna("").tolist()
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = headers
    df = df.dropna(axis=0, how="all")
    print(f"\nHeaders as-is: {headers}")
    print(f"\nFirst 5 data rows:")
    print(df.head(5).to_string())
else:
    print("No BID/ASK header row found!")

print("\n--- DONE. Paste this entire output for the fix. ---")