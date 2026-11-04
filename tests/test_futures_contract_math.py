import pytest

from futures_contract_math import (
    contract_notional_usdt,
    contracts_for_notional,
    estimated_taker_fee_usdt,
    linear_pnl_usdt,
    mark_to_market_position,
)


def metadata(**overrides):
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


def test_base_currency_contract_notional_uses_mark_price():
    assert contract_notional_usdt(metadata(), 60_000) == pytest.approx(600)


def test_contract_sizing_rounds_down_to_lot():
    result = contracts_for_notional(
        metadata(), target_notional_usdt=1_000, mark_price=60_000
    )
    assert result["contracts"] == 1
    assert result["notional_usdt"] == pytest.approx(600)
    assert result["notional_rounding_residual_usdt"] == pytest.approx(400)


def test_target_below_minimum_is_rejected():
    with pytest.raises(ValueError, match="below the instrument minimum"):
        contracts_for_notional(metadata(), target_notional_usdt=500, mark_price=60_000)


def test_linear_base_contract_long_and_short_pnl():
    assert linear_pnl_usdt(
        metadata(), contracts=2, entry_price=60_000, mark_price=61_000, side="long"
    ) == pytest.approx(20)
    assert linear_pnl_usdt(
        metadata(), contracts=2, entry_price=60_000, mark_price=61_000, side="short"
    ) == pytest.approx(-20)


def test_quote_currency_contract_pnl_uses_return_fraction():
    quote = metadata(contract_value=100, contract_value_currency="USDT")
    assert contract_notional_usdt(quote, 60_000) == pytest.approx(100)
    assert linear_pnl_usdt(
        quote, contracts=2, entry_price=60_000, mark_price=63_000, side="long"
    ) == pytest.approx(10)


def test_contract_size_must_match_lot_increment():
    with pytest.raises(ValueError, match="exact multiple"):
        linear_pnl_usdt(
            metadata(), contracts=1.5, entry_price=60_000, mark_price=61_000, side="long"
        )


def test_rejects_inverse_or_non_usdt_contracts():
    with pytest.raises(ValueError, match="only linear"):
        contract_notional_usdt(metadata(contract_type="inverse"), 60_000)
    with pytest.raises(ValueError, match="only USDT-settled"):
        contract_notional_usdt(metadata(settlement_currency="BTC"), 60_000)


def test_fee_is_one_side_and_finite():
    assert estimated_taker_fee_usdt(1_000, 5) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        estimated_taker_fee_usdt(1_000, float("nan"))


def test_mark_to_market_includes_estimated_exit_fee():
    result = mark_to_market_position(
        metadata(), contracts=2, entry_price=60_000, mark_price=61_000,
        side="long", taker_fee_bps=5,
    )
    assert result["gross_unrealized_pnl_usdt"] == pytest.approx(20)
    assert result["estimated_exit_taker_fee_usdt"] == pytest.approx(0.61)
    assert result["estimated_unrealized_after_exit_fee_usdt"] == pytest.approx(19.39)
    assert result["live_trading_enabled"] is False


def test_rejects_non_live_or_missing_metadata():
    with pytest.raises(ValueError, match="not marked live"):
        contract_notional_usdt(metadata(instrument_state="suspend"), 60_000)
    with pytest.raises(ValueError, match="contract_value_currency"):
        contract_notional_usdt(metadata(contract_value_currency=None), 60_000)



def test_exchange_minimum_notional_is_enforced_when_provided():
    with pytest.raises(ValueError, match="minimum notional"):
        contracts_for_notional(
            metadata(minimum_notional_usdt="100"),
            target_notional_usdt=60,
            mark_price=60_000,
        )


def test_rounded_quantity_must_still_meet_exchange_minimum_notional():
    with pytest.raises(ValueError, match="sized notional.*minimum notional"):
        contracts_for_notional(
            metadata(minimum_notional_usdt="650"),
            target_notional_usdt=1_000,
            mark_price=60_000,
        )


def test_exchange_maximum_quantity_is_enforced_when_provided():
    with pytest.raises(ValueError, match="maximum quantity"):
        contracts_for_notional(
            metadata(maximum_size_contracts="0.01"),
            target_notional_usdt=1_000,
            mark_price=60_000,
        )
