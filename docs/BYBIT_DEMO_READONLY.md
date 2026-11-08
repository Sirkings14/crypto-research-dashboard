# Bybit V5 Demo read-only adapter

This is an optional, isolated Demo connectivity adapter. It does not replace
Binance market-data ingestion, the strategy engine, or the paper ledger.

## Environment

- Fixed REST host: `https://api-demo.bybit.com`
- Demo account only. Create the API key while switched into Bybit **Demo Trading**.
- Use a key with **Read** permission only.
- Store credentials only as GitHub Actions secrets:
  - `BYBIT_DEMO_API_KEY`
  - `BYBIT_DEMO_API_SECRET`

Do not use a mainnet key, do not paste credentials into chat, and do not commit
them to the repository. Do not enable IP/withdrawal/transfer permissions.

## Safe test

The manual workflow first runs local adapter tests. The authenticated job runs
only when the operator selects `YES_READ_ONLY`. It calls:

1. Public `GET /v5/market/time`
2. Authenticated `GET /v5/account/info`

The script prints only HTTP/result status and safe account metadata. It does
not print wallet balances, positions, raw responses, API credentials, or
signatures. It has no order, leverage, transfer, or withdrawal implementation.

A successful read-only check proves connectivity and authentication only. It
does not prove strategy profitability or authorize simulated/live order
submission.

## Availability and regional eligibility

Bybit documents Demo Trading API access and the `api-demo.bybit.com` host.
Demo availability and derivatives eligibility remain subject to Bybit's
applicable terms and account restrictions. Verify access through Bybit's
official website from your actual location before creating credentials. Do not
use a VPN, proxy, false residence, or another person's account to bypass a
restriction.

Official references:
- https://bybit-exchange.github.io/docs/v5/demo
- https://www.bybit.com/en/help-center/article/Demo-Trading
