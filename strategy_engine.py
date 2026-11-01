"""Deterministic strategy definitions and OHLCV simulation primitives."""
from __future__ import annotations

from itertools import product

import numpy as np
import pandas as pd


def strategy_grid() -> list[dict]:
    """Return a deterministic grid with >1,000 distinct configurations."""
    configs: list[dict] = []
    fasts = range(3, 31, 3)
    slows = range(20, 201, 10)
    for family in ("sma", "ema"):
        configs.extend({"family": family, "fast": f, "slow": s}
                       for f, s in product(fasts, slows) if f < s)
    configs.extend({"family": "momentum", "window": w, "threshold": t}
                   for w, t in product(
                       (3, 4, 6, 8, 10, 12, 16, 20, 24, 30, 40, 50, 60, 72, 90, 120),
                       (0.0005, 0.001, 0.0015, 0.002, 0.003, 0.005)))
    configs.extend({"family": "rsi", "window": w, "entry": entry, "exit": exit_}
                   for w, entry, exit_ in product(
                       (5, 6, 8, 10, 12, 14, 18, 22, 28, 35),
                       (15, 20, 25, 30, 35, 40),
                       (50, 55, 60, 65, 70, 75)))
    configs.extend({"family": "bollinger", "window": w, "deviations": d}
                   for w, d in product(
                       (8, 10, 12, 15, 18, 20, 25, 30, 40, 50, 60, 80),
                       (1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5)))
    configs.extend({"family": "donchian", "entry_window": entry, "exit_window": exit_}
                   for entry, exit_ in product(range(5, 76, 5), (5, 10, 15, 20, 30, 40, 50, 60)))
    return configs


def _state_machine(enter: np.ndarray, leave: np.ndarray) -> np.ndarray:
    state = np.zeros(len(enter), dtype=np.bool_)
    active = False
    for i in range(len(enter)):
        if not active and enter[i]:
            active = True
        elif active and leave[i]:
            active = False
        state[i] = active
    return state


def make_signal(close: pd.Series, cfg: dict) -> np.ndarray:
    family = cfg["family"]
    if family in ("sma", "ema"):
        if family == "sma":
            fast = close.rolling(cfg["fast"], min_periods=cfg["fast"]).mean()
            slow = close.rolling(cfg["slow"], min_periods=cfg["slow"]).mean()
        else:
            fast = close.ewm(span=cfg["fast"], min_periods=cfg["fast"], adjust=False).mean()
            slow = close.ewm(span=cfg["slow"], min_periods=cfg["slow"], adjust=False).mean()
        return (fast > slow).fillna(False).to_numpy(dtype=bool)
    if family == "momentum":
        momentum = close.pct_change(cfg["window"], fill_method=None)
        return (momentum > cfg["threshold"]).fillna(False).to_numpy(dtype=bool)
    if family == "rsi":
        delta = close.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / cfg["window"], min_periods=cfg["window"], adjust=False).mean()
        losses = (-delta.clip(upper=0)).ewm(alpha=1 / cfg["window"], min_periods=cfg["window"], adjust=False).mean()
        rs = gains / losses.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.mask(losses.eq(0) & gains.gt(0), 100.0)
        rsi = rsi.mask(losses.eq(0) & gains.eq(0), 50.0)
        enter = (rsi < cfg["entry"]).fillna(False).to_numpy(dtype=bool)
        leave = (rsi > cfg["exit"]).fillna(False).to_numpy(dtype=bool)
        return _state_machine(enter, leave)
    if family == "bollinger":
        mid = close.rolling(cfg["window"], min_periods=cfg["window"]).mean()
        std = close.rolling(cfg["window"], min_periods=cfg["window"]).std(ddof=0)
        lower = mid - cfg["deviations"] * std
        enter = (close < lower).fillna(False).to_numpy(dtype=bool)
        leave = (close >= mid).fillna(False).to_numpy(dtype=bool)
        return _state_machine(enter, leave)
    if family == "donchian":
        upper = close.rolling(cfg["entry_window"], min_periods=cfg["entry_window"]).max().shift(1)
        lower = close.rolling(cfg["exit_window"], min_periods=cfg["exit_window"]).min().shift(1)
        enter = (close > upper).fillna(False).to_numpy(dtype=bool)
        leave = (close < lower).fillna(False).to_numpy(dtype=bool)
        return _state_machine(enter, leave)
    raise ValueError(f"Unknown strategy family: {family}")


def simulate(frame: pd.DataFrame, signal: np.ndarray, allocation: float,
             fee_bps: float, slippage_bps: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return equity, per-bar net returns, and target exposure; fill at next bar open."""
    n = len(frame)
    target = np.zeros(n, dtype=np.float64)
    if n > 1:
        target[1:] = signal[:-1].astype(np.float64) * allocation
    prior = np.zeros(n, dtype=np.float64)
    if n > 1:
        prior[1:] = target[:-1]
    open_ = frame["open"].to_numpy(dtype=np.float64)
    close = frame["close"].to_numpy(dtype=np.float64)
    gap = np.zeros(n, dtype=np.float64)
    if n > 1:
        gap[1:] = open_[1:] / close[:-1] - 1
    intrabar = close / open_ - 1
    cost = (fee_bps + slippage_bps) / 10_000
    factors = (1 + prior * gap) * (1 - cost * np.abs(target - prior)) * (1 + target * intrabar)
    factors[0] = 1
    returns = factors - 1
    return np.cumprod(factors), returns, target


def metrics(equity: np.ndarray, returns: np.ndarray, target: np.ndarray,
            start: int, end: int, interval_seconds: float) -> dict:
    segment = equity[start:end]
    if len(segment) == 0:
        return {"return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe": 0.0, "position_changes": 0}
    base = equity[start - 1] if start else 1.0
    curve = segment / base
    peak = np.maximum.accumulate(np.r_[1.0, curve])
    dd = 1 - np.r_[1.0, curve] / peak
    r = returns[start:end]
    sd = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    periods = (365.25 * 24 * 3600 / interval_seconds) if interval_seconds > 0 else 1.0
    sharpe = float(np.mean(r) / sd * np.sqrt(periods)) if sd > 0 else 0.0
    prior_position = target[start - 1] if start else 0.0
    changes = np.count_nonzero(np.abs(np.diff(np.r_[prior_position, target[start:end]])) > 1e-9)
    return {"return_pct": float((curve[-1] - 1) * 100),
            "max_drawdown_pct": float(dd.max(initial=0) * 100),
            "sharpe": sharpe, "position_changes": int(changes)}


