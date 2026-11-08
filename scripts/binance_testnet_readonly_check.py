""""One-shot, read-only Binance USD-M Futures Demo connectivity check."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

# When invoked as `python scripts/...py`, Python places `scripts/` rather
# than the repository root on sys.path. Add the root for reliable imports.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from binance_usdm_testnet import (
    TESTNET_BASE_URL,
    BinanceUsdMTestnetClient,
    BinanceTestnetError,
)


def main() -> int:
    api_key = os.environ.get("BINANCE_FUTURES_TESTNET_API_KEY", "")
    api_secret = os.environ.get("BINANCE_FUTURES_TESTNET_API_SECRET", "")
    if not api_key or not api_secret:
        print("FAILED: required Demo credentials are not configured.")
        return 2

    # Probe both known USD-M Futures test hosts using public, unauthenticated
    # GET requests only. The authenticated account request remains pinned to
    # TESTNET_BASE_URL (the configured Demo host); credentials are NEVER sent
    # to the legacy host. Never print response bodies, headers, or credentials.
    public_statuses: dict[str, int | None] = {}
    for host_name, base_url in (
        ("demo", TESTNET_BASE_URL),
        ("legacy_testnet", "https://testnet.binancefuture.com"),
    ):
        try:
            public_response = requests.get(
                base_url + "/fapi/v1/ping",
                timeout=10,
                headers={"User-Agent": "CryptoResearchLab-Demo-Diagnostic/1.0"},
                allow_redirects=False,
            )
            public_statuses[host_name] = public_response.status_code
        except requests.RequestException:
            public_statuses[host_name] = None
    public_status = public_statuses["demo"]

    try:
        client = BinanceUsdMTestnetClient(
            api_key,
            api_secret,
            allow_order_submission=False,
            max_notional_usdt=25,
        )
        account = client.account_status()
        can_trade = account.get("canTrade")
        if not isinstance(can_trade, bool):
            print(
                "FAILED: Demo returned an unexpected account response. "
                f"demo_public_probe_http_status={public_status or 'network_error'} "
                f"legacy_public_probe_http_status={public_statuses['legacy_testnet'] or 'network_error'}"
            )
            return 3

        # Deliberately omit balances, positions, API key, and raw response data.
        print(json.dumps({
            "result": "AUTHENTICATED",
            "environment": "BINANCE_FUTURES_DEMO",
            "demo_public_probe_http_status": public_status,
            "legacy_public_probe_http_status": public_statuses["legacy_testnet"],
            "authenticated_account_host": "demo-fapi.binance.com",
            "read_only": True,
            "can_trade_permission": can_trade,
            "order_submissions": "NOT_ATTEMPTED",
        }, sort_keys=True))
        return 0
    except BinanceTestnetError as exc:
        # Numeric diagnostics only; never expose response bodies or credentials.
        details = [
            f"demo_public_probe_http_status={public_status or 'network_error'}",
            f"legacy_public_probe_http_status={public_statuses['legacy_testnet'] or 'network_error'}"
        ]
        if exc.http_status is not None:
            details.append(f"account_http_status={exc.http_status}")
        if exc.api_code is not None:
            details.append(f"binance_code={exc.api_code}")
        print(
            "FAILED: read-only Demo authentication or request check failed. "
            + " ".join(details)
        )
        return 1
    except (ValueError, OSError):
        print("FAILED: local validation or operating-system error.")
        return 1
    except Exception:
        print("FAILED: unexpected error during read-only Demo check.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
