# Crypto Research Dashboard

Research tool for accelerated, reproducible backtests and, later, live paper simulation. This first milestone is offline: it accepts OHLCV CSV data and never connects to an exchange or places orders.

## Baseline market

- Market: BTC/USDT
- Bar size: 5 minutes
- Timestamp convention: UTC
- Initial strategy family: long/flat simple moving-average crossover
- Execution assumption: signal from a completed candle is acted on at the next candle's open
- Position constraint: spot-like, long only, no leverage

BTC/USDT is a useful first benchmark because it is generally one of the deepest crypto markets and has broad data coverage. Five-minute bars provide enough intraday observations for vectorized parameter sweeps while remaining less sensitive to tick-level noise. The market choice is a research baseline, not a profit claim. Exchange-specific symbols and data availability still need to be verified before selecting a data feed.

## Run locally

Python 3.11 or newer is recommended.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

The repository includes a `.devcontainer` definition for a repeatable Python 3.11 Codespace. Create a Codespace from the repository, let its setup install the requirements, then run `python -m streamlit run app.py` in the Codespaces terminal. The dashboard port is 8501.

Upload an OHLCV CSV with columns `timestamp,open,high,low,close` and optional `volume`. Timestamps should be ISO-8601 or Unix seconds/milliseconds. Data must be ordered consistently; the loader sorts rows, rejects duplicate timestamps and invalid rows, and validates OHLC prices. The first version expects 5-minute data; it reports the observed interval so mismatched data is visible. There is not yet an automatic market-data downloader; until the chosen public data source is confirmed, use an appropriately sourced CSV and label its market and source in the sidebar.

## Research workflow

1. Import and inspect the data, coverage, interval, and gaps.
2. Sweep SMA parameters on the training segment; use the validation segment to select a candidate subject to a drawdown ceiling.
3. Freeze the candidate, then explicitly run the holdout evaluation. Each holdout run is recorded in a local SQLite experiment log with the data hash and assumptions.
4. Treat any repeated holdout inspection as a new model-selection decision. The holdout stops being independent once it influences further tuning.

Reported P&L is net of the configured per-side fee and slippage estimates. The model is intentionally simple: bar-based fills, fixed allocation, and no leverage. It does not yet model order-book depth, market impact, latency, funding, taxes, delistings, or exchange outages. Results are hypothetical and are not a forecast.

## Repository safety

Never commit `.env` files, credentials, account exports, or private trading data. Keep this repository private while strategy research is in progress. No exchange API credentials are required for this milestone.

## Next milestones

1. Robust data ingestion and integrity diagnostics.
2. Vectorized sweep with train/validation/holdout separation and experiment audit trail.
3. Public market-data adapter and event-driven real-time paper trading.
4. Execution realism checks and monitoring; live order placement is outside the current scope.

