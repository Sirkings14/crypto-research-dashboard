import numpy as np
import pandas as pd

from strategy_engine import make_signal, metrics, simulate, strategy_grid


def sample_frame():
    timestamps = pd.date_range("2025-01-01", periods=5, freq="5min", tz="UTC")
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": [100.0, 100.0, 100.0, 101.0, 99.0],
        "high": [100.0, 110.0, 101.0, 102.0, 100.0],
        "low": [100.0, 100.0, 99.0, 98.0, 97.0],
        "close": [100.0, 110.0, 100.0, 99.0, 98.0],
    })


def test_default_grid_has_at_least_one_thousand_unique_configurations():
    configs = strategy_grid()
    keys = [tuple(sorted(cfg.items())) for cfg in configs]
    assert len(configs) >= 1000
    assert len(keys) == len(set(keys))


def test_signal_does_not_change_when_future_bars_are_appended():
    close = pd.Series([100 + (i % 7) - i * 0.03 for i in range(120)], dtype=float)
    configs = [
        {"family": "sma", "fast": 3, "slow": 8},
        {"family": "ema", "fast": 3, "slow": 8},
        {"family": "momentum", "window": 6, "threshold": 0.001},
        {"family": "rsi", "window": 8, "entry": 30, "exit": 60},
        {"family": "bollinger", "window": 10, "deviations": 2.0},
        {"family": "donchian", "entry_window": 10, "exit_window": 5},
    ]
    for cfg in configs:
        full = make_signal(close, cfg)
        prefix = make_signal(close.iloc[:80], cfg)
        np.testing.assert_array_equal(prefix, full[:80])


def test_signal_is_filled_at_next_bar_and_costs_are_charged():
    frame = sample_frame().iloc[:3].copy()
    signal = np.array([False, True, False])
    equity, returns, target = simulate(frame, signal, allocation=0.5,
                                       fee_bps=10.0, slippage_bps=5.0)
    assert target[1] == 0.0
    assert target[2] == 0.5
    assert returns[1] == 0.0
    assert equity[1] == 1.0
    assert equity[2] < equity[1]


def test_metrics_are_finite_and_count_position_changes():
    equity = np.array([1.0, 1.1, 1.0, 1.2])
    returns = np.array([0.0, 0.1, -1 / 11, 0.2])
    target = np.array([0.0, 0.5, 0.5, 0.0])
    result = metrics(equity, returns, target, 0, 4, 300.0)
    assert np.isfinite(result["sharpe"])
    assert result["position_changes"] == 2
    assert result["max_drawdown_pct"] > 0


def test_forward_worker_seeds_all_accounts_without_historical_pnl(tmp_path, monkeypatch):
    import paper_portfolio_worker as worker

    monkeypatch.setattr(worker, "DATA_DIR", tmp_path)
    monkeypatch.setattr(worker, "STATE_PATH", tmp_path / "strategy_paper_state.json")
    monkeypatch.setattr(worker, "DAILY_PATH", tmp_path / "strategy_paper_daily.csv")
    timestamps = pd.date_range("2025-01-01", periods=250, freq="5min", tz="UTC")
    candles = pd.DataFrame({"timestamp": timestamps, "open": 100.0, "high": 101.0,
                            "low": 99.0, "close": 100.0})
    state = worker.initialize(candles)
    assert state["status"] == "running"
    assert state["strategy_count"] == 1030
    assert len(state["accounts"]) == 1030
    assert all(a["equity_usdt"] == worker.INITIAL_EQUITY for a in state["accounts"].values())
    assert all(a["position"] == 0.0 for a in state["accounts"].values())
    assert worker.STATE_PATH.exists()
    assert worker.DAILY_PATH.exists()
