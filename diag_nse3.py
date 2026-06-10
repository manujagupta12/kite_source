"""Verify patched NSEFetcher + data_provider end-to-end. Writes diag_out3.txt."""
import sys, traceback
from pathlib import Path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "algo"))

out = []
def log(s):
    out.append(str(s))

try:
    from algo.nse_fetcher import NSEFetcher
    f = NSEFetcher()
    exps = f.get_expiry_dates("BANKNIFTY")
    log(f"expiries: {exps[:4]}")
    oc = f.get_option_chain("BANKNIFTY", expiry=exps[0] if exps else None)
    log(f"chain rows: {len(oc.get('chain', {}))}  spot: {oc.get('underlying')}")
    atm = f.get_atm_strike("BANKNIFTY")
    log(f"ATM: {atm}")
    pcr = f.get_pcr("BANKNIFTY")
    log(f"PCR: {pcr}")
except Exception:
    log("FETCHER FAILED:")
    log(traceback.format_exc()[-2000:])

try:
    from algo import data_provider as dp
    df = dp.get_instruments("BANKNIFTY")
    log(f"data_provider rows: {0 if df is None else len(df)}")
    if df is not None and not df.empty:
        atm = dp.get_atm_strike(df)
        sp = dp.get_spread(df, atm, "CE", "BANKNIFTY")
        log(f"dp ATM={atm} spread={sp.get('spread') if sp else None} fair={sp.get('fair') if sp else None} far_bid={sp.get('far_bid') if sp else None}")
except Exception:
    log("DATA_PROVIDER FAILED:")
    log(traceback.format_exc()[-2000:])

(ROOT / "diag_out3.txt").write_text("\n".join(out), encoding="utf-8")
