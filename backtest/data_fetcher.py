"""
backtest/data_fetcher.py
========================
Downloads real NSE Bhavcopy data — F&O and Equity segments.
Handles BOTH old (pre-2023) and new (2023+) NSE URL formats automatically.
Filters to NIFTY / BANKNIFTY / FINNIFTY / top equity symbols.
No mocks. No random data.
"""

import io
import argparse
import logging
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── NSE URL patterns ──────────────────────────────────────────────────────────
# F&O — new format (2023-01-01 onwards)
FO_NEW = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip"
# F&O — old format (before 2023)
FO_OLD = "https://archives.nseindia.com/archives/fo/bhav/fo{DDMMYYYY}bhav.csv.zip"
# Equity Bhavcopy
EQ_URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv"
# Equity alt URL
EQ_ALT = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv"

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

# ── Instruments to track ──────────────────────────────────────────────────────
FO_INDEX_SYMBOLS  = {"NIFTY", "BANKNIFTY", "FINNIFTY"}

# Top 30 NIFTY50 stocks for equity backtest (high liquidity)
EQUITY_SYMBOLS = {
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "KOTAKBANK", "LT", "SBIN", "BAJFINANCE", "BHARTIARTL",
    "HCLTECH", "WIPRO", "ASIANPAINT", "TITAN", "ULTRACEMCO",
    "AXISBANK", "MARUTI", "SUNPHARMA", "TATAMOTORS", "POWERGRID",
    "NTPC", "HINDALCO", "JSWSTEEL", "TATASTEEL", "TECHM",
    "BPCL", "COALINDIA", "DIVISLAB", "DRREDDY", "CIPLA",
}

# ── Column normalisation maps ─────────────────────────────────────────────────
# F&O new format → standard
_FO_NEW_MAP = {
    "TCKRSYMB": "SYMBOL",   "XPRYDT": "EXPIRY_DT",
    "STRKPRIC": "STRIKE_PR","OPTNTP": "OPTION_TYP",
    "OPNPRIC": "OPEN",      "HGHPRIC": "HIGH",
    "LWPRIC": "LOW",        "CLSPRIC": "CLOSE",
    "STTLMPRIC": "SETTLE_PR","TTLTRADGVOL": "CONTRACTS",
    "OPNINTRST": "OPEN_INT","CHGINNOPNINTRST": "CHG_IN_OI",
    "FININSTRMNTP": "INSTRUMENT", "FININSTRMTP": "INSTRUMENT",
}
# F&O old format already has standard names
_FO_OLD_MAP = {
    "SYMBOL": "SYMBOL", "EXPIRY_DT": "EXPIRY_DT",
    "STRIKE_PR": "STRIKE_PR", "OPTION_TYP": "OPTION_TYP",
    "OPEN": "OPEN", "HIGH": "HIGH", "LOW": "LOW",
    "CLOSE": "CLOSE", "SETTLE_PR": "SETTLE_PR",
    "CONTRACTS": "CONTRACTS", "OPEN_INT": "OPEN_INT",
    "CHG_IN_OI": "CHG_IN_OI", "INSTRUMENT": "INSTRUMENT",
}
# Equity Bhavcopy columns
_EQ_MAP = {
    "SYMBOL": "SYMBOL", "SERIES": "SERIES",
    "OPEN": "OPEN", "HIGH": "HIGH",
    "LOW": "LOW",   "CLOSE": "CLOSE",
    "LAST": "LAST", "PREVCLOSE": "PREV_CLOSE",
    "TOTTRDQTY": "VOLUME", "TOTTRDVAL": "TURNOVER",
}

_FO_KEEP = {"SYMBOL","EXPIRY_DT","STRIKE_PR","OPTION_TYP",
            "OPEN","HIGH","LOW","CLOSE","SETTLE_PR",
            "OPEN_INT","CHG_IN_OI","DATE"}

_EQ_KEEP = {"SYMBOL","SERIES","OPEN","HIGH","LOW","CLOSE","VOLUME","DATE"}

_NUMERIC_FO  = ["STRIKE_PR","OPEN","HIGH","LOW","CLOSE","SETTLE_PR","OPEN_INT","CHG_IN_OI"]
_NUMERIC_EQ  = ["OPEN","HIGH","LOW","CLOSE","VOLUME"]

# ── Lot sizes (as of 2024 — NSE revises periodically) ────────────────────────
LOT_SIZES = {
    "NIFTY":     50,
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
}
EQUITY_LOT = 1  # shares (position sizing done via capital)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(NSE_HOME, timeout=15)
        time.sleep(0.5)
    except Exception as e:
        log.warning("NSE handshake failed: %s", e)
    return s


def _trading_days(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _get(session: requests.Session, url: str, timeout: int = 25):
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 200 and len(r.content) > 100:
            return r
        return None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# F&O Bhavcopy
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_fo(raw: pd.DataFrame, dt: date, fmt: str) -> pd.DataFrame:
    """Normalise F&O DataFrame columns, filter to index options only."""
    raw.columns = [c.strip().upper() for c in raw.columns]
    col_map = _FO_NEW_MAP if fmt == "new" else _FO_OLD_MAP
    raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns}, inplace=True)

    # Guess SYMBOL column if still missing
    if "SYMBOL" not in raw.columns:
        cands = [c for c in raw.columns if "SYMB" in c or "TICK" in c]
        if cands:
            raw.rename(columns={cands[0]: "SYMBOL"}, inplace=True)

    if "SYMBOL" not in raw.columns:
        log.error("F&O: SYMBOL column not found. Columns: %s", raw.columns.tolist())
        return pd.DataFrame()

    # Filter to index options only
    raw = raw[raw["SYMBOL"].str.strip().str.upper().isin(FO_INDEX_SYMBOLS)].copy()

    # Keep only needed columns
    raw = raw[[c for c in raw.columns if c in _FO_KEEP]].copy()

    for col in _NUMERIC_FO:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw["DATE"] = pd.to_datetime(dt)
    if "EXPIRY_DT" in raw.columns:
        raw["EXPIRY_DT"] = pd.to_datetime(raw["EXPIRY_DT"], errors="coerce")

    return raw


def fetch_fo_bhavcopy(dt: date, session: requests.Session, out_dir: Path):
    cache = out_dir / f"fo_{dt.strftime('%Y%m%d')}.csv"

    if cache.exists():
        df = pd.read_csv(cache, dtype=str)
        if "SYMBOL" in df.columns:
            df = df[df["SYMBOL"].str.strip().str.upper().isin(FO_INDEX_SYMBOLS)].copy()
        df = df[[c for c in df.columns if c in _FO_KEEP]].copy()
        for col in _NUMERIC_FO:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "DATE" in df.columns:
            df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        if "EXPIRY_DT" in df.columns:
            df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"], errors="coerce")
        return df if not df.empty else None

    # Try new format first (2023+), then old format
    urls = []
    if dt >= date(2023, 1, 1):
        urls.append((FO_NEW.replace("{YYYYMMDD}", dt.strftime("%Y%m%d")), "new"))
    # Always try old format as fallback
    urls.append((FO_OLD.replace("{DDMMYYYY}", dt.strftime("%d%m%Y")), "old"))

    for url, fmt in urls:
        resp = _get(session, url)
        if resp is None:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                raw = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
        except Exception as e:
            log.debug("F&O zip parse failed (%s): %s", fmt, e)
            continue

        df = _normalise_fo(raw, dt, fmt)
        if df.empty:
            continue

        df.to_csv(cache, index=False)
        log.info("F&O %s: %d rows [%s]", dt, len(df), fmt)
        return df

    log.debug("F&O %s: no data (holiday/weekend)", dt)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Equity Bhavcopy
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_eq(raw: pd.DataFrame, dt: date) -> pd.DataFrame:
    raw.columns = [c.strip().upper() for c in raw.columns]
    raw.rename(columns={k: v for k, v in _EQ_MAP.items() if k in raw.columns}, inplace=True)

    if "SYMBOL" not in raw.columns:
        return pd.DataFrame()

    # Keep only EQ series (not BE, BL, etc.) and target symbols
    if "SERIES" in raw.columns:
        raw = raw[raw["SERIES"].str.strip().str.upper() == "EQ"].copy()

    raw = raw[raw["SYMBOL"].str.strip().str.upper().isin(EQUITY_SYMBOLS)].copy()
    raw = raw[[c for c in raw.columns if c in _EQ_KEEP]].copy()

    for col in _NUMERIC_EQ:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")

    raw["DATE"] = pd.to_datetime(dt)
    return raw


def fetch_eq_bhavcopy(dt: date, session: requests.Session, out_dir: Path):
    cache = out_dir / f"eq_{dt.strftime('%Y%m%d')}.csv"

    if cache.exists():
        df = pd.read_csv(cache, dtype=str)
        if "DATE" in df.columns:
            df["DATE"] = pd.to_datetime(df["DATE"], errors="coerce")
        for col in _NUMERIC_EQ:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df if not df.empty else None

    ddmmyyyy = dt.strftime("%d%m%Y")
    for url in [EQ_URL.replace("{DDMMYYYY}", ddmmyyyy),
                EQ_ALT.replace("{DDMMYYYY}", ddmmyyyy)]:
        resp = _get(session, url)
        if resp is None:
            continue
        try:
            # Equity Bhavcopy is a plain CSV (not zipped)
            raw = pd.read_csv(io.StringIO(resp.text), dtype=str)
        except Exception:
            continue

        df = _normalise_eq(raw, dt)
        if df.empty:
            continue

        df.to_csv(cache, index=False)
        log.info("EQ  %s: %d rows", dt, len(df))
        return df

    log.debug("EQ  %s: no data", dt)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Load range
# ─────────────────────────────────────────────────────────────────────────────

def load_range(
    start: date,
    end: date,
    out_dir: Path,
    include_equity: bool = True,
) -> tuple:
    """
    Download NSE F&O + Equity Bhavcopy for every trading day in [start, end].
    Returns (df_fo, df_equity).
    """
    (out_dir / "fo").mkdir(parents=True, exist_ok=True)
    (out_dir / "eq").mkdir(parents=True, exist_ok=True)

    session  = _session()
    fo_frames, eq_frames = [], []
    days = list(_trading_days(start, end))

    log.info("Loading %d trading days: %s → %s", len(days), start, end)

    for i, d in enumerate(days, 1):
        fo = fetch_fo_bhavcopy(d, session, out_dir / "fo")
        if fo is not None and not fo.empty:
            fo_frames.append(fo)

        if include_equity:
            eq = fetch_eq_bhavcopy(d, session, out_dir / "eq")
            if eq is not None and not eq.empty:
                eq_frames.append(eq)

        if i % 20 == 0:
            log.info("Progress: %d/%d days", i, len(days))
            time.sleep(0.3)

    if not fo_frames:
        raise RuntimeError("No F&O data downloaded. Check dates and internet connection.")

    df_fo = pd.concat(fo_frames, ignore_index=True)
    df_fo.sort_values("DATE", inplace=True)
    df_fo.reset_index(drop=True, inplace=True)

    df_eq = pd.DataFrame()
    if eq_frames:
        df_eq = pd.concat(eq_frames, ignore_index=True)
        df_eq.sort_values("DATE", inplace=True)
        df_eq.reset_index(drop=True, inplace=True)

    log.info("F&O rows: %d | Equity rows: %d", len(df_fo), len(df_eq))
    log.info("F&O symbols: %s", df_fo["SYMBOL"].unique().tolist() if not df_fo.empty else [])
    log.info("EQ symbols:  %d unique", df_eq["SYMBOL"].nunique() if not df_eq.empty else 0)

    return df_fo, df_eq


def filter_options(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only CE and PE options."""
    if "OPTION_TYP" not in df.columns:
        return df
    return df[df["OPTION_TYP"].str.strip().str.upper().isin(["CE", "PE"])].copy()

# Backwards-compatible alias (older run_backtest.py used this name)
filter_nifty_options = filter_options


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", default="2020-01-01")
    parser.add_argument("--to",   dest="end",   default="2024-12-31")
    parser.add_argument("--out",  dest="out",   default="./data/bhavcopy")
    args = parser.parse_args()

    df_fo, df_eq = load_range(
        date.fromisoformat(args.start),
        date.fromisoformat(args.end),
        Path(args.out),
    )
    print(f"F&O: {len(df_fo)} rows | Equity: {len(df_eq)} rows")
    print(f"Columns F&O: {df_fo.columns.tolist()}")
