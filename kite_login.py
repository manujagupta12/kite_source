"""
kite_login.py — daily Zerodha Kite Connect access-token generator.

Usage (each trading morning, ~1 min):
    python kite_login.py

Flow:
  1. Opens the Kite login URL in your browser.
  2. After login + 2FA, Kite redirects to your registered redirect URL
     with ?request_token=XXXX in the address bar.
  3. Paste that request_token here — the script exchanges it
     (SHA-256 checksum of api_key + request_token + api_secret)
     and writes KITE_ACCESS_TOKEN into .env.
  4. Restart the backend (restart_backend.bat) to pick it up.

No kiteconnect package needed — uses the raw HTTPS API.
"""
import hashlib
import re
import sys
import webbrowser
from pathlib import Path

import requests

ENV_PATH = Path(__file__).parent / ".env"
API_BASE = "https://api.kite.trade"


def read_env() -> dict:
    env = {}
    if not ENV_PATH.exists():
        sys.exit(f"ERROR: {ENV_PATH} not found")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def write_env_var(key: str, value: str) -> None:
    """Update or append KEY=value in .env without touching anything else."""
    text = ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    ENV_PATH.write_text(text, encoding="utf-8")


def main() -> None:
    env = read_env()
    api_key = env.get("KITE_API_KEY", "")
    api_secret = env.get("KITE_API_SECRET", "")
    if not (api_key and api_secret):
        sys.exit("ERROR: KITE_API_KEY / KITE_API_SECRET missing in .env")

    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    print(f"\nOpening Kite login:\n  {login_url}\n")
    webbrowser.open(login_url)

    raw = input("Paste the request_token from the redirect URL: ").strip()
    # Accept either the bare token or the full redirect URL
    m = re.search(r"request_token=([A-Za-z0-9]+)", raw)
    request_token = m.group(1) if m else raw
    if not request_token:
        sys.exit("ERROR: no request_token provided")

    checksum = hashlib.sha256(
        (api_key + request_token + api_secret).encode()
    ).hexdigest()

    r = requests.post(
        f"{API_BASE}/session/token",
        headers={"X-Kite-Version": "3"},
        data={"api_key": api_key, "request_token": request_token,
              "checksum": checksum},
        timeout=10,
    )
    body = r.json()
    if r.status_code != 200 or body.get("status") != "success":
        sys.exit(f"ERROR {r.status_code}: {body.get('message', body)}")

    data = body["data"]
    access_token = data["access_token"]
    write_env_var("KITE_ACCESS_TOKEN", access_token)
    print(f"\n  Logged in as : {data.get('user_name')} ({data.get('user_id')})")
    print(f"  Access token : {access_token[:6]}...{access_token[-4:]} (written to .env)")
    print("\nDone. Run restart_backend.bat to pick up the new token.")
    print("Token is valid until ~7:30 AM tomorrow.")


if __name__ == "__main__":
    main()
