"""One-shot, read-only Binance USD-M Futures Testnet authentication check."""
from __future__ import annotations

import json
import os
import sys

from binance_usdm_testnet import BinanceUsdMTestnetClient, BinanceTestnetError


def main() -> int:
    api_key = os.environ.get("BINANCE_FUTURES_TESTNET_API_KEY", "")
    api_secret = os.environ.get("BINANCE_FUTURES_TESTNET_API_SECRET", "")
    if not api_key or not api_secret:
        print("FAILED: required Testnet credentials are not configured.")
        return 2

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
            print("FAILED: Testnet returned an unexpected account response.")
            return 3

        # Deliberately omit balances, positions, API key, and all raw response data.
        print(json.dumps({
            "result": "AUTHENTICATED",
            "environment": "BINANCE_FUTURES_TESTNET",
            "read_only": True,
            "can_trade_permission": can_trade,
            "order_submissions": "NOT_ATTEMPTED",
        }, sort_keys=True))
        return 0
    except (BinanceTestnetError, ValueError, OSError):
        # Do not print exception details or request/response data to avoid leakage.
        print("FAILED: read-only Testnet authentication or request check failed.")
        return 1
    except Exception:
        print("FAILED: unexpected error during read-only Testnet check.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
