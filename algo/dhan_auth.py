"""
dhan_auth.py
============
Dhan API token validation and auto-refresh helper.

How to get your token (2 min):
  1. Open https://web.dhan.co
  2. Profile > DhanHQ Trading APIs > Request Access (first time only)
  3. Access Tokens > Create New > Generate Token
  4. Copy Client ID + Access Token to .env:
       DHAN_CLIENT_ID=your_client_id
       DHAN_ACCESS_TOKEN=your_token
"""

import os
import json
import time
import requests
from pathlib import Path
from typing import Optional

TOKEN_FILE = Path(__file__).parent.parent / "data" / "dhan_token.json"
_DHAN_API  = "https://api.dhan.co/v2"
_AUTH_API  = "https://auth.dhan.co/app"


def get_credentials() -> tuple:
    """Return (client_id, access_token). Priority: env vars > token file."""
    client_id    = os.environ.get("DHAN_CLIENT_ID", "").strip()
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "").strip()
    if client_id and access_token:
        return client_id, access_token
    if TOKEN_FILE.exists():
        try:
            data = json.loads(TOKEN_FILE.read_text())
            c, t = data.get("client_id",""), data.get("access_token","")
            if c and t:
                os.environ["DHAN_CLIENT_ID"]    = c
                os.environ["DHAN_ACCESS_TOKEN"] = t
                return c, t
        except Exception:
            pass
    raise RuntimeError(
        "Dhan credentials not set.\n"
        "Add DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN to .env\n"
        "Get them: web.dhan.co > Profile > DhanHQ Trading APIs"
    )


def validate_token() -> bool:
    """Validate token against Dhan API. Returns True if valid."""
    try:
        client_id, access_token = get_credentials()
    except RuntimeError:
        print("[DhanAuth] No credentials — running on NSE Direct API only")
        return False
    try:
        r = requests.get(
            f"{_DHAN_API}/funds",
            headers={"access-token": access_token, "client-id": client_id},
            timeout=8,
        )
        if r.status_code == 200:
            print("[DhanAuth] Token valid ✓")
            return True
        elif r.status_code == 401:
            print("[DhanAuth] Token EXPIRED — refresh at web.dhan.co > DhanHQ Trading APIs")
            _try_totp_refresh(client_id)
            return False
        else:
            print(f"[DhanAuth] Status {r.status_code} — assuming token valid (market may be closed)")
            return True
    except requests.exceptions.ConnectionError:
        print("[DhanAuth] No internet — skipping validation")
        return True
    except Exception as e:
        print(f"[DhanAuth] Validation error: {e}")
        return True


def refresh_token_totp() -> Optional[str]:
    """
    Auto-refresh via TOTP.
    Requires DHAN_PIN + DHAN_TOTP_SECRET in .env.
    Returns new token string or None.
    """
    try:
        import pyotp
    except ImportError:
        return None
    client_id   = os.environ.get("DHAN_CLIENT_ID", "")
    pin         = os.environ.get("DHAN_PIN", "")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")
    if not all([client_id, pin, totp_secret]):
        return None
    totp = pyotp.TOTP(totp_secret).now()
    try:
        r = requests.post(
            f"{_AUTH_API}/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": totp},
            timeout=10,
        )
        r.raise_for_status()
        data  = r.json()
        token = data.get("accessToken", "")
        if token:
            _save_token(client_id, token, data.get("expiryTime",""))
            print(f"[DhanAuth] Token refreshed. Expiry: {data.get('expiryTime')}")
        return token or None
    except Exception as e:
        print(f"[DhanAuth] TOTP refresh failed: {e}")
        return None


def save_token_manual(client_id: str, access_token: str) -> None:
    """Save manually-entered token to file + env."""
    _save_token(client_id, access_token, "")
    print(f"[DhanAuth] Saved to {TOKEN_FILE}")


def _save_token(client_id: str, access_token: str, expiry: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "client_id": client_id, "access_token": access_token,
        "expiry": expiry, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2))
    os.environ["DHAN_CLIENT_ID"]    = client_id
    os.environ["DHAN_ACCESS_TOKEN"] = access_token


def _try_totp_refresh(client_id: str) -> None:
    token = refresh_token_totp()
    if token:
        print("[DhanAuth] Token auto-refreshed via TOTP")
    else:
        print("[DhanAuth] Refresh manually: web.dhan.co > DhanHQ Trading APIs > Access Tokens")


if __name__ == "__main__":
    print("\n[DhanAuth] Token check\n")
    validate_token()
