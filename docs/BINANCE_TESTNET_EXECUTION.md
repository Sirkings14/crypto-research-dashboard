# Binance USD-M Futures Testnet execution

## Scope

`binance_usdm_testnet.py` is an isolated execution adapter for the Binance
USD-M Futures Testnet host only:

- Fixed host: `https://demo-fapi.binance.com`
- No configurable base URL, no redirects to another host, and no production fallback.
- No production API credentials, leverage-setting, account transfer, withdrawal,
  or live-order methods.
- Only MARKET order submission, order lookup, and cancellation are exposed.
- Order submission is disabled unless explicitly enabled by the caller.
- The configured estimated notional cap is hard-limited to 25 USDT.
- This is an execution transport test, not a strategy or profitability test.

## Automated validation

The repository test suite uses mocked HTTP responses. It verifies URL pinning,
HMAC signing, default-disabled order submission, notional limits, symbol/side/
quantity validation, order lookup, cancellation, and that invalid requests
never reach the network.

These tests do not contact Binance and do not prove that Testnet credentials
or exchange connectivity work.

## Manual Testnet validation prerequisites

A later, separately approved manual run requires:

1. A Binance Futures Demo Trading account and API key/secret created specifically
   through the official Demo Trading API Management page at
   `https://demo.binance.com/en/my/settings/api-management`. Never use
   production credentials or legacy Mock Trading keys.
2. Testnet funds obtained through the official Testnet funding mechanism.
3. For the manually triggered read-only connectivity check, store only the
   Demo/Testnet credentials as GitHub Actions secrets named
   `BINANCE_FUTURES_TESTNET_API_KEY` and
   `BINANCE_FUTURES_TESTNET_API_SECRET`. These secrets are not needed by unit tests.
4. Do not put credentials in source files, commits, issue comments, logs, or
   chat messages.

No credentials are required for the automated mocked tests. No Testnet order
has been submitted by the CI workflow in this implementation.

## Read-only preflight added

`binance_testnet_preflight.py` validates a selected symbol against the Testnet `exchangeInfo` response and a Testnet ticker price. It checks trading status, USDT perpetual contract type, MARKET permission, lot-size rules and minimum notional. Quantity calculations use `Decimal`, floor to the permitted step size, and enforce the 25 USDT ceiling. The result explicitly labels order submission as disabled. Unit tests cover invalid metadata and cap/minimum-size failures.

The adapter now exposes the public Testnet ticker endpoint. The next operational step is a manually triggered, read-only authenticated connectivity check; no order endpoint should be called during that check.

## Required next validation before a manual order

The pure preflight function now validates Testnet exchangeInfo and ticker data
when supplied, and its tests cover step rounding, symbol eligibility, minimum
quantity/notional, and the 25 USDT cap. It is not wired to submit orders or
currently invoked by the authenticated connectivity workflow. Before any
separately approved Testnet order lifecycle test, a reviewed read-only wrapper
must fetch exchangeInfo and ticker data from the pinned host, call this
preflight, and refuse if no valid quantity fits the cap.

A manual lifecycle test should use one tiny Testnet-only order, confirm its
exchange response and queried status, then cancel it if still open. If it is
already filled, the test must immediately reconcile the fill and close only
the test position with a reduce-only order. Never run this against production.

## Safety state

`live_trading_enabled` remains false. No live trading workflow, production
endpoint, or production credential path is introduced.


## Read-only authenticated connectivity check

The manually triggered workflow `.github/workflows/binance-testnet-readonly-connection.yml`
uses only the two Testnet-named GitHub Actions secrets and the fixed Binance
Demo Trading Futures host (`https://demo-fapi.binance.com`).
It calls the signed `GET /fapi/v2/account` endpoint to verify authentication.
It does not call any order, cancel, transfer, leverage, or withdrawal endpoint.
The script prints only an authentication result and the account's trade-permission
boolean; it deliberately does not print balances, positions, raw API responses,
or credential material.

The workflow requires the manual input `YES_READ_ONLY`; the default is `NO`.
The workflow file is also installed on the default branch so it can be manually
dispatched in GitHub Actions. It checks out the `binance-testnet-execution`
branch containing the reviewed adapter code. A successful run proves only
that Testnet authentication works; it is not an order test or profitability
evidence.
