"""Diagnose why the option-chain feed fails. Writes diag_out.txt next to this file."""
import sys, time, traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "algo"))

out = []
def log(s):
    out.append(str(s))

log(f"Python: {sys.version}")

# ── 1. nsepython option chain ─────────────────────────────────
try:
    from nsepython import nse_optionchain_scrapper
    t = time.time()
    d = nse_optionchain_scrapper("BANKNIFTY")
    rows = len(d.get("records", {}).get("data", [])) if d else 0
    spot = d.get("records", {}).get("underlyingValue") if d else None
    log(f"[1] nsepython OC: rows={rows} spot={spot} ({time.time()-t:.1f}s)")
except Exception as e:
    log(f"[1] nsepython OC FAILED: {e!r}")
    log(traceback.format_exc()[-1200:])

# ── 2. raw requests option chain ──────────────────────────────
try:
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://www.nseindia.com/option-chain",
    })
    r0 = s.get("https://www.nseindia.com", timeout=8)
    time.sleep(0.5)
    r1 = s.get("https://www.nseindia.com/option-chain", timeout=8)
    time.sleep(0.5)
    r = s.get("https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY", timeout=10)
    log(f"[2] raw OC: warmup={r0.status_code}/{r1.status_code} oc_status={r.status_code} len={len(r.content)} ct={r.headers.get('content-type','')}")
    if r.status_code == 200:
        try:
            j = r.json()
            log(f"[2] raw OC rows={len(j.get('records',{}).get('data',[]))}")
        except Exception as je:
            log(f"[2] raw OC JSON parse failed: {je!r} body[:200]={r.text[:200]!r}")
    else:
        log(f"[2] raw OC body[:200]={r.text[:200]!r}")
except Exception as e:
    log(f"[2] raw OC FAILED: {e!r}")

# ── 3. Dhan data path ─────────────────────────────────────────
try:
    from algo import dhan_data
    avail = dhan_data.is_available()
    log(f"[3] dhan_data.is_available = {avail}")
    if avail:
        exp = dhan_data.get_expiry_dates("BANKNIFTY")
        log(f"[3] dhan expiries = {exp[:4] if exp else exp}")
        if exp:
            oc = dhan_data.get_nse_compatible_chain("BANKNIFTY", expiry=exp[0])
            ch = oc.get("chain", {}) if oc else {}
            log(f"[3] dhan chain rows={len(ch)} underlying={oc.get('underlying') if oc else None} src={oc.get('_src') if oc else None}")
except Exception as e:
    log(f"[3] dhan_data FAILED: {e!r}")
    log(traceback.format_exc()[-1200:])

# ── 4. Dhan market feed ───────────────────────────────────────
try:
    from algo import dhan_market_feed as mf
    avail = mf.is_available()
    log(f"[4] dhan_market_feed.is_available = {avail}")
    if avail:
        exp = mf.get_expiry_dates("BANKNIFTY")
        log(f"[4] mf expiries = {exp[:4] if exp else exp}")
        oc = mf.get_option_chain("BANKNIFTY")
        ch = oc.get("chain", {}) if oc else {}
        log(f"[4] mf chain rows={len(ch)} underlying={oc.get('underlying') if oc else None}")
except Exception as e:
    log(f"[4] dhan_market_feed FAILED: {e!r}")
    log(traceback.format_exc()[-1200:])

(ROOT / "diag_out.txt").write_text("\n".join(out), encoding="utf-8")
