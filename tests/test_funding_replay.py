from __future__ import annotations

import pytest

from funding_replay import replay_funding_cost


def records():
    return [
        {"funding_time": "2026-01-01T00:00:00+00:00", "realized_rate": 0.001, "predicted_rate": 0.002},
        {"funding_time": "2026-01-01T08:00:00+00:00", "realized_rate": -0.0005, "predicted_rate": 0.003},
        {"funding_time": "2026-01-01T16:00:00+00:00", "realized_rate": None, "predicted_rate": 0.004},
    ]


def test_long_pays_positive_and_receives_negative_funding():
    result = replay_funding_cost(records(), notional_usdt=1000, side="long")
    assert result["settlement_count"] == 2
    assert result["excluded_unrealized_records"] == 1
    assert result["gross_funding_expenses_usdt"] == pytest.approx(1.0)
    assert result["gross_funding_credits_usdt"] == pytest.approx(0.5)
    assert result["net_funding_cost_usdt"] == pytest.approx(0.5)
    assert result["live_trading_enabled"] is False


def test_short_has_opposite_funding_cashflows():
    result = replay_funding_cost(records()[:2], notional_usdt=1000, side="short")
    assert result["gross_funding_expenses_usdt"] == pytest.approx(0.5)
    assert result["gross_funding_credits_usdt"] == pytest.approx(1.0)
    assert result["net_funding_cost_usdt"] == pytest.approx(-0.5)


@pytest.mark.parametrize("notional", [0, -1, float("nan"), float("inf")])
def test_invalid_notional_is_rejected(notional):
    with pytest.raises(ValueError, match="finite positive"):
        replay_funding_cost([], notional_usdt=notional, side="long")


def test_predicted_rate_is_never_used_without_realized_rate():
    result = replay_funding_cost(
        [{"funding_time": "2026-01-01T00:00:00+00:00", "realized_rate": None, "predicted_rate": 0.05}],
        notional_usdt=1000, side="long",
    )
    assert result["settlement_count"] == 0
    assert result["excluded_unrealized_records"] == 1
    assert result["net_funding_cost_usdt"] == 0


def test_duplicate_or_naive_timestamps_are_rejected():
    bad = [
        {"funding_time": "2026-01-01T00:00:00+00:00", "realized_rate": 0.001},
        {"funding_time": "2026-01-01T00:00:00+00:00", "realized_rate": 0.001},
    ]
    with pytest.raises(ValueError, match="strictly chronological"):
        replay_funding_cost(bad, notional_usdt=1000, side="long")
    with pytest.raises(ValueError, match="timezone"):
        replay_funding_cost(
            [{"funding_time": "2026-01-01T00:00:00", "realized_rate": 0.001}],
            notional_usdt=1000, side="long",
        )
