# Exchange selection and rollout policy

**Decision date:** 2026-11-02  
**Primary candidate:** Binance  
**Read-only derivatives data fallback:** OKX  
**Execution status:** disabled; no API credentials or order placement

## Why Binance is the primary candidate

The operator is based in Burkina Faso. Binance's country/app-support page, updated 2026-08-18, lists Burkina Faso as available. Binance also documents XOF among supported fiat currencies and describes African fiat/P2P access in more than 30 countries. These are useful accessibility signals, but they do **not** prove that every payment rail, deposit/withdrawal method, or derivatives product is available to this particular account.

Before treating Binance as an executable venue, the account owner must independently confirm, using their true Burkina Faso residence and identity details:

1. KYC verification is accepted for Burkina Faso.
2. Spot trading is available.
3. USDⓈ-M perpetual Futures is explicitly enabled for the verified account, if Futures is to be used.
4. The desired XOF deposit/withdrawal or P2P payment method is displayed in the account and its fees/counterparty terms are acceptable.
5. The specific contracts, leverage rules, margin mode, maintenance-margin brackets, liquidation-fee rules and fee tier are available in the account's applicable product documentation.

App availability is not the same as product eligibility. No VPN, false residence, borrowed identity, or other restriction circumvention is acceptable. If Futures is not enabled for the verified account, the system must remain Spot-only for any venue-linked paper workflow; it must not route Futures orders elsewhere automatically.

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
- **Binance:** best current candidate based on its explicit Burkina Faso app-support listing and XOF/Africa access documentation, subject to account-level KYC and product confirmation.

## Safe implementation sequence

1. Keep the current collector, paper ledgers and stress tests read-only/research-only.
2. Add venue identity and provenance to every market dataset: venue, product type, instrument ID, quote/settlement currency, contract metadata source and retrieval timestamp.
3. Build Binance public Spot market-data support and validate it in GitHub Actions before changing any research baselines.
4. Investigate Binance USDⓈ-M public data separately. HTTP 451 from the Actions runner must be recorded as a source-access failure, not silently bypassed.
5. Keep OKX swap data as a clearly labelled research fallback; do not silently substitute it for Binance data.
6. Only after the account owner confirms product eligibility, collect official account-specific contract rules and calibrate fee, funding, maintenance-margin tier and liquidation models.
7. Any later private API integration starts read-only, with minimum permissions and no withdrawal permission. Order placement remains a separate explicitly approved project phase.

## Current safety boundary

- `live_trading_enabled: false`
- No API keys, secrets, account balances or personal identity data are stored by this decision.
- No live orders, transfers, deposits or withdrawals are performed.
- Public market data and paper simulations do not establish profitability or guarantee real execution.
