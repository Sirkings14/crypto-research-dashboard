# Project instructions

- This is a research and paper-simulation project. Do not add live order placement unless the user explicitly changes scope.
- Do not claim or imply profitability from historical results.
- Backtests must avoid lookahead: a signal from a completed bar cannot fill on that same bar.
- Report trading costs, drawdown, trade count, and out-of-sample results alongside return.
- Preserve chronological train/validation/holdout splits. Never shuffle market time series.
- Keep exchange credentials and user account data out of source control and logs.
- Prefer deterministic calculations and record the data hash and assumptions for each experiment.
