"""Advance a durable, long-only paper account using completed public candles."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from market_data import fetch_ohlcv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STATE_PATH = DATA_DIR / "paper_state.json"
HISTORY_PATH = DATA_DIR / "paper_history.csv"
SYMBOL = "BTCUSDT"
INTERVAL = "5m"
FAST = 10
SLOW = 40
INITIAL_EQUITY = 1_000.0
ALLOCATION = 0.50
FEE_BPS = 10.0
SLIPPAGE_BPS = 5.0
HISTORY_COLUMNS = [
    "bar_open_utc", "bar_close_utc", "open", "high", "low", "close", "target_position",
    "position", "equity_usdt", "return_pct", "drawdown_pct", "estimated_cost_usdt", "action",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def write_state(state: dict) -> None:
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE_PATH)


def initialize(candles: pd.DataFrame) -> None:
    latest = candles.iloc[-1]
    started = utc_now()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not HISTORY_PATH.exists():
        with HISTORY_PATH.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(HISTORY_COLUMNS)
    write_state({
        "status": "running", "market": "BTC/USDT", "symbol": SYMBOL, "interval": INTERVAL,
        "strategy": f"SMA crossover {FAST}/{SLOW}", "fast_window": FAST, "slow_window": SLOW,
        "initial_equity_usdt": INITIAL_EQUITY, "equity_usdt": INITIAL_EQUITY,
        "allocation": ALLOCATION, "fee_bps_per_side": FEE_BPS, "slippage_bps_per_side": SLIPPAGE_BPS,
        "position": 0.0, "peak_equity_usdt": INITIAL_EQUITY, "max_drawdown_pct": 0.0,
        "estimated_costs_usdt": 0.0, "processed_bars": 0,
        "last_bar_open_utc": pd.Timestamp(latest["timestamp"]).isoformat(),
        "last_close": float(latest["close"]), "started_utc": started.isoformat(),
        "last_updated_utc": started.isoformat(),
        "note": "Paper account seeded at the latest completed candle; no historical P&L is included.",
    })


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candles = fetch_ohlcv(SYMBOL, INTERVAL, 2)
    if len(candles) < SLOW + 3:
        raise RuntimeError(f"Need at least {SLOW + 3} completed candles; got {len(candles)}.")
    if not STATE_PATH.exists():
        initialize(candles)
        print("Initialized paper account at the latest completed candle; next run begins accumulation.")
        return

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    last_time = pd.Timestamp(state["last_bar_open_utc"])
    if last_time.tzinfo is None:
        last_time = last_time.tz_localize("UTC")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
    if last_time < candles["timestamp"].iloc[0]:
        candles = fetch_ohlcv(SYMBOL, INTERVAL, 30)
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True)
        if last_time < candles["timestamp"].iloc[0]:
            raise RuntimeError("Paper state is older than the available 30-day catch-up window; refusing to skip bars silently.")

    close = candles["close"].astype(float)
    crossed_up = close.rolling(FAST, min_periods=FAST).mean() > close.rolling(SLOW, min_periods=SLOW).mean()
    target_signal = crossed_up.shift(1).fillna(False)
    candles["target_position"] = target_signal.astype(float) * ALLOCATION
    new_bars = candles[candles["timestamp"] > last_time]
    if new_bars.empty:
        print("No newly completed candles. Paper ledger is current.")
        return

    equity = float(state["equity_usdt"])
    position = float(state["position"])
    previous_close = float(state["last_close"])
    peak = float(state["peak_equity_usdt"])
    max_drawdown = float(state["max_drawdown_pct"])
    cumulative_costs = float(state["estimated_costs_usdt"])
    cost_rate = (FEE_BPS + SLIPPAGE_BPS) / 10_000.0
    rows: list[dict] = []

    for row in new_bars.itertuples(index=False):
        target = float(row.target_position)
        gap_return = float(row.open) / previous_close - 1.0
        intrabar_return = float(row.close) / float(row.open) - 1.0
        turnover = abs(target - position)
        before_cost = equity * (1.0 + position * gap_return)
        estimated_cost = before_cost * cost_rate * turnover
        equity = before_cost * (1.0 - cost_rate * turnover) * (1.0 + target * intrabar_return)
        cumulative_costs += estimated_cost
        if target > position:
            action = "ENTER_LONG"
        elif target < position:
            action = "EXIT_TO_CASH"
        else:
            action = "HOLD_LONG" if target > 0 else "HOLD_CASH"
        peak = max(peak, equity)
        drawdown = (1.0 - equity / peak) * 100.0 if peak else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        bar_open = pd.Timestamp(row.timestamp)
        bar_close = bar_open + timedelta(minutes=5)
        rows.append({
            "bar_open_utc": bar_open.isoformat(), "bar_close_utc": bar_close.isoformat(),
            "open": float(row.open), "high": float(row.high), "low": float(row.low),
            "close": float(row.close), "target_position": target, "position": target,
            "equity_usdt": round(equity, 8), "return_pct": round((equity / INITIAL_EQUITY - 1.0) * 100.0, 8),
            "drawdown_pct": round(drawdown, 8), "estimated_cost_usdt": round(estimated_cost, 8),
            "action": action,
        })
        position = target
        previous_close = float(row.close)

    had_history = HISTORY_PATH.exists()
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS)
        if not had_history:
            writer.writeheader()
        writer.writerows(rows)

    state.update({
        "status": "running", "equity_usdt": round(equity, 8), "position": position,
        "peak_equity_usdt": round(peak, 8), "max_drawdown_pct": round(max_drawdown, 8),
        "estimated_costs_usdt": round(cumulative_costs, 8),
        "processed_bars": int(state["processed_bars"]) + len(rows),
        "last_bar_open_utc": pd.Timestamp(new_bars.iloc[-1]["timestamp"]).isoformat(),
        "last_close": previous_close, "last_updated_utc": utc_now().isoformat(),
        "last_action": rows[-1]["action"],
    })
    write_state(state)
    print(f"Processed {len(rows)} completed bars through {state['last_bar_open_utc']}; paper equity {equity:.2f} USDT.")


if __name__ == "__main__":
    main()
