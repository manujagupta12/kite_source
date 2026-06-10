"""Probe NSE option-chain v3 API format. Writes diag_out2.txt."""
import sys, time, json
from pathlib import Path

ROOT = Path(__file__).parent
out = []
def log(s):
    out.append(str(s))

import requests
s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
})
try:
    s.get("https://www.nseindia.com", timeout=8); time.sleep(0.4)
    s.get("https://www.nseindia.com/option-chain", timeout=8); time.sleep(0.4)
except Exception as e:
    log(f"warmup error: {e!r}")

CANDIDATES = [
    "https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=BANKNIFTY",
    "https://www.nseindia.com/api/option-chain-contract-info?symbol=BANKNIFTY",
    "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY",
]
for url in CANDIDATES:
    try:
        r = s.get(url, timeout=10)
        ct = r.headers.get("content-type", "")
        log(f"GET {url}\n  -> {r.status_code} ct={ct} len={len(r.content)}")
        if "json" in ct and r.status_code == 200:
            j = r.json()
            log(f"  top keys: {list(j.keys())}")
            rec = j.get("records") or {}
            if rec:
                log(f"  records keys: {list(rec.keys())}")
                log(f"  expiryDates[:5]: {rec.get('expiryDates', [])[:5]}")
                log(f"  underlyingValue: {rec.get('underlyingValue')}")
                data = rec.get("data") or []
                log(f"  data rows: {len(data)}")
                if data:
                    log("  sample row: " + json.dumps(data[len(data)//2])[:900])
            else:
                log("  body sample: " + json.dumps(j)[:900])
        time.sleep(0.6)
    except Exception as e:
        log(f"  FAILED: {e!r}")

# If contract-info gave expiries, try v3 with explicit expiry
try:
    r = s.get("https://www.nseindia.com/api/option-chain-contract-info?symbol=BANKNIFTY", timeout=10)
    if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
        j = r.json()
        exps = j.get("expiryDates") or (j.get("records") or {}).get("expiryDates") or []
        log(f"contract-info expiries[:5]: {exps[:5]}")
        if exps:
            e0 = exps[0]
            url = f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol=BANKNIFTY&expiry={e0}"
            r2 = s.get(url, timeout=10)
            log(f"GET {url}\n  -> {r2.status_code} len={len(r2.content)}")
            if r2.status_code == 200 and "json" in r2.headers.get("content-type", ""):
                j2 = r2.json()
                rec = j2.get("records") or {}
                data = rec.get("data") or []
                log(f"  v3+expiry records keys: {list(rec.keys())} rows={len(data)} spot={rec.get('underlyingValue')}")
                if data:
                    log("  sample row: " + json.dumps(data[len(data)//2])[:900])
except Exception as e:
    log(f"v3+expiry FAILED: {e!r}")

(ROOT / "diag_out2.txt").write_text("\n".join(out), encoding="utf-8")
