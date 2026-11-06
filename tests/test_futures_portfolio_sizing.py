import pytest

from futures_portfolio_sizing import allocate_portfolio_notional


def test_weighted_allocation_reserves_minimum_lots_and_respects_caps():
    result = allocate_portfolio_notional(
        minimum_notionals_usdt={"BTCUSDT": 100, "ETHUSDT": 50, "SOLUSDT": 25},
        target_total_notional_usdt=600,
        max_total_notional_usdt=750,
        weights={"BTCUSDT": 2, "ETHUSDT": 1, "SOLUSDT": 1},
        max_symbol_allocation_pct=50,
    )
    target = result["target_allocations_usdt"]
    maximum = result["maximum_allocations_usdt"]
    assert sum(target.values()) == pytest.approx(600)
    assert sum(maximum.values()) == pytest.approx(750)
    assert target["BTCUSDT"] > target["ETHUSDT"]
    assert all(value >= {"BTCUSDT": 100, "ETHUSDT": 50, "SOLUSDT": 25}[s]
               for s, value in target.items())
    assert all(value <= 300 + 1e-8 for value in target.values())
    assert all(value <= 375 + 1e-8 for value in maximum.values())
    assert result["live_trading_enabled"] is False


def test_equal_weights_produce_equal_remainder_after_minimums():
    result = allocate_portfolio_notional(
        minimum_notionals_usdt={"BTCUSDT": 100, "ETHUSDT": 100},
        target_total_notional_usdt=400,
        max_total_notional_usdt=500,
        max_symbol_allocation_pct=50,
    )
    assert result["target_allocations_usdt"] == pytest.approx(
        {"BTCUSDT": 200, "ETHUSDT": 200}
    )


def test_weighted_water_filling_redistributes_when_symbol_saturates():
    result = allocate_portfolio_notional(
        minimum_notionals_usdt={"AUSDT": 10, "BUSDT": 10, "CUSDT": 10},
        target_total_notional_usdt=90,
        max_total_notional_usdt=120,
        weights={"AUSDT": 10, "BUSDT": 1, "CUSDT": 1},
        max_symbol_allocation_pct=40,
    )
    allocation = result["target_allocations_usdt"]
    assert allocation["AUSDT"] == pytest.approx(36)
    assert allocation["BUSDT"] == pytest.approx(27)
    assert allocation["CUSDT"] == pytest.approx(27)


@pytest.mark.parametrize("kwargs", [
    {"minimum_notionals_usdt": {}, "target_total_notional_usdt": 100, "max_total_notional_usdt": 100},
    {"minimum_notionals_usdt": {"BTCUSDT": 60}, "target_total_notional_usdt": 50, "max_total_notional_usdt": 100},
    {"minimum_notionals_usdt": {"BTCUSDT": 10}, "target_total_notional_usdt": 100, "max_total_notional_usdt": 90},
    {"minimum_notionals_usdt": {"BTCUSDT": 10}, "target_total_notional_usdt": 100, "max_total_notional_usdt": 100, "max_symbol_allocation_pct": 40},
])
def test_rejects_invalid_or_unallocatable_budgets(kwargs):
    with pytest.raises(ValueError):
        allocate_portfolio_notional(**kwargs)


def test_rejects_missing_or_non_positive_weights():
    base = {
        "minimum_notionals_usdt": {"BTCUSDT": 10, "ETHUSDT": 10},
        "target_total_notional_usdt": 100,
        "max_total_notional_usdt": 100,
    }
    with pytest.raises(ValueError, match="exactly"):
        allocate_portfolio_notional(**base, weights={"BTCUSDT": 1})
    with pytest.raises(ValueError, match="weight"):
        allocate_portfolio_notional(**base, weights={"BTCUSDT": 1, "ETHUSDT": 0})
