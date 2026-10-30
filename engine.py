"""Vectorized, long-only OHLCV backtest engine for the research dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from itertools import product

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("open", "high", "low", "close")


@dataclass(frozen=True)
class Split:
    train_end: int
    validation_end: int
    size: int


def digest_bytes(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def load_ohlcv(raw: bytes) -> pd.DataFrame:
    """Load and validate CSV bytes. Timestamps are normalized to UTC."""
    try:
        frame = pd.read_csv(BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc
    frame.columns = [str(c).strip().lower() for c in frame.columns]

    time_column = next((c for c in ("timestamp", "datetime", "date", "time") if c in frame.columns), None)
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if time_column is None or missing:
        needed = "timestamp, open, high, low, close"
        raise ValueError(f"CSV must include {needed}; missing: {', '.join(missing) or 'timestamp'}")

    values = frame[time_column]
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().mean() > 0.95:
        unit = "ms" if numeric.dropna().median() > 100_000_000_000 else "s"
        timestamp = pd.to_datetime(numeric, unit=unit, utc=True, errors="coerce")
    else:
        timestamp = pd.to_datetime(values, utc=True, errors="coerce")
    frame["timestamp"] = timestamp
    for column in REQUIRED_COLUMNS + (("volume",) if "volume" in frame.columns else ()):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    selected = ["timestamp", *REQUIRED_COLUMNS]
    if "volume" in frame.columns:
        selected.append("volume")
    frame = frame[selected]
    invalid = frame.isna().any(axis=1)
    if invalid.any():
        raise ValueError(f"Found {int(invalid.sum()):,} rows with invalid timestamps or OHLCV values. Correct or remove them explicitly before analysis.")
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    if len(frame) < 1_000:
        raise ValueError(f"Need at least 1,000 valid bars for a minimally useful three-way time split; found {len(frame):,}")
    if frame["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps found. Resolve duplicates before backtesting.")
    if (frame[list(REQUIRED_COLUMNS)] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (frame["high"] < frame[["open", "close"]].max(axis=1)).any():
        raise ValueError("At least one high is below its open or close.")
    if (frame["low"] > frame[["open", "close"]].min(axis=1)).any():
        raise ValueError("At least one low is above its open or close.")
    if "volume" in frame and (frame["volume"] < 0).any():
        raise ValueError("Volume cannot be negative.")
    if not np.isfinite(frame[list(REQUIRED_COLUMNS)].to_numpy(dtype=np.float64)).all():
        raise ValueError("OHLC prices must be finite values.")
    if "volume" in frame and not np.isfinite(frame["volume"].to_numpy(dtype=np.float64)).all():
        raise ValueError("Volume must be finite values.")
    return frame


def infer_interval(frame: pd.DataFrame) -> tuple[str, int]:
    seconds = frame["timestamp"].diff().dt.total_seconds().dropna()
    median = int(seconds.median())
    if median <= 0:
        return "irregular", median
    if (seconds != median).any():
        return f"irregular (median {median}s)", median
    if median % 3600 == 0:
        label = f"{median // 3600}h"
    elif median % 60 == 0:
        label = f"{median // 60}m"
    else:
        label = f"{median}s"
    return label, median


def split_indices(size: int, train_fraction: float = 0.60, validation_fraction: float = 0.20) -> Split:
    if not 0.5 <= train_fraction <= 0.8 or not 0.1 <= validation_fraction <= 0.3:
        raise ValueError("Use train 50–80% and validation 10–30%.")
    train_end = int(size * train_fraction)
    validation_end = int(size * (train_fraction + validation_fraction))
    if validation_end >= size or train_end < 100:
        raise ValueError("The selected split leaves too little data in one segment.")
    return Split(train_end, validation_end, size)


def _sma(values: np.ndarray, window: int) -> np.ndarray:
    cumulative = np.concatenate(([0.0], np.cumsum(values, dtype=np.float64)))
    result = np.full(values.size, np.nan, dtype=np.float64)
    if window <= values.size:
        result[window - 1:] = (cumulative[window:] - cumulative[:-window]) / window
    return result


def _segment_stats(equity: np.ndarray, positions: np.ndarray, start: int, end: int, initial: float) -> dict[str, float | int]:
    base = initial if start == 0 else equity[start - 1]
    curve = equity[start:end] / base
    if curve.size == 0:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0, "position_changes": 0}
    peaks = np.maximum.accumulate(np.concatenate(([1.0], curve)))
    drawdown = 1.0 - np.concatenate(([1.0], curve)) / peaks
    prior = positions[start - 1] if start else 0.0
    changes = int(np.count_nonzero(np.abs(np.diff(np.concatenate(([prior], positions[start:end])))) > 1e-9))
    return {
        "return_pct": float((curve[-1] - 1.0) * 100.0),
        "max_drawdown_pct": float(drawdown.max(initial=0.0) * 100.0),
        "position_changes": changes,
    }


def _simulate_batch(
    signals: np.ndarray,
    opens: np.ndarray,
    closes: np.ndarray,
    initial: float,
    allocation: float,
    cost_rate: float,
) -> np.ndarray:
    """Mark-to-market by bar: old exposure earns the gap; new exposure earns open-to-close."""
    batch, size = signals.shape
    positions = np.zeros((batch, size), dtype=np.float32)
    if size > 1:
        positions[:, 1:] = signals[:, :-1] * allocation
    prior_positions = np.zeros_like(positions)
    if size > 1:
        prior_positions[:, 1:] = positions[:, :-1]

    gaps = np.zeros(size, dtype=np.float64)
    intraday = np.zeros(size, dtype=np.float64)
    gaps[1:] = opens[1:] / closes[:-1] - 1.0
    intraday[:] = closes / opens - 1.0

    factors = np.ones((batch, size), dtype=np.float64)
    if size > 1:
        factors[:, 1:] = (
            (1.0 + prior_positions[:, 1:] * gaps[None, 1:])
            * (1.0 - cost_rate * np.abs(positions[:, 1:] - prior_positions[:, 1:]))
            * (1.0 + positions[:, 1:] * intraday[None, 1:])
        )
    return np.cumprod(factors, axis=1) * initial


def run_sweep(
    frame: pd.DataFrame,
    fast_windows: list[int],
    slow_windows: list[int],
    initial: float = 1_000.0,
    allocation: float = 0.50,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    max_drawdown_pct: float = 25.0,
    train_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> tuple[pd.DataFrame, Split]:
    if initial <= 0 or not 0 < allocation <= 0.95:
        raise ValueError("Initial capital must be positive; allocation must be in (0, 95%].")
    if min(fee_bps, slippage_bps) < 0 or fee_bps + slippage_bps >= 2_000:
        raise ValueError("Fee and slippage must be non-negative and sum to less than 20% per side.")
    if max_drawdown_pct <= 0:
        raise ValueError("Maximum drawdown limit must be positive.")

    pairs = [(f, s) for f, s in product(sorted(set(fast_windows)), sorted(set(slow_windows))) if 1 <= f < s]
    if not pairs:
        raise ValueError("No valid parameter pairs: every fast window must be smaller than a slow window.")
    if len(pairs) > 600:
        raise ValueError(f"Sweep contains {len(pairs)} combinations. Limit this run to 600 or fewer.")

    close = frame["close"].to_numpy(dtype=np.float64)
    open_ = frame["open"].to_numpy(dtype=np.float64)
    split = split_indices(len(frame), train_fraction, validation_fraction)
    windows = sorted({w for pair in pairs for w in pair})
    averages = {w: _sma(close, w) for w in windows}
    cost_rate = (fee_bps + slippage_bps) / 10_000.0
    records: list[dict[str, float | int | bool]] = []

    # Small batches bound memory while letting NumPy process each bar path in compiled code.
    # Keep large histories bounded in memory; each batch creates several B x bars arrays.
    batch_size = max(1, min(48, 1_000_000 // len(frame)))
    for offset in range(0, len(pairs), batch_size):
        current = pairs[offset:offset + batch_size]
        # Do not simulate or calculate the holdout during model selection.
        signals = np.vstack([averages[fast][:split.validation_end] > averages[slow][:split.validation_end] for fast, slow in current])
        equity = _simulate_batch(
            signals, open_[:split.validation_end], close[:split.validation_end], initial, allocation, cost_rate,
        )
        positions = np.zeros_like(signals, dtype=np.float32)
        positions[:, 1:] = signals[:, :-1] * allocation
        for row, (fast, slow) in enumerate(current):
            train = _segment_stats(equity[row], positions[row], 0, split.train_end, initial)
            validation = _segment_stats(equity[row], positions[row], split.train_end, split.validation_end, initial)
            records.append({
                "fast": fast,
                "slow": slow,
                "train_return_pct": train["return_pct"],
                "train_max_drawdown_pct": train["max_drawdown_pct"],
                "train_position_changes": train["position_changes"],
                "validation_return_pct": validation["return_pct"],
                "validation_max_drawdown_pct": validation["max_drawdown_pct"],
                "validation_position_changes": validation["position_changes"],
                "passes_drawdown_limit": validation["max_drawdown_pct"] <= max_drawdown_pct,
            })
    result = pd.DataFrame.from_records(records)
    result = result.sort_values(
        ["passes_drawdown_limit", "validation_return_pct", "validation_max_drawdown_pct"],
        ascending=[False, False, True],
    ).reset_index(drop=True)
    return result, split


def evaluate_holdout(
    frame: pd.DataFrame,
    fast: int,
    slow: int,
    split: Split,
    initial: float,
    allocation: float,
    fee_bps: float,
    slippage_bps: float,
) -> tuple[dict[str, float | int], pd.DataFrame]:
    close = frame["close"].to_numpy(dtype=np.float64)
    open_ = frame["open"].to_numpy(dtype=np.float64)
    start = split.validation_end
    context_index = start - 1
    full_signal = _sma(close, fast) > _sma(close, slow)
    # Reset to cash for the holdout, but retain the signal computed from historical context.
    signal = np.concatenate((full_signal[context_index:context_index + 1], full_signal[start:]))[None, :]
    context_close = close[context_index]
    sim_open = np.concatenate(([context_close], open_[start:]))
    sim_close = np.concatenate(([context_close], close[start:]))
    cost_rate = (fee_bps + slippage_bps) / 10_000.0
    equity = _simulate_batch(signal, sim_open, sim_close, initial, allocation, cost_rate)[0]
    positions = np.zeros(len(signal[0]), dtype=np.float32)
    positions[1:] = signal[0, :-1] * allocation
    holdout_start, holdout_end = 1, len(equity)
    metrics = _segment_stats(equity, positions, holdout_start, holdout_end, initial)
    # Close any remaining spot position at the last bar and include that estimated cost.
    if positions[-1] > 0:
        equity[-1] *= 1 - cost_rate * allocation
        metrics = _segment_stats(equity, positions, holdout_start, holdout_end, initial)
        metrics["position_changes"] = int(metrics["position_changes"]) + 1
    metrics["final_equity"] = float(equity[-1])

    chart = frame.iloc[context_index:].copy()
    chart["strategy_equity"] = equity
    benchmark_curve = np.full(len(chart), initial, dtype=np.float64)
    if len(chart) > 1:
        benchmark_curve[1:] = initial * (
            1 - allocation + allocation * chart["close"].iloc[1:].to_numpy(dtype=np.float64) / open_[start]
        )
        benchmark_curve[1:] *= 1 - allocation * cost_rate
    if benchmark_curve.size:
        benchmark_curve[-1] *= 1 - allocation * cost_rate
    chart["buy_hold_equity"] = benchmark_curve
    baseline_return = float((benchmark_curve[-1] / initial - 1.0) * 100.0)
    metrics["buy_hold_return_pct"] = baseline_return
    return metrics, chart


def record_experiment(database_path: str, entry: dict) -> None:
    import sqlite3
    import json
    from pathlib import Path

    Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS holdout_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_utc TEXT NOT NULL,
                data_sha256 TEXT NOT NULL,
                fast INTEGER NOT NULL,
                slow INTEGER NOT NULL,
                assumptions_json TEXT NOT NULL,
                results_json TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO holdout_runs(created_utc,data_sha256,fast,slow,assumptions_json,results_json) VALUES(?,?,?,?,?,?)",
            (
                pd.Timestamp.now(tz="UTC").isoformat(),
                entry["data_sha256"],
                int(entry["fast"]),
                int(entry["slow"]),
                json.dumps(entry["assumptions"], sort_keys=True),
                json.dumps(entry["results"], sort_keys=True),
            ),
        )
        connection.commit()


def load_experiments(database_path: str) -> pd.DataFrame:
    import sqlite3
    from pathlib import Path

    if not Path(database_path).exists():
        return pd.DataFrame()
    with sqlite3.connect(database_path) as connection:
        return pd.read_sql_query(
            "SELECT id, created_utc, data_sha256, fast, slow, assumptions_json, results_json FROM holdout_runs ORDER BY id DESC",
            connection,
        )
