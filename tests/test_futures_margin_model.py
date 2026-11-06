import pytest

from futures_margin_model import assess_isolated_margin


def btc_metadata(**overrides):
    data = {
        "instrument_type": "SWAP",
        "contract_type": "linear",
        "settlement_currency": "USDT",
        "instrument_state": "live",
        "contract_value": 0.01,
        "contract_value_currency": "BTC",
        "contract_multiplier": "1",
        "lot_size_contracts": 1,
        "minimum_size_contracts": 1,
    }
    data.update(overrides)
    return data


def assess(**overrides):
    args = {
        "metadata": btc_metadata(),
        "contracts": 1,
        "entry_price": 60_000,
        "mark_price": 60_000,
        "side": "long",
        "isolated_margin_usdt": 60,
        "maintenance_margin_rate": 0.005,
        "liquidation_fee_rate": 0.0005,
        "accrued_costs_usdt": 0.6,
    }
    args.update(overrides)
    return assess_isolated_margin(**args)


def test_healthy_long_reports_margin_buffer_and_liquidation_estimate():
    result = assess()
    assert result["entry_notional_usdt"] == pytest.approx(600)
    assert result["effective_leverage"] == pytest.approx(10)
    assert result["margin_equity_usdt"] == pytest.approx(59.4)
    assert result["maintenance_margin_usdt"] == pytest.approx(3)
    assert result["estimated_liquidation_fee_reserve_usdt"] == pytest.approx(0.3)
    assert result["equity_buffer_usdt"] == pytest.approx(56.1)
    assert result["estimated_liquidation_price"] == pytest.approx(
        (600 - 60 + 0.6) / (0.01 * (1 - 0.005 - 0.0005))
    )
    assert result["liquidation_threshold_breached"] is False
    assert result["live_trading_enabled"] is False


def test_long_position_breaches_threshold_after_adverse_mark_move():
    result = assess(mark_price=54_000)
    assert result["unrealized_pnl_usdt"] == pytest.approx(-60)
    assert result["liquidation_threshold_breached"] is True
    assert result["equity_buffer_usdt"] < 0


def test_short_position_liquidation_estimate_is_above_entry():
    result = assess(side="short")
    expected = (600 + 60 - 0.6) / (0.01 * (1 + 0.005 + 0.0005))
    assert result["estimated_liquidation_price"] == pytest.approx(expected)
    assert result["estimated_liquidation_price"] > 60_000


def test_short_position_breaches_after_adverse_rally():
    result = assess(side="short", mark_price=66_000)
    assert result["unrealized_pnl_usdt"] == pytest.approx(-60)
    assert result["liquidation_threshold_breached"] is True


def test_funding_debit_reduces_margin_buffer_and_credit_increases_it():
    debit = assess(accrued_costs_usdt=5.6)
    credit = assess(accrued_costs_usdt=-4.4)
    assert debit["equity_buffer_usdt"] < assess()["equity_buffer_usdt"]
    assert credit["equity_buffer_usdt"] > assess()["equity_buffer_usdt"]


def test_rejects_invalid_rates_margin_and_position_inputs():
    with pytest.raises(ValueError, match="less than 1"):
        assess(maintenance_margin_rate=1)
    with pytest.raises(ValueError, match="must be less than 1"):
        assess(liquidation_fee_rate=1)
    with pytest.raises(ValueError, match="less than 1"):
        assess(maintenance_margin_rate=0.8, liquidation_fee_rate=0.3)
    with pytest.raises(ValueError, match="cannot exceed position notional"):
        assess(isolated_margin_usdt=700)
    with pytest.raises(ValueError, match="side must be"):
        assess(side="flat")
    with pytest.raises(ValueError, match="finite"):
        assess(accrued_costs_usdt=float("nan"))


def test_rejects_unsupported_contract_metadata():
    with pytest.raises(ValueError, match="only linear"):
        assess(metadata=btc_metadata(contract_type="inverse"))
