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
