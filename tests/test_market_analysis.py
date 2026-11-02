from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market_analysis import analyze_dataset, analyze_snapshot


def make_dataset(symbol="BTCUSDT", market="spot", trend=1.0, count=100):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(count):
        close = 100.0 + i * trend
        candles.append({
            "timestamp": (start + timedelta(hours=i)).isoformat(),
            "open": close - 0.2, "high": close + 1.0,
            "low": close - 1.0, "close": close, "volume": 10.0,
        })
    return {"market": market, "provider": "test", "symbol": symbol,
            "interval": "1h", "candles": candles}


def test_uptrend_scores_bullish_but_never_an_order():
    result = analyze_dataset(make_dataset())
    assert result["research_bias"] == "bullish"
    assert result["research_score"] >= 2
    assert result["signal_is_probability"] is False
    assert result["order_instruction"] is None


def test_downtrend_scores_bearish():
    result = analyze_dataset(make_dataset(trend=-0.5))
    assert result["research_bias"] == "bearish"
    assert result["research_score"] <= -2


def test_insufficient_history_is_rejected():
    with pytest.raises(ValueError, match="at least 60"):
        analyze_dataset(make_dataset(count=59))


def test_duplicate_or_reversed_timestamps_are_rejected():
    dataset = make_dataset()
    dataset["candles"][20]["timestamp"] = dataset["candles"][19]["timestamp"]
    with pytest.raises(ValueError, match="strictly chronological"):
        analyze_dataset(dataset)


def test_invalid_ohlc_is_rejected():
    dataset = make_dataset()
    dataset["candles"][-1]["high"] = dataset["candles"][-1]["close"] - 1
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        analyze_dataset(dataset)


def test_snapshot_compares_spot_and_swap_without_combining_scores():
    snapshot = {"fetched_at": "2026-01-01T00:00:00+00:00", "results": [
        make_dataset(market="spot", trend=1.0),
        make_dataset(market="futures", trend=-0.5),
    ]}
    result = analyze_snapshot(snapshot)
    assert result["dataset_count"] == 2
    assert result["spot_futures_comparison"] == [{
        "symbol": "BTCUSDT", "spot_bias": "bullish",
        "futures_bias": "bearish", "alignment": "divergent",
    }]
    assert result["live_trading_enabled"] is False
