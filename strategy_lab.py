"""Multi-strategy research lab with a 1,000+ trial default grid.

Run with: streamlit run strategy_lab.py
Signals are computed from completed bars and applied at the next bar open.
This is historical research only; no order execution is implemented.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import load_ohlcv
from market_data import fetch_ohlcv

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "experiments.sqlite3"


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


def persist_trials(rows: list[dict], data_hash: str, assumptions: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS strategy_trials (
            id INTEGER PRIMARY KEY, created_utc TEXT NOT NULL, data_sha256 TEXT NOT NULL,
            strategy_json TEXT NOT NULL, assumptions_json TEXT NOT NULL,
            train_json TEXT NOT NULL, validation_json TEXT NOT NULL)""")
        now = datetime.now(timezone.utc).isoformat()
        db.executemany("""INSERT INTO strategy_trials
            (created_utc,data_sha256,strategy_json,assumptions_json,train_json,validation_json)
            VALUES (?,?,?,?,?,?)""",
            [(now, data_hash, json.dumps(x["strategy"], sort_keys=True),
              json.dumps(assumptions, sort_keys=True), json.dumps(x["train"], sort_keys=True),
              json.dumps(x["validation"], sort_keys=True)) for x in rows])


def holdout_used(experiment_hash: str) -> bool:
    if not DB_PATH.exists():
        return False
    with sqlite3.connect(DB_PATH) as db:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategy_holdout_evaluations'"
        ).fetchone()
        if table is None:
            return False
        exists = db.execute(
            "SELECT 1 FROM strategy_holdout_evaluations WHERE experiment_hash=? LIMIT 1",
            (experiment_hash,),
        ).fetchone()
    return exists is not None


def persist_holdout(experiment_hash: str, data_hash: str, cfg: dict,
                    assumptions: dict, result: dict) -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS strategy_holdout_evaluations (
            experiment_hash TEXT PRIMARY KEY, created_utc TEXT NOT NULL,
            data_sha256 TEXT NOT NULL, strategy_json TEXT NOT NULL,
            assumptions_json TEXT NOT NULL, result_json TEXT NOT NULL)""")
        db.execute("""INSERT INTO strategy_holdout_evaluations
            (experiment_hash,created_utc,data_sha256,strategy_json,assumptions_json,result_json)
            VALUES (?,?,?,?,?,?)""",
            (experiment_hash, datetime.now(timezone.utc).isoformat(), data_hash,
             json.dumps(cfg, sort_keys=True), json.dumps(assumptions, sort_keys=True),
             json.dumps(result, sort_keys=True)))


def run_grid(frame: pd.DataFrame, configs: list[dict], allocation: float, fee_bps: float,
             slippage_bps: float, max_drawdown: float) -> pd.DataFrame:
    n = len(frame)
    train_end, validation_end = int(n * .60), int(n * .80)
    if train_end < 100 or validation_end >= n:
        raise ValueError("Need enough rows for a 60/20/20 chronological split.")
    interval = float(frame["timestamp"].diff().dt.total_seconds().median())
    close = frame["close"]
    output = []
    for cfg in configs:
        signal = make_signal(close, cfg)
        equity, returns, target = simulate(frame, signal, allocation, fee_bps, slippage_bps)
        output.append({
            "strategy": cfg,
            "train": metrics(equity, returns, target, 0, train_end, interval),
            "validation": metrics(equity, returns, target, train_end, validation_end, interval),
        })
    rows = []
    for item in output:
        v = item["validation"]
        rows.append({"strategy": json.dumps(item["strategy"], sort_keys=True),
                     "family": item["strategy"]["family"],
                     "train_return_pct": item["train"]["return_pct"],
                     "train_max_drawdown_pct": item["train"]["max_drawdown_pct"],
                     "validation_return_pct": v["return_pct"],
                     "validation_max_drawdown_pct": v["max_drawdown_pct"],
                     "validation_sharpe": v["sharpe"],
                     "validation_position_changes": v["position_changes"],
                     "passes_drawdown": v["max_drawdown_pct"] <= max_drawdown,
                     "_strategy": item["strategy"]})
    result = pd.DataFrame(rows).sort_values(
        ["passes_drawdown", "validation_sharpe", "validation_return_pct"],
        ascending=[False, False, False]).reset_index(drop=True)
    return result


st.set_page_config(page_title="Multi-Strategy Research Lab", page_icon="🧪", layout="wide")
st.title("Multi-Strategy Research Lab")
st.caption("Historical strategy search and paper research only · no live orders")

with st.sidebar:
    st.header("Market data")
    source = st.radio("Source", ["Binance public Spot", "Upload CSV"])
    if source == "Binance public Spot":
        symbol = st.text_input("Symbol", "BTCUSDT")
        interval = st.selectbox("Interval", ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"], index=2)
        days = st.select_slider("Lookback", options=[30, 60, 90, 180, 365], value=180)
        uploaded = None
    else:
        symbol = st.text_input("Dataset label", "BTCUSDT")
        interval, days = "CSV", 180
        uploaded = st.file_uploader("OHLCV CSV", type=["csv"])
    st.header("Simulation assumptions")
    initial = st.number_input("Starting equity (USDT)", min_value=10., max_value=10_000_000., value=1000., step=100.)
    allocation = st.slider("Spot allocation", .05, .95, .50, .05)
    fee_bps = st.number_input("Fee per side (bps)", 0., 500., 10., 1.)
    slippage_bps = st.number_input("Slippage per side (bps)", 0., 500., 5., 1.)
    max_dd = st.slider("Validation drawdown ceiling (%)", 2., 80., 25., 1.)

if source == "Binance public Spot":
    try:
        with st.spinner("Loading completed public candles…"):
            frame = fetch_ohlcv(symbol, interval, days)
        raw = frame.to_csv(index=False).encode()
    except Exception as exc:
        st.error(f"Could not load market data: {exc}")
        st.stop()
else:
    if uploaded is None:
        st.info("Upload a CSV to begin. Required columns: timestamp, open, high, low, close. At least 1,000 valid rows.")
        st.stop()
    raw = uploaded.getvalue()
    try:
        frame = load_ohlcv(raw)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()

frame = frame.copy()
frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
data_hash = hashlib.sha256(raw).hexdigest()
experiment_assumptions = {"symbol": symbol, "interval": interval, "initial_equity": initial,
                          "allocation": allocation, "fee_bps_per_side": fee_bps,
                          "slippage_bps_per_side": slippage_bps, "split": [0.6, 0.2, 0.2]}
experiment_hash = hashlib.sha256(
    (data_hash + json.dumps(experiment_assumptions, sort_keys=True)).encode("utf-8")
).hexdigest()
grid = strategy_grid()
st.metric("Distinct configurations in default grid", f"{len(grid):,}")
st.caption(f"{symbol} · {len(frame):,} bars · {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]} UTC · SHA-256 {data_hash[:16]}…")
st.warning("A large search makes lucky winners more likely. Validation ranks candidates; it does not establish a durable edge. Do not optimize for a target daily profit.")

if st.button(f"Run all {len(grid):,} configurations", type="primary"):
    try:
        with st.spinner(f"Evaluating {len(grid):,} fixed configurations…"):
            result = run_grid(frame, grid, allocation, fee_bps, slippage_bps, max_dd)
        assumptions = {**experiment_assumptions,
                       "ranking": "validation Sharpe then return, after validation drawdown filter"}
        persist_trials([{"strategy": r["_strategy"],
                         "train": {"return_pct": r["train_return_pct"], "max_drawdown_pct": r["train_max_drawdown_pct"]},
                         "validation": {"return_pct": r["validation_return_pct"],
                                        "max_drawdown_pct": r["validation_max_drawdown_pct"],
                                        "sharpe": r["validation_sharpe"],
                                        "position_changes": r["validation_position_changes"]}}
                        for _, r in result.iterrows()], data_hash, assumptions)
        st.session_state["grid_result"] = result
        st.session_state["grid_hash"] = data_hash
    except Exception as exc:
        st.error(f"Search failed: {exc}")

result = st.session_state.get("grid_result")
if result is not None and st.session_state.get("grid_hash") != data_hash:
    st.warning("Data changed. Run the full search again before selecting a candidate.")
    result = None
if result is not None:
    st.success(f"Saved {len(result):,} distinct trials. Every result includes data hash and assumptions in the local experiment database.")
    st.dataframe(result.drop(columns=["_strategy"]), use_container_width=True, hide_index=True)
    eligible = result[result["passes_drawdown"]].head(50)
    if eligible.empty:
        st.info("No candidate passed the selected validation drawdown ceiling.")
    else:
        selection = st.selectbox("Freeze a candidate for the untouched holdout",
                                 eligible.index.tolist(),
                                 format_func=lambda i: f"{result.loc[i, 'family']} · {result.loc[i, 'strategy']}")
        already_evaluated = holdout_used(experiment_hash)
        if already_evaluated:
            st.warning("The holdout for this data and these assumptions has already been used. Start a new experiment with genuinely new data for another final evaluation.")
        if st.button("Evaluate selected candidate on holdout", disabled=already_evaluated):
            cfg = result.loc[selection, "_strategy"]
            signal = make_signal(frame["close"], cfg)
            equity, returns, target = simulate(frame, signal, allocation, fee_bps, slippage_bps)
            split = int(len(frame) * .80)
            interval_seconds = float(frame["timestamp"].diff().dt.total_seconds().median())
            holdout = metrics(equity, returns, target, split, len(frame), interval_seconds)
            persist_holdout(experiment_hash, data_hash, cfg, experiment_assumptions, holdout)
            st.session_state["holdout_result"] = {"cfg": cfg, "metrics": holdout, "data_hash": data_hash, "equity": equity.copy()}
    holdout = st.session_state.get("holdout_result")
    if holdout and holdout["data_hash"] == data_hash:
        st.subheader("Frozen holdout result")
        st.write(holdout["cfg"])
        st.json(holdout["metrics"])
        chart = go.Figure(go.Scatter(x=frame["timestamp"], y=holdout["equity"] * initial, name="Strategy equity"))
        chart.update_layout(height=360, xaxis_title="UTC", yaxis_title="Simulated equity (USDT)")
        st.plotly_chart(chart, use_container_width=True)
        st.caption("This holdout is now consumed. Using it to change the strategy turns it into development data.")
