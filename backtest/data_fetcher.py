"""
backtest/data_fetcher.py
========================
Downloads real NSE F&O Bhavcopy CSV files from NSE archives.
No mocks. No random digits. Real data only.

Usage:
    python data_fetcher.py --from 2024-01-01 --to 2024-12-31 --out ./data
"""

import os
import time
import zipfile
import io
import argparse
import logging
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

NSE_ARCHIVE_BASE = "https://nsearchives.nseindia.com/content/fo"
NSE_HOME = "https://www.nseindia.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def _trading_days(start: date, end: date):
    """Yield weekdays between start and end inclusive."""
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon–Fri
            yield d
        d += timedelta(days=1)


def _session() -> requests.Session:
    """Create a warm NSE session (cookie handshake required)."""
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(NSE_HOME, timeout=15)
        time.sleep(1)
    except Exception as e:
        log.warning("NSE handshake failed: %s (proceeding anyway)", e)
    return s


def fetch_bhavcopy(dt: date, session: requests.Session, out_dir: Path) -> pd.DataFrame | None:
    """
    Download and parse NSE F&O Bhavcopy for a single date.
    Returns DataFrame or None if market was closed / file missing.

    Columns returned (standardised):
        SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP,
        OPEN, HIGH, LOW, CLOSE, SETTLE_PR,
        CONTRACTS, VAL_INLAKH, OPEN_INT, CHG_IN_OI, DATE
    """
    fname = f"BhavCopy_NSE_FO_0_0_0_{dt.strftime('%Y%m%d')}_F_0000.csv.zip"
    url = f"{NSE_ARCHIVE_BASE}/{fname}"
    cache_path = out_dir / f"fo_{dt.strftime('%Y%m%d')}.csv"

    # Return cached file if already downloaded
    if cache_path.exists():
        log.debug("Cache hit: %s", cache_path)
        return pd.read_csv(cache_path, parse_dates=["EXPIRY_DT", "DATE"])

    try:
        resp = session.get(url, timeout=20)
        if resp.status_code == 404:
            log.debug("No data for %s (holiday/weekend)", dt)
            return None
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("Failed to fetch %s: %s", dt, e)
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            csv_name = z.namelist()[0]
            df = pd.read_csv(z.open(csv_name))
    except Exception as e:
        log.warning("Failed to parse zip for %s: %s", dt, e)
        return None

    # Normalise column names (NSE changes header names occasionally)
    df.columns = [c.strip().upper() for c in df.columns]

    # Add trade date
    df["DATE"] = pd.to_datetime(dt)

    # Parse expiry
    if "EXPIRY_DT" in df.columns:
        df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"], errors="coerce")

    df.to_csv(cache_path, index=False)
    log.info("Saved %s (%d rows)", cache_path.name, len(df))
    return df


def load_range(start: date, end: date, out_dir: Path) -> pd.DataFrame:
    """
    Download NSE F&O Bhavcopy for every trading day in [start, end].
    Returns combined DataFrame sorted by DATE.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    session = _session()
    frames = []

    days = list(_trading_days(start, end))
    log.info("Fetching %d potential trading days (%s to %s)", len(days), start, end)

    for i, d in enumerate(days, 1):
        df = fetch_bhavcopy(d, session, out_dir)
        if df is not None:
            frames.append(df)
        if i % 10 == 0:
            time.sleep(0.5)  # polite rate limiting

    if not frames:
        raise RuntimeError("No Bhavcopy data downloaded. Check dates and internet connection.")

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values("DATE", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    log.info("Total rows loaded: %d across %d trading days", len(combined), len(frames))
    return combined


def filter_nifty_options(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NIFTY index options (CE and PE)."""
    mask = (
        df["SYMBOL"].str.strip().str.upper().isin(["NIFTY", "BANKNIFTY"])
        & df["OPTION_TYP"].str.strip().str.upper().isin(["CE", "PE"])
    )
    return df[mask].copy()


def filter_stock_futures(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only stock futures (instrument type FUT)."""
    if "INSTRUMENT" in df.columns:
        return df[df["INSTRUMENT"].str.strip().str.upper() == "FUTSTK"].copy()
    return pd.DataFrame()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download NSE F&O Bhavcopy")
    parser.add_argument("--from", dest="start", default="2024-01-01")
    parser.add_argument("--to",   dest="end",   default="2024-12-31")
    parser.add_argument("--out",  dest="out",   default="./data/bhavcopy")
    args = parser.parse_args()

    df = load_range(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        Path(args.out),
    )
    print(df.head())
    print(f"\nLoaded {len(df)} rows from {df['DATE'].min().date()} to {df['DATE'].max().date()}")
