# Crypto Research Lab

Research dashboard for historical OHLCV analysis. Default baseline: BTC/USDT Spot, 5-minute bars, UTC.

## Persistent paper run

The `BTCUSDT paper simulation` GitHub Actions workflow polls completed 5-minute Binance Spot candles every five minutes, advances one fixed SMA 10/40 long/flat paper account, and commits its state and bar-by-bar ledger to `data/paper_state.json` and `data/paper_history.csv`. It runs independently of Codespaces and needs no API key or exchange account. The dashboard reads the public ledger and refreshes its display every minute. The simulation seeds at its first successful workflow run; it does not backfill or claim performance before that start time. Missed intervals are caught up from public candles when possible.

GitHub's scheduled jobs can start late during busy periods. The workflow processes only closed bars and the worker catches up missed bars; it fails visibly if the gap exceeds its 30-day recovery window. Public repository workflows can be automatically disabled by GitHub after 60 days without repository activity, so check the Actions tab if the ledger stops updating.

This is a fixed research paper account: 1,000 USDT starting equity, 50% allocation, SMA 10/40, and estimated 10 bps fee plus 5 bps slippage per side. The ledger is public because the repository is public. It records hypothetical fills only; no live trading is present.

## Run in Codespaces

Create or open a Codespace for this repository, wait for environment setup, then run:

```bash
streamlit run app.py
```

Open forwarded port 8501. In the sidebar, choose public Binance Spot candles or upload a CSV. Public candles require no account or API key. The CSV upload is the fallback if the public endpoint is inaccessible. You can download fetched candles from the page.

## Research workflow

1. Inspect row count, interval, date coverage and missing-bar gaps.
2. Sweep SMA parameters across the chronological training and validation segments. Candidates are ranked on validation return subject to the selected drawdown ceiling.
3. Freeze one candidate and explicitly evaluate it on the holdout. Each holdout evaluation is logged locally in SQLite with the assumptions and data hash.
4. Monitor the persistent paper account and its closed-bar ledger while separately using historical sweeps and the holdout workflow to evaluate strategy hypotheses.

## CSV format

Required columns: `timestamp, open, high, low, close`; `volume` is optional. Timestamps may be ISO strings or Unix seconds/milliseconds. At least 1,000 valid, unique rows are required. Use UTC timestamps for comparable results.

## Model and limits

- Long-only, spot-like position; no leverage, short selling, credentials, exchange account connection, or order placement.
- Signals use completed candles and apply at the next candle open. Fee and slippage settings are estimates.
- Train/validation/holdout split is chronological at 60/20/20. Repeatedly using holdout results to tune the model invalidates it as an independent final check.
- The model does not include order-book depth, market impact, latency, funding, taxes, delistings, or exchange outages. Historical and simulated results do not predict future performance.
- Codespaces is an interactive dashboard environment. Stopping or closing it pauses the app, while the scheduled GitHub Actions paper worker continues independently. Historical holdout experiment records remain in the Codespace filesystem; the paper ledger is public in this repository.

## Multi-strategy research lab

The experimental `strategy_lab.py` dashboard evaluates a deterministic grid of more than 1,000 SMA, EMA, momentum, RSI, Bollinger-band, and Donchian configurations. It ranks candidates on a chronological validation segment after a drawdown filter, records every trial with its data hash and assumptions in the local SQLite database, and only calculates holdout metrics after a candidate is selected. The holdout is then consumed and must not be used to tune that strategy.

Run it in the Codespace with:

```bash
streamlit run strategy_lab.py
```

This is a separate historical research view; it does not change the fixed paper account or place orders. The default grid contains 1,030 configurations and caps each run at 100,000 bars to keep research responsive. The strategy grid is a discovery tool, not evidence of a durable edge. Searching many variants increases selection bias; candidates require fresh data, forward paper evaluation, and explicit risk review before any discussion of live deployment. MiroFish can be used to explore narrative scenarios that suggest hypotheses, but its agent simulations are not a substitute for historical execution data or calibrated market forecasts.

## 30-day forward paper experiment

After this change is merged, the scheduled worker seeds 1,030 separate hypothetical BTC/USDT Spot accounts at the latest completed 5-minute candle. Each starts with 1,000 USDT, uses 50% allocation and estimated 10 bps fees plus 5 bps slippage per side. The worker catches up missed completed candles, writes daily strategy scorecards to `data/strategy_paper_daily.csv`, and stops the experiment after 30 days. Current state is in `data/strategy_paper_state.json`.

This experiment is paper-only and does not place orders. GitHub Actions scheduling can be delayed; the worker catches up from public candles when it runs, and refuses to silently skip data older than its recovery window. A positive result over one month is not by itself proof of a durable edge or authorization to begin live trading.

## Multi-market public data foundation

The Phase 2 collector reads completed hourly candles for a bounded initial universe (BTC, ETH, BNB, SOL, XRP, ADA and DOGE). Spot data is sourced from Binance public market data; USDT perpetual swap candles are sourced from OKX's unauthenticated V5 public market-data endpoint. This is a data-source choice only, not a decision about where the user should open an account or execute trades.

The scheduled GitHub Actions workflow runs every four hours and stores a JSON snapshot as a seven-day artifact rather than committing market snapshots into Git history. Each snapshot records its provider, per-market failures and the explicit `live_trading_enabled: false` safety flag. A successful market-data scan is not evidence of a trading edge and does not enable order placement.

Volume fields preserve their source units: Binance Spot `volume` is base-asset quantity, while OKX swap `volume` is contract count. The collector also records `base_volume` and `quote_volume` (USDT) separately so cross-market volume comparisons can use a common quote-currency measure rather than mixing contracts and coins.

Official endpoint reference: [OKX V5 API – Candlesticks](https://www.okx.com/docs-v5/en/#rest-api-market-data-get-candlesticks).


## Multi-market technical analysis (research only)

The public market worker also writes `data/market_analysis_latest.json` alongside the raw candle snapshot. It calculates EMA20/EMA50 trend structure, RSI14, 24-hour momentum and ATR14 volatility independently for each Spot and perpetual-swap dataset, then compares the directional research bias for each matched symbol.

The score is an explainable heuristic, not a probability, forecast guarantee, or buy/sell instruction. Spot and perpetual results are kept separate; a disagreement is labelled as divergence rather than averaged away. Insufficient or malformed candle histories are reported as analysis failures. The report explicitly sets `live_trading_enabled: false` and contains no order instructions. Both the raw snapshot and analysis report are uploaded as a seven-day Actions artifact.


### Perpetual funding context

For each OKX USDT perpetual market, the collector separately requests the public current funding-rate endpoint. It records the current and next published rates, settlement timestamps, formula type and observation time when available. This is a live snapshot, not a historical funding series or a guarantee of the next settlement rate. Funding is exposed in the analysis report as context only and is deliberately excluded from the technical research score until a separately validated funding-aware model and historical dataset exist. If the funding endpoint fails, candle collection and technical analysis continue and the funding error is recorded.

The collector also retrieves up to 100 recent funding-history records per swap. Predicted `fundingRate` and actual `realizedRate` are kept as distinct fields and ordered by settlement time; only realized observations are suitable for a funding-cost replay. OKX documents up to three months of history, so this bounded sample is useful for initial research but is not a long-term funding archive. The current funding snapshot and the historical series have separate failure fields.

### Funding-cost replay (research only)

`funding_replay.py` replays actual `realized_rate` observations for a fixed USDT notional and a selected long or short side. Positive output is a net funding expense; negative output is a net funding credit. Predicted rates and records without a realized rate are excluded. This is deliberately not a complete futures backtest: it does not include price PnL, changing notional, trading fees, slippage, margin or liquidation. It cannot establish profitability or authorize live trading.

### Perpetual contract metadata

For each OKX USDT swap, the collector also reads public instrument metadata: contract type/value and value currency, settlement currency, contract lot increment, minimum contract size, tick size, exchange leverage ceiling, instrument state and any upcoming sizing-rule changes. Only linear USDT-settled swaps are accepted by this metadata parser. The exchange leverage ceiling is informational and is never used to configure leverage or open a position. Contract size fields are retained so a later paper engine can size in contracts rather than incorrectly treating contract counts as coin quantities.

### Mark-price history for risk research

The collector also requests up to 100 completed hourly mark-price candles for each perpetual swap. These are kept separate from the trade-price candles and are not substituted for them. Mark-price candles provide the reference series needed for a later, more realistic unrealized-PnL and liquidation-risk paper model; missing mark-price data is reported separately and must block any risk-sensitive simulation for that market. OKX limits this endpoint to 100 rows per request.


## Contract-aware Futures paper accounting (initial primitives)

`futures_contract_math.py` adds isolated, deterministic accounting primitives for OKX-style linear USDT-settled perpetual contracts. It derives per-contract USDT notional from the public contract value/currency and mark price, rounds target exposure down to the published lot increment, rejects sizes below the published minimum, computes linear long/short P&L, and estimates one-side taker fees. Mark-to-market uses the supplied mark price rather than substituting the trade candle close.

This is not yet a full Futures paper-trading worker. The functions fail closed for unsupported contract types, non-USDT settlement, missing sizing metadata, or instruments not marked live. The module does not choose leverage, calculate liquidation prices, model maintenance margin, or place orders. Entry and exit fees must be charged separately by a future ledger, and funding must be applied only at verified settlement timestamps to positions actually open at that time. No Futures paper account has been seeded by this change; no historical or live performance is claimed.


## Futures paper ledger (research-only replay)

`futures_paper_ledger.py` adds a deterministic, single-instrument linear USDT perpetual replay. It accepts a supplied long/short/flat target for each candle, fills at the trade-candle open with adverse fixed slippage, values open positions at the aligned mark-candle close, and applies fees on both entry and exit. It applies only realized funding rates at exact candle-open settlement timestamps, to the position carried into that timestamp; a position opened at the settlement timestamp is not charged or credited for that event.

The replay requires trade and mark candles to have identical timestamps and a regular interval. It refuses to approximate a funding settlement that does not align with a candle boundary. It caps notional exposure, rounds contract size down using public instrument metadata, tracks cash/equity/drawdown, and blocks new entries after configured daily-loss or drawdown limits. Any open position is closed at the end of the replay with the configured exit slippage and fee.

This is an accounting/replay module, not yet an automated strategy or scheduled paper account. Signals are inputs; this module does not generate them. The 1x label is a conservative research assumption, not a margin or liquidation simulation. Maintenance margin tiers, liquidation mechanics, exchange-specific fee schedules, variable slippage, intrabar paths and funding mark prices between candle boundaries are not modelled. The engine has no API credentials, account access, or order-placement capability; `live_trading_enabled` remains false.


## Futures strategy signal adapter (no-lookahead bridge)

`futures_signal_adapter.py` adapts the existing strategy engine's long/flat boolean output to the Futures replay's per-candle target-side input. It shifts every completed-candle signal forward by one row: the first candle is always flat, and a signal calculated from candle i's close can only become active at candle i+1's open. It validates timestamp ordering/uniqueness and finite positive close prices. The current strategy engine is long/flat only, so this adapter intentionally does not manufacture short signals.

The adapter is a small, testable bridge; it does not yet select or optimize a strategy, coordinate capital across symbols, or run a persistent Futures paper account. It must be paired with trade/mark/funding datasets on the same verified candle timeline. The Futures replay remains research-only and `live_trading_enabled` remains false.


## Multi-symbol Futures portfolio coordinator (ring-fenced v1)

`futures_portfolio_coordinator.py` coordinates several linear USDT perpetual paper replays under one starting-equity and gross-notional budget. It allocates the minimum valid contract-lot notional to each supplied symbol first, then distributes any remaining target and maximum notional using configurable weights and per-symbol concentration caps. Ring-fenced starting equity is assigned in proportion to each symbol's maximum-notional allocation, so no sleeve receives a notional cap above its own starting equity. This avoids assigning a sleeve less than an instrument's published minimum contract size. It validates all market timelines and instrument identities before replay, then delegates each allocation to the single-symbol ledger. Every symbol's trade and mark candles must match exactly and all symbols must share the same timestamps. The result aggregates per-symbol ledgers, portfolio equity, gross mark-notional exposure and portfolio drawdown.

Capital remains ring-fenced by symbol rather than cross-margined, but risk limits are now portfolio-wide. The coordinator runs a synchronized exploratory pass with sleeve-level loss guards neutralized, aggregates each completed candle's mark-to-market equity, and detects the first portfolio daily-loss or peak-to-trough drawdown breach. It then reruns the replay with a latched breaker: at the next candle open, every symbol is forced flat and all later entries are blocked. The coordinated exit is charged the normal adverse slippage and exit fee. If the breach occurs on the final candle, the normal end-of-replay close exits positions at that candle's close because no next candle exists. Daily loss resets at UTC day boundaries using the prior completed portfolio equity as the new day's baseline; drawdown remains measured from the portfolio's running peak. This is a completed-candle controller, not an intrabar or real-time stop, and it cannot guarantee a live exchange exit price.

The aggregate gross-notional cap is constrained to no more than starting equity (1x paper assumption). If combined minimum contract sizes exceed the configured portfolio target or cap, the replay fails closed rather than rounding exposure upward. Shared margin, cross-symbol netting, maintenance margin and liquidation remain unimplemented. This is research replay only; it does not create a scheduled paper account or enable live trading.


## Weighted portfolio sizing and concentration controls

`futures_portfolio_sizing.py` adds a minimum-lot-aware portfolio allocator. It reserves each market's minimum valid contract notional first, then distributes remaining target and maximum budgets using configurable positive weights. Weighted water-filling redistributes budget when a market reaches its configured concentration cap. The allocator fails closed if minimum lots exceed the budget, a market's minimum lot already breaches its concentration cap, the weighted budget cannot be fully allocated, or inputs are invalid. The coordinator accepts optional `allocation_weights` and `max_symbol_allocation_pct`; omitted weights preserve equal weighting, and the default concentration setting remains 100% for compatibility. This controls planned per-symbol sizing; it is not a dynamic cross-market exposure limiter during price gaps and does not replace the existing completed-candle portfolio loss/drawdown breaker. No live trading capability is added.


## Dynamic portfolio gross-exposure breaker

The portfolio coordinator now evaluates aggregate linear-contract notional using mark prices at both the candle open (after the supplied signal has been processed) and completed candle close. If a price gap or new position pushes combined gross notional above the configured portfolio cap at the open, the second replay pass forces every sleeve flat at that same candle open, applying normal adverse slippage and exit fees. If the cap is breached only at a completed close, all sleeves are flattened at the next candle open. The breaker latches for the remainder of the replay and blocks later entries. Exposure is measured from contract count, contract metadata and mark price—not from stale entry notional.

This is a synchronized candle-level emergency flatten, not partial portfolio rebalancing. It cannot react inside a candle, guarantee a real exchange fill, or model cross-margin, maintenance margin or liquidation. Missing or invalid mark prices continue to fail closed. The feature remains paper-replay only and does not enable live trading.


## Adversarial Futures portfolio stress checks

`tests/test_futures_portfolio_stress.py` adds deterministic adverse scenarios across multiple simultaneous sleeves: a correlated cross-asset price shock, simultaneous realized funding debits, and invalid mark-price reference data. The tests verify that portfolio loss controls coordinate exits across symbols and that invalid risk inputs stop the replay instead of being silently accepted. These tests validate accounting and safety behavior under selected scenarios; they do not establish strategy profitability, cover every market regime, or replace exchange-specific margin and liquidation simulation.


## Isolated Futures margin and liquidation estimate (research only)

`futures_margin_model.py` adds a standalone calculator for a single linear USDT perpetual position under an explicitly supplied isolated-margin amount, maintenance-margin rate, liquidation-fee reserve rate, and net accrued costs. It computes mark-to-market P&L, margin equity, maintenance requirement, remaining equity buffer, effective leverage, and an estimated liquidation threshold for long and short positions. Positive accrued costs reduce margin equity; funding credits can be represented as negative costs.

This is intentionally not wired into the existing 1x portfolio replay yet. It uses one constant maintenance rate supplied by the caller; it does not fetch or infer exchange maintenance tiers, and it does not simulate partial liquidation, cross margin, insurance funds, ADL, intrabar price paths, exchange-specific bankruptcy prices, or actual liquidation execution. The estimate is useful for controlled unit tests and scenario analysis only. Before integration or any leverage conclusions, the model must be calibrated against the selected exchange's official contract-specific margin and liquidation rules. Live trading remains disabled.


## Exchange selection

The current venue decision, Burkina Faso availability checks, source separation rules and safe rollout sequence are documented in [`EXCHANGE_SELECTION.md`](EXCHANGE_SELECTION.md). Binance is the primary candidate pending account-level KYC and product eligibility confirmation; OKX remains a read-only derivatives market-data source. Live trading remains disabled.
