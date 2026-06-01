"""
Run this in cmd: python test_nse.py
It shows exactly which data sources work on your machine.
"""
import json, time

print("\n=== NSE Data Source Test ===\n")

# Test 1: nsepython
print("1. Testing nsepython...")
try:
    from nsepython import nsefetch
    data = nsefetch("https://www.nseindia.com/api/allIndices")
    items = data.get("data", [])
    nifty = next((x for x in items if x.get("index") == "NIFTY 50"), None)
    if nifty:
        print(f"   ✓ nsepython works! NIFTY={nifty.get('last')}")
    else:
        print(f"   ✗ nsepython returned data but no NIFTY: keys={list(data.keys())[:5]}")
except Exception as e:
    print(f"   ✗ nsepython failed: {e}")

# Test 2: raw requests
print("\n2. Testing raw requests...")
try:
    import requests
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.nseindia.com",
        "Connection": "keep-alive",
    })
    s.get("https://www.nseindia.com", timeout=5)
    time.sleep(1)
    s.get("https://www.nseindia.com/option-chain", timeout=5)
    time.sleep(1)
    r = s.get("https://www.nseindia.com/api/allIndices", timeout=5)
    data = r.json()
    items = data.get("data", [])
    nifty = next((x for x in items if x.get("index") == "NIFTY 50"), None)
    if nifty:
        print(f"   ✓ raw requests works! NIFTY={nifty.get('last')}")
    else:
        print(f"   ✗ returned {r.status_code}: keys={list(data.keys())[:5]}")
except Exception as e:
    print(f"   ✗ raw requests failed: {e}")

# Test 3: Dhan WebSocket tick store
print("\n3. Testing Dhan tick store...")
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from algo.dhan_ticker import get_tick_store, is_running
    store = get_tick_store()
    running = is_running()
    all_ticks = store.all()
    print(f"   Ticker running: {running}")
    print(f"   Ticks in store: {len(all_ticks)}")
    if all_ticks:
        for token, tick in list(all_ticks.items())[:3]:
            print(f"   Token {token}: ltp={tick.get('last_price') or tick.get('ltp')}")
    else:
        print("   No ticks yet (normal if just started)")
except Exception as e:
    print(f"   ✗ failed: {e}")

print("\n=== Done ===")
print("Paste the output above in chat so we can see what works.")
