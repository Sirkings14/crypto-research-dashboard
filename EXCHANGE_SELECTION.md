# Exchange selection and rollout policy

**Decision date:** 2026-11-02  
**Primary candidate:** Binance  
**Read-only derivatives data fallback:** OKX  
**Account status:** user reports verified Binance account; USDⓈ-M Futures and BTCUSDT perpetual are visible; no funds deposited  
**Execution status:** disabled; no API credentials or order placement

## Why Binance is the primary candidate

The operator is based in Burkina Faso. Binance's country/app-support page, updated 2026-08-18, lists Burkina Faso as available. Binance also documents XOF among supported fiat currencies and describes African fiat/P2P access in more than 30 countries. These are useful accessibility signals, but they do **not** prove that every payment rail, deposit/withdrawal method, or derivatives product is available to this particular account.

Account-level update: the user confirms the account is verified and working, and has now confirmed that the USDⓈ-M Futures section and BTCUSDT perpetual are visible in the app. No funds have been deposited and none are needed for this engineering phase. Remaining checks are:

1. Confirm any desired XOF deposit/withdrawal or P2P payment method directly in the account and review its fees/counterparty terms before choosing to fund it. This is not required for the current build.
2. Verify contract-specific margin mode, maintenance-margin brackets, liquidation-fee rules and the applicable account fee tier before any margin-sensitive paper simulation or later execution.
3. Keep account API permissions unverified until explicitly inspected in the account interface; no credentials or API secrets should be shared in chat.

The app confirmation establishes visible product access, not account-specific API permission or the availability of every contract and funding rail. No VPN, false residence, borrowed identity, or other restriction circumvention is acceptable.

## Why OKX remains in the research data stack

The existing public-data collector already uses OKX USDT perpetual market endpoints for trade candles, mark-price candles, funding history and instrument metadata. Binance USD-M Futures requests from the GitHub Actions runner previously returned HTTP 451, while OKX public swap data worked. This is a runner/network observation, not proof that a Binance account or the Binance Futures product is unavailable in Burkina Faso.

Therefore:
- Keep OKX as a read-only research-data source while its public endpoints remain reliable.
- Do not interpret OKX data as a recommendation to open an OKX account.
- Do not mix Binance execution assumptions with OKX contract metadata, funding, mark prices, fees or liquidation brackets.
- Any future execution adapter must consume data and risk rules from the same selected venue and exact contract.

## Alternatives considered

- **Bybit:** not selected as the primary venue. Its fiat-service provider's restricted KYC-country list includes Burkina Faso, which is a material limitation for funding/withdrawal routes. Its platform-wide excluded-jurisdiction list is a separate list and does not itself establish every product's availability.
- **OKX:** retained as the current read-only derivatives data provider. Its published app-availability list we reviewed did not list Burkina Faso, so we will not assume account eligibility from public market-data access alone.
- **Binance:** best current candidate based on its explicit Burkina Faso app-support listing and XOF/Africa access documentation, with visible USDⓈ-M Futures/BTCUSDT access now confirmed, while account-specific API permissions, fee tier, margin settings and funding rails remain unverified.

## Safe implementation sequence

1. Keep the current collector, paper ledgers and stress tests read-only/research-only.
2. Add venue identity and provenance to every market dataset: venue, product type, instrument ID, quote/settlement currency, contract metadata source and retrieval timestamp.
3. Build Binance public Spot market-data support and validate it in GitHub Actions before changing any research baselines.
4. Investigate Binance USDⓈ-M public data separately. HTTP 451 from the Actions runner must be recorded as a source-access failure, not silently bypassed.
5. Keep OKX swap data as a clearly labelled research fallback; do not silently substitute it for Binance data.
6. With USDⓈ-M/BTCUSDT visibility confirmed, validate the public contract rules in the isolated Binance worker first. Before any account-specific margin simulation, verify the account's margin mode, fee tier, maintenance-margin brackets and liquidation rules through official account documentation/interface.
7. Any later private API integration starts read-only, with minimum permissions and no withdrawal permission. Order placement remains a separate explicitly approved project phase.

## Current safety boundary

- `live_trading_enabled: false`
- No API keys, secrets, account balances or personal identity data are stored by this decision.
- No live orders, transfers, deposits or withdrawals are performed.
- Public market data and paper simulations do not establish profitability or guarantee real execution.


## Account confirmation update

The account owner has confirmed that their Binance account is verified and working, and that no funds have been deposited. This removes the need to treat account KYC as unconfirmed, but it does not by itself confirm USDⓈ-M Futures product access, each desired contract, or available funding/withdrawal rails. No deposit is required for the current engineering phase.

A separate `binance_usdm_data.py` adapter now fetches public USDⓈ-M trade candles, mark-price candles and historical funding records. It is covered by mocked-response tests and is not yet connected to the scheduled portfolio replay. The existing OKX dataset remains explicitly labelled as OKX; switching the replay requires successful Binance public-data validation and Binance-specific contract/fee/margin rules.


## USDⓈ-M app access confirmed

The user confirms the USDⓈ-M Futures section and BTCUSDT perpetual are visible in the verified Binance app. The public-data worker now probes Binance's separate USD-M trade candles, mark-price candles, realized funding history and public exchangeInfo rules. This app confirmation is not treated as proof of account-specific fee tier, leverage brackets, margin settings, or permission for any API operation. No deposit, private API key or live order is required for the current work.


## GitHub runner verification status

The first completed GitHub Actions Binance USD-M probe (2026-11-04) returned HTTP 451 from the hosted runner for `/fapi/v1/exchangeInfo` and all seven `/fapi/v1/klines` requests. The diagnostic artifact confirms zero datasets collected and zero paper-ready markets. This is a runner/network restriction, not evidence that the user's verified Binance account or visible USDⓈ-M product is unavailable.

The worker now records `source_status: blocked_by_http_451`, preserves diagnostics and exits as an explicit provider skip rather than presenting the known runner restriction as a code defect. The dedicated workflow reports a warning and remains non-paper-ready. On that response it audits the two latest complete months from the separate official Binance Vision archive host, including checksums and timestamp coverage for trade klines, mark-price klines and funding-rate files across all seven symbols.

The 2026-09 audit passed checksums for all 21 files and found complete 720-hour trade and mark-price timelines for all seven symbols. The 2026-10 audit also passed all checksums, but every symbol was missing 24 trade-candle hours and 48 mark-price-candle hours. October is not eligible for replay; no gaps are forward-filled and no other venue is substituted. Funding records were timestamped 0–9ms after hourly opens in September and 0–7ms in October. The paper ledger now handles exact-open funding before the signal and 1–10ms post-open funding after the signal; farther offsets inside the replay window are rejected. When the archive file does not contain a settlement mark price, the ledger explicitly labels its use of the hourly mark-open proxy.

This audit does not yet ingest archive files into a paper replay or solve current REST reachability. Binance USD-M must not be activated for portfolio replay until a separately validated dataset has complete trade/mark coverage, realized funding aligned to the supported event window, and matching Binance contract rules. The existing OKX derivatives data remains a separate read-only research source; no venue data is silently substituted.

## Monthly archive ingestion verified

A separate historical-only ingestion worker now downloads the three official Binance Vision monthly USD-M datasets for all seven symbols and verifies each ZIP's SHA-256 checksum before parsing. It rejects incomplete hourly coverage, mismatched trade/mark timestamps and funding events more than 10ms after an hourly open. The September 2026 run completed successfully: 7/7 symbols, 720 trade candles + 720 mark candles + 90 realized funding records per symbol, all checksums valid and no candle gaps. The output is stored as a GitHub Actions artifact, not committed as mutable market data.

This closes the historical archive ingestion gap for September, but not the runner's HTTP 451 restriction on current Binance USD-M REST data. The archive funding CSV has no event mark price; the ledger must disclose its mark-candle-open proxy. Historical exchange filters and maintenance tiers are not supplied by these market archives. Current exchangeInfo metadata must not be represented as verified September historical rules. Before any portfolio replay, use a clearly labelled metadata snapshot and preserve that limitation, or source verified historical instrument rules. No live trading or account API is involved.

The archive worker also emits descriptive-only market analytics (trade/mark basis, hourly return and volatility statistics, buy-and-hold drawdown, realized funding-rate distribution and quote volume). These metrics describe the historical market sample; they do not evaluate a trading strategy, imply predictive value, or validate profitability. The annualized volatility estimate extrapolates a single month and must not be read as a forecast.
