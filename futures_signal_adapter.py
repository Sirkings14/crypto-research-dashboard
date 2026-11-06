"""Convert completed-candle strategy outputs into next-open Futures targets.

The existing strategy engine emits long/flat boolean states. This adapter
shifts those states by one candle so a target at candle i's open can only use
information from candle i-1's completed close. It deliberately does not
invent short signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_engine import make_signal


def make_futures_target_sides(frame: pd.DataFrame, config: dict) -> list[str]:
    """Return one long/flat target per candle, shifted to the next candle open.

    The first candle is always flat because no prior completed candle exists.
    The function rejects unordered/duplicate timestamps and invalid closes.
    """
    required = {"timestamp", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"frame is missing required columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("frame must contain at least one candle")

    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    if timestamps.isna().any():
        raise ValueError("timestamps must be valid")
    if timestamps.duplicated().any() or not timestamps.is_monotonic_increasing:
        raise ValueError("timestamps must be unique and strictly increasing")

    close = pd.to_numeric(frame["close"], errors="coerce")
    values = close.to_numpy(dtype=np.float64)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("close prices must be finite and positive")

    signal = np.asarray(make_signal(close, config), dtype=bool)
    if signal.ndim != 1 or len(signal) != len(frame):
        raise ValueError("strategy engine returned an invalid signal shape")

    targets = np.zeros(len(signal), dtype=bool)
    if len(signal) > 1:
        targets[1:] = signal[:-1]
    return ["long" if active else "flat" for active in targets]
