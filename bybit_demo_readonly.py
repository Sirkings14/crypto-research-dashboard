"""Read-only Bybit V5 Demo account connectivity check.

This module deliberately exposes no order, transfer, leverage, or withdrawal
methods. Use a Demo Trading API key with Read permission only.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
from urllib.parse import urlencode

import requests

BASE_URL = "https://api-demo.bybit.com"
PUBLIC_TIME_URL = f"{BASE_URL}/v5/market/time"
ACCOUNT_INFO_URL = f"{BASE_URL}/v5/account/info"
RECV_WINDOW = "5000"


class BybitDemoError(RuntimeError):
    """Sanitized Bybit Demo request failure."""


def _signature(secret: str, timestamp: str, api_key: str, recv_window: str, query: str = "") -> str:
    payload = f"{timestamp}{api_key}{recv_window}{query}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _public_probe(session=requests, timeout: int = 10) -> int:
    response = session.get(PUBLIC_TIME_URL, timeout=timeout)
    return response.status_code


def read_only_account_check(
    api_key: str,
    api_secret: str,
    *,
    session=requests,
    clock_ms=None,
    timeout: int = 10,
) -> dict:
    """Check authenticated Demo account access; return only safe metadata."""
    if not api_key or not api_secret:
        raise ValueError("Bybit Demo API credentials are required")
    timestamp = str(clock_ms if clock_ms is not None else int(time.time() * 1000))
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": _signature(api_secret, timestamp, api_key, RECV_WINDOW),
        "X-BAPI-SIGN-TYPE": "2",
        "User-Agent": "CryptoResearchLab-Bybit-Demo-ReadOnly/1.0",
    }
    response = session.get(ACCOUNT_INFO_URL, headers=headers, timeout=timeout)
    if response.status_code != 200:
        raise BybitDemoError(f"http_status={response.status_code}")
    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        raise BybitDemoError("invalid_json_response") from exc
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        code = payload.get("retCode") if isinstance(payload, dict) else None
        safe_code = code if isinstance(code, int) else "unknown"
        raise BybitDemoError(f"bybit_ret_code={safe_code}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise BybitDemoError("unexpected_account_response")
    account_type = result.get("unifiedMarginStatus")
    return {
        "result": "AUTHENTICATED",
        "environment": "BYBIT_DEMO",
        "account_type_status": account_type if isinstance(account_type, int) else "available",
        "read_only": True,
        "order_submissions": "NOT_ATTEMPTED",
    }


def main() -> int:
    api_key = os.environ.get("BYBIT_DEMO_API_KEY", "")
    api_secret = os.environ.get("BYBIT_DEMO_API_SECRET", "")
    if not api_key or not api_secret:
        print("FAILED: BYBIT_DEMO_API_KEY and BYBIT_DEMO_API_SECRET are not configured.")
        return 2
    try:
        public_status = _public_probe()
        try:
            result = read_only_account_check(api_key, api_secret)
        except BybitDemoError as exc:
            detail = str(exc)
            print(f"FAILED: Bybit Demo read-only account check failed. public_probe_http_status={public_status} {detail}")
            return 1
        result["public_probe_http_status"] = public_status
        print(" ".join(f"{key}={value}" for key, value in sorted(result.items())))
        return 0
    except requests.RequestException:
        print("FAILED: Bybit Demo network request failed.")
        return 1
    except (ValueError, OSError):
        print("FAILED: local validation or operating-system error.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
