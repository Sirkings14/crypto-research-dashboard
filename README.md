# Crypto Research Lab

Research dashboard for historical OHLCV analysis. Default baseline: BTC/USDT Spot, 5-minute bars, UTC.

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
4. Use the public-data paper replay to watch a rolling 30-day simulated account refresh from completed candles while the page is open. This replay resets to the configured starting capital and is not a persistent portfolio.

## CSV format

Required columns: `timestamp, open, high, low, close`; `volume` is optional. Timestamps may be ISO strings or Unix seconds/milliseconds. At least 1,000 valid, unique rows are required. Use UTC timestamps for comparable results.

## Model and limits

- Long-only, spot-like position; no leverage, short selling, credentials, exchange account connection, or order placement.
- Signals use completed candles and apply at the next candle open. Fee and slippage settings are estimates.
- Train/validation/holdout split is chronological at 60/20/20. Repeatedly using holdout results to tune the model invalidates it as an independent final check.
- The model does not include order-book depth, market impact, latency, funding, taxes, delistings, or exchange outages. Historical and simulated results do not predict future performance.
- Codespaces is an interactive environment. Stopping or closing it pauses the app and paper refreshes; experiment records live in its filesystem and should be exported/backed up if needed.
