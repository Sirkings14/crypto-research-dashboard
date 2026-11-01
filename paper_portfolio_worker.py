"""Advance 1,030 independent spot-only paper accounts on completed BTCUSDT candles."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pandas as pd

from market_data import fetch_ohlcv
from strategy_engine import make_signal, strategy_grid

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "strategy_paper_state.json"
DAILY_PATH = DATA_DIR / "strategy_paper_daily.csv"
SYMBOL, INTERVAL = "BTCUSDT", "5m"
LOOKBACK_DAYS = 30
RUN_DAYS = 30
INITIAL_EQUITY = 1_000.0
ALLOCATION = 0.50
FEE_BPS, SLIPPAGE_BPS = 10.0, 5.0
COST_RATE = (FEE_BPS + SLIPPAGE_BPS) / 10_000.0
DAILY_FIELDS = [
    "date_utc", "strategy_id", "family", "strategy_json", "equity_usdt",
    "return_pct", "max_drawdown_pct", "position", "trade_count", "estimated_costs_usdt",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def strategy_id(config: dict) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()[:16]


def write_state(state: dict) -> None:
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def ensure_daily_file() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DAILY_PATH.exists():
        with DAILY_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(DAILY_FIELDS)


def record_daily_snapshot(date_utc: str, state: dict) -> None:
    ensure_daily_file()
    rows = []
    for sid, account in state["accounts"].items():
        rows.append({
            "date_utc": date_utc, "strategy_id": sid, "family": account["config"]["family"],
            "strategy_json": json.dumps(account["config"], sort_keys=True),
            "equity_usdt": round(account["equity_usdt"], 8),
            "return_pct": round((account["equity_usdt"] / INITIAL_EQUITY - 1) * 100, 8),
            "max_drawdown_pct": round(account["max_drawdown_pct"], 8),
            "position": account["position"], "trade_count": account["trade_count"],
            "estimated_costs_usdt": round(account["estimated_costs_usdt"], 8),
        })
    with DAILY_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_FIELDS)
        writer.writerows(rows)


def initialize(candles: pd.DataFrame) -> dict:
    started = utc_now()
    latest = candles.iloc[-1]
    configs = strategy_grid()
    accounts = {}
    for config in configs:
        sid = strategy_id(config)
        accounts[sid] = {
            "config": config, "equity_usdt": INITIAL_EQUITY, "position": 0.0,
            "peak_equity_usdt": INITIAL_EQUITY, "max_drawdown_pct": 0.0,
            "trade_count": 0, "estimated_costs_usdt": 0.0,
        }
    state = {
        "status": "running", "symbol": SYMBOL, "interval": INTERVAL,
        "strategy_count": len(configs), "started_utc": started.isoformat(),
        "expires_utc": (started + timedelta(days=RUN_DAYS)).isoformat(),
        "last_updated_utc": started.isoformat(),
        "last_bar_open_utc": pd.Timestamp(latest["timestamp"]).isoformat(),
        "last_close": float(latest["close"]),
        "last_snapshot_date": pd.Timestamp(latest["timestamp"]).date().isoformat(),
        "initial_equity_usdt_per_strategy": INITIAL_EQUITY,
        "allocation": ALLOCATION, "fee_bps_per_side": FEE_BPS,
        "slippage_bps_per_side": SLIPPAGE_BPS,
        "note": "Seeded at the latest completed candle. No historical P&L is included.",
        "accounts": accounts,
    }
    ensure_daily_file()
    write_state(state)
    return state


def finalize(state: dict, at: datetime) -> None:
    last_day = pd.Timestamp(state["last_bar_open_utc"]).date().isoformat()
    if state.get("last_snapshot_date") != last_day:
        record_daily_snapshot(last_day, state)
        state["last_snapshot_date"] = last_day
    state.update({"status": "complete", "completed_utc": at.isoformat(),
                  "last_updated_utc": at.isoformat()})
    write_state(state)


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candles = fetch_ohlcv(SYMBOL, INTERVAL, LOOKBACK_DAYS)
    if len(candles) < 200:
        raise RuntimeError(f"Need at least 200 completed candles; got {len(candles)}.")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
    if not STATE_PATH.exists():
        state = initialize(candles)
        print(f"Seeded {state['strategy_count']} paper accounts at {state['last_bar_open_utc']}.")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if state.get("status") == "complete":
        print("The 30-day forward paper experiment is complete.")
        return
    last_time = pd.Timestamp(state["last_bar_open_utc"])
    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    if last_time < candles["timestamp"].iloc[0]:
        raise RuntimeError("Paper state is older than the 30-day candle recovery window; refusing to skip bars.")
    expiry = pd.Timestamp(state["expires_utc"])
    if expiry.tzinfo is None:
        expiry = expiry.tz_localize("UTC")

    eligible = candles[
        (candles["timestamp"] > last_time)
        & (candles["timestamp"] + pd.Timedelta(minutes=5) <= expiry)
    ].reset_index(drop=True)
    if eligible.empty:
        if utc_now() >= expiry.to_pydatetime():
            finalize(state, utc_now())
            print("The 30-day forward paper experiment is complete.")
        else:
            print("No newly completed candles.")
        return

    configs = strategy_grid()
    signal_by_id = {}
    for config in configs:
        signal_by_id[strategy_id(config)] = make_signal(candles["close"], config)
    index_by_time = {ts: i for i, ts in enumerate(candles["timestamp"])}
    prior_close = float(state["last_close"])
    accounts = state["accounts"]
    processed = 0
    for bar in eligible.itertuples(index=False):
        timestamp = pd.Timestamp(bar.timestamp)
        current_date = timestamp.date().isoformat()
        snapshot_date = state.get("last_snapshot_date", current_date)
        if current_date > snapshot_date:
            record_daily_snapshot(snapshot_date, state)
            state["last_snapshot_date"] = current_date
        idx = index_by_time[timestamp]
        previous_idx = idx - 1
        gap_return = float(bar.open) / prior_close - 1.0
        intrabar_return = float(bar.close) / float(bar.open) - 1.0
        for sid, account in accounts.items():
            signal = signal_by_id[sid]
            target = ALLOCATION if bool(signal[previous_idx]) else 0.0
            position = float(account["position"])
            turnover = abs(target - position)
            before_cost = float(account["equity_usdt"]) * (1.0 + position * gap_return)
            estimated_cost = before_cost * COST_RATE * turnover
            equity = before_cost * (1.0 - COST_RATE * turnover) * (1.0 + target * intrabar_return)
            if turnover > 1e-9:
                account["trade_count"] += 1
            account["estimated_costs_usdt"] += estimated_cost
            account["equity_usdt"] = equity
            account["position"] = target
            account["peak_equity_usdt"] = max(float(account["peak_equity_usdt"]), equity)
            drawdown = (1.0 - equity / account["peak_equity_usdt"]) * 100.0
            account["max_drawdown_pct"] = max(float(account["max_drawdown_pct"]), drawdown)
        prior_close = float(bar.close)
        state["last_bar_open_utc"] = timestamp.isoformat()
        state["last_close"] = prior_close
        processed += 1

    now = utc_now()
    state["last_updated_utc"] = now.isoformat()
    if now >= expiry.to_pydatetime():
        finalize(state, now)
    else:
        write_state(state)
    print(f"Processed {processed} completed candles across {len(accounts)} paper accounts through {state['last_bar_open_utc']}.")


if __name__ == "__main__":
    main()
