"""
bhavcopy_loader.py
==================
NSE F&O Bhavcopy historical data loader.
Completely FREE — official NSE public archives.

What it provides:
  - EOD OHLC + OI for ALL F&O instruments (futures + options)
  - 10+ years of data available on NSE archives
  - Downloaded as ZIP/CSV, stored locally as Parquet for fast querying

Limitation:
  - EOD only (no intraday tick history)
  - Options: data keyed by (symbol, expiry, strike, option_type)
  - Futures: continuous data available

Usage:
    from algo.bhavcopy_loader import BhavcopyCatalog

    catalog = BhavcopyCatalog()                          # init (auto-creates cache dir)
    catalog.download_range("2021-01-01", "2026-05-29")   # one-time 5yr download (~20 min)

    df = catalog.load_options("BANKNIFTY", "2026-06-05")  # options for one expiry
    df = catalog.load_futures("BANKNIFTY",                # continuous futures
                               start="2024-01-01",
                               end="2026-05-29")
"""

import os
import io
import time
import zipfile
import requests
import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── NSE archive URL patterns ──────────────────────────────────
# Daily F&O bhavcopy: NSE provides as ZIP containing CSV
_BHAV_URL_NEW = (
    "https://nsearchives.nseindia.com/content/fo/"
    "BFO_DDMMYYYY.zip"  # format used since ~2019
)
_BHAV_URL_OLD = (
    "https://www1.nseindia.com/content/historical/DERIVATIVES/"
    "{year}/{month}/fo{date}bhav.csv.zip"
)

_HEADERS = {
    "User-Agent":  "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":     "https://www.nseindia.com",
    "Accept":      "*/*",
}

# ── Cache configuration ───────────────────────────────────────
_DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "bhavcopy_cache"
)


class BhavcopyCatalog:
    """
    Downloads, caches, and queries NSE F&O Bhavcopy data.
    Each trading day is stored as one Parquet file.
    """

    def __init__(self, cache_dir: str = _DEFAULT_CACHE):
        self.cache_dir = Path(cache_dir).resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)
        print(f"[Bhavcopy] Cache directory: {self.cache_dir}")

    # ── Download ──────────────────────────────────────────────

    def download_range(
        self,
        start: str,
        end:   str,
        skip_existing: bool = True,
        delay_sec: float = 1.0,
    ) -> dict:
        """
        Download F&O bhavcopy for a date range.
        Skips weekends and already-cached dates automatically.

        Args:
            start:         'YYYY-MM-DD'
            end:           'YYYY-MM-DD'
            skip_existing: skip dates already in cache (default True)
            delay_sec:     sleep between requests to avoid NSE rate-limit

        Returns:
            {'downloaded': N, 'skipped': N, 'failed': N}
        """
        start_dt = date.fromisoformat(start)
        end_dt   = date.fromisoformat(end)
        stats    = {"downloaded": 0, "skipped": 0, "failed": 0}

        current = start_dt
        while current <= end_dt:
            if current.weekday() >= 5:   # skip weekends
                current += timedelta(days=1)
                continue

            parquet_path = self._parquet_path(current)

            if skip_existing and parquet_path.exists():
                stats["skipped"] += 1
                current += timedelta(days=1)
                continue

            df = self._download_one(current)
            if df is not None and not df.empty:
                df.to_parquet(parquet_path, index=False)
                stats["downloaded"] += 1
                print(f"  [Bhavcopy] {current} — {len(df)} instruments")
            else:
                # Holiday or unavailable — mark as empty file so we don't retry
                parquet_path.touch()
                stats["failed"] += 1

            current += timedelta(days=1)
            time.sleep(delay_sec)

        print(f"\n[Bhavcopy] Done: {stats}")
        return stats

    def _download_one(self, trade_date: date) -> Optional[pd.DataFrame]:
        """
        Download bhavcopy for a single date. Tries both URL formats.
        Returns DataFrame or None on failure / holiday.
        """
        dd   = trade_date.strftime("%d")
        mm   = trade_date.strftime("%m")
        yyyy = trade_date.strftime("%Y")
        mon  = trade_date.strftime("%b").upper()

        # Primary URL (post-2019 format)
        url1 = (
            "https://nsearchives.nseindia.com/content/fo/"
            f"BFO_{dd}{mm}{yyyy}.zip"
        )
        # Fallback URL (pre-2019 format)
        url2 = (
            "https://www1.nseindia.com/content/historical/DERIVATIVES/"
            f"{yyyy}/{mon}/fo{dd}{mon}{yyyy}bhav.csv.zip"
        )

        for url in (url1, url2):
            try:
                r = self._session.get(url, timeout=20)
                if r.status_code == 404:
                    continue  # Try next URL or it's a holiday
                r.raise_for_status()
                return self._parse_bhav_zip(r.content, trade_date)
            except requests.exceptions.Timeout:
                print(f"  [Bhavcopy] Timeout: {trade_date}")
            except Exception as e:
                # Silently skip — holidays return errors
                pass
        return None

    def _parse_bhav_zip(self, content: bytes, trade_date: date) -> Optional[pd.DataFrame]:
        """Extract and parse CSV from bhavcopy ZIP."""
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                csv_name = [n for n in zf.namelist() if n.endswith(".csv")]
                if not csv_name:
                    return None
                with zf.open(csv_name[0]) as f:
                    df = pd.read_csv(f)

            # Normalise column names (NSE changes them occasionally)
            df.columns = df.columns.str.strip().str.upper()

            # Standard F&O bhavcopy columns:
            # INSTRUMENT, SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP,
            # OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, VAL_INLAKH,
            # OPEN_INT, CHG_IN_OI, TIMESTAMP

            # Add trade date
            df["TRADE_DATE"] = trade_date.isoformat()

            # Clean up
            df = df[df["SYMBOL"].notna()].copy()
            if "EXPIRY_DT" in df.columns:
                df["EXPIRY_DT"] = pd.to_datetime(df["EXPIRY_DT"], errors="coerce").dt.strftime("%Y-%m-%d")
            if "STRIKE_PR" in df.columns:
                df["STRIKE_PR"] = pd.to_numeric(df["STRIKE_PR"], errors="coerce")

            return df

        except zipfile.BadZipFile:
            return None
        except Exception as e:
            print(f"  [Bhavcopy] Parse error {trade_date}: {e}")
            return None

    # ── Query ─────────────────────────────────────────────────

    def load_options(
        self,
        symbol:      str,
        expiry:      Optional[str] = None,
        start:       Optional[str] = None,
        end:         Optional[str] = None,
        option_type: Optional[str] = None,   # 'CE' or 'PE'
        strike:      Optional[float] = None,
    ) -> pd.DataFrame:
        """
        Load historical options data from cache.

        Args:
            symbol:      'BANKNIFTY', 'NIFTY', etc.
            expiry:      filter by expiry date 'YYYY-MM-DD' (optional)
            start/end:   trade date range (optional — defaults to all cached)
            option_type: 'CE' or 'PE' (optional)
            strike:      specific strike (optional)

        Returns:
            DataFrame with columns: TRADE_DATE, EXPIRY_DT, STRIKE_PR,
            OPTION_TYP, OPEN, HIGH, LOW, CLOSE, OPEN_INT, CHG_IN_OI, CONTRACTS
        """
        frames = []
        for parquet_path in sorted(self.cache_dir.glob("*.parquet")):
            # Date range filter from filename (YYYY-MM-DD.parquet)
            fname = parquet_path.stem
            if start and fname < start:
                continue
            if end   and fname > end:
                continue
            if parquet_path.stat().st_size == 0:
                continue  # empty = holiday marker
            try:
                df = pd.read_parquet(parquet_path)
            except Exception:
                continue

            # Filter instrument type
            if "INSTRUMENT" in df.columns:
                df = df[df["INSTRUMENT"].str.contains("OPT", na=False)]

            if df.empty:
                continue

            # Filter symbol
            df = df[df["SYMBOL"].str.upper() == symbol.upper()]
            if df.empty:
                continue

            # Filter expiry
            if expiry and "EXPIRY_DT" in df.columns:
                df = df[df["EXPIRY_DT"] == expiry]

            # Filter option type
            if option_type and "OPTION_TYP" in df.columns:
                df = df[df["OPTION_TYP"].str.upper() == option_type.upper()]

            # Filter strike
            if strike is not None and "STRIKE_PR" in df.columns:
                df = df[df["STRIKE_PR"] == float(strike)]

            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values("TRADE_DATE").reset_index(drop=True)
        return result

    def load_futures(
        self,
        symbol: str,
        start:  Optional[str] = None,
        end:    Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Load historical futures data (continuous — all expiries combined).
        Returns OHLCV + OI sorted by TRADE_DATE.
        """
        frames = []
        for parquet_path in sorted(self.cache_dir.glob("*.parquet")):
            fname = parquet_path.stem
            if start and fname < start:
                continue
            if end   and fname > end:
                continue
            if parquet_path.stat().st_size == 0:
                continue
            try:
                df = pd.read_parquet(parquet_path)
            except Exception:
                continue

            if "INSTRUMENT" in df.columns:
                df = df[df["INSTRUMENT"].str.contains("FUT", na=False)]

            df = df[df["SYMBOL"].str.upper() == symbol.upper()]
            if not df.empty:
                frames.append(df)

        if not frames:
            return pd.DataFrame()

        result = pd.concat(frames, ignore_index=True)
        result = result.sort_values(["TRADE_DATE", "EXPIRY_DT"]).reset_index(drop=True)
        return result

    def cached_dates(self) -> list[str]:
        """Return sorted list of all cached trade dates."""
        return sorted([
            p.stem for p in self.cache_dir.glob("*.parquet")
            if p.stat().st_size > 0
        ])

    def _parquet_path(self, trade_date: date) -> Path:
        return self.cache_dir / f"{trade_date.isoformat()}.parquet"


# ── Singleton ─────────────────────────────────────────────────
_catalog: Optional[BhavcopyCatalog] = None

def get_catalog(cache_dir: str = _DEFAULT_CACHE) -> BhavcopyCatalog:
    global _catalog
    if _catalog is None:
        _catalog = BhavcopyCatalog(cache_dir)
    return _catalog


# ── Quick test ────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    catalog = BhavcopyCatalog()

    # Download last 7 trading days as test
    from datetime import date, timedelta
    end   = date.today().isoformat()
    start = (date.today() - timedelta(days=10)).isoformat()
    print(f"[TEST] Downloading bhavcopy {start} to {end}")
    stats = catalog.download_range(start, end)
    print(f"Stats: {stats}")

    cached = catalog.cached_dates()
    print(f"Cached dates: {cached}")

    if cached:
        df = catalog.load_options("BANKNIFTY", start=cached[0], end=cached[-1])
        print(f"BANKNIFTY options rows: {len(df)}")
        print(df.head(3).to_string())
