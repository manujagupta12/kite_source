import pandas as pd

df = pd.read_excel(r"C:\AlgoTrading\data\multitrade_feed.xls", header=None)
print(df.head(10))
