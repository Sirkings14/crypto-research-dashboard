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
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import load_ohlcv
from market_data import fetch_ohlcv

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "experiments.sqlite3"
MAX_GRID_BARS = 100_000


from strategy_engine import metrics, make_signal, simulate, strategy_grid


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
    interval_seconds_map = {"1m": 60, "3m": 180, "5m": 300, "15m": 900,
                            "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400}
    estimated_bars = days * 86400 / interval_seconds_map[interval]
    if estimated_bars > MAX_GRID_BARS:
        st.error(f"This search is capped at {MAX_GRID_BARS:,} bars for a responsive 1,000+ strategy run. Reduce lookback or use a slower interval.")
        st.stop()
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

if len(frame) > MAX_GRID_BARS:
    st.error(f"This search is capped at {MAX_GRID_BARS:,} bars for a responsive 1,000+ strategy run. Upload a smaller sample.")
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
        st.session_state["grid_hash"] = experiment_hash
    except Exception as exc:
        st.error(f"Search failed: {exc}")

result = st.session_state.get("grid_result")
if result is not None and st.session_state.get("grid_hash") != experiment_hash:
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
            st.session_state["holdout_result"] = {"cfg": cfg, "metrics": holdout, "data_hash": data_hash, "experiment_hash": experiment_hash, "equity": equity.copy()}
    holdout = st.session_state.get("holdout_result")
    if holdout and holdout.get("experiment_hash") == experiment_hash:
        st.subheader("Frozen holdout result")
        st.write(holdout["cfg"])
        st.json(holdout["metrics"])
        chart = go.Figure(go.Scatter(x=frame["timestamp"], y=holdout["equity"] * initial, name="Strategy equity"))
        chart.update_layout(height=360, xaxis_title="UTC", yaxis_title="Simulated equity (USDT)")
        st.plotly_chart(chart, use_container_width=True)
        st.caption("This holdout is now consumed. Using it to change the strategy turns it into development data.")
