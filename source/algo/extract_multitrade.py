import pandas as pd

file = r"C:\AlgoTrading\data\multitrade_feed.xls"

# Load raw with NO header
raw = pd.read_excel(file, header=None)

# Actual header row is at row 9
header_row = 9
headers = raw.iloc[header_row].fillna("").tolist()

# Data starts from row 10
df = raw.iloc[header_row+1:].copy()
df.columns = headers

# Drop fully empty columns
df = df.dropna(axis=1, how='all')

# Drop fully empty rows
df = df.dropna(axis=0, how='all')

# Rename first four columns properly
colnames = df.columns.tolist()

# First 4 columns = [Type, Strike1, Strike2, Strike3]
colnames[0] = "Type"
colnames[1] = "Strike"
colnames[2] = "Strike2"
colnames[3] = "Strike3"

# Apply corrected names
df.columns = colnames

# Convert strike to number
df["Strike"] = pd.to_numeric(df["Strike"], errors='coerce')

# Keep only rows where Type is CE or PE
df = df[df["Type"].isin(["CE", "PE"])]

print("\nCLEANED DATA:")
print(df.head(10))

# Separate NEAR/FAR expiry by MultiTrade pattern
df_near = df.iloc[0::4].reset_index(drop=True)
df_far  = df.iloc[2::4].reset_index(drop=True)

print("\nNEAR:")
print(df_near.head())

print("\nFAR:")
print(df_far.head())
