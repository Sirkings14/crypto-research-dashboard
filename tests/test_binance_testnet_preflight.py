import pytest

from binance_testnet_preflight import PreflightError, market_order_preflight


def info(**overrides):
    symbol = {
        "symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
        "contractType": "PERPETUAL", "orderTypes": ["LIMIT", "MARKET"],
        "filters": [
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "5"},
        ],
    }
    symbol.update(overrides)
    return {"symbols": [symbol]}


def test_calculates_step_aligned_quantity_under_cap():
    result = market_order_preflight(info(), "60000", "BTCUSDT")
    assert result["quantity"] == "0.000"
    assert result["estimated_notional_usdt"] == "0.000"


def test_rejects_non_trading_symbol():
    with pytest.raises(PreflightError, match="not TRADING"):
        market_order_preflight(info(status="BREAK"), "60000", "BTCUSDT")


def test_rejects_wrong_contract_or_quote_asset():
    with pytest.raises(PreflightError, match="USDT perpetual"):
        market_order_preflight(info(contractType="CURRENT_QUARTER"), "60000", "BTCUSDT")


def test_rejects_missing_market_order_permission():
    with pytest.raises(PreflightError, match="MARKET orders"):
        market_order_preflight(info(orderTypes=["LIMIT"]), "60000", "BTCUSDT")


def test_rejects_missing_filters():
    with pytest.raises(PreflightError, match="filters"):
        market_order_preflight(info(filters=[]), "60000", "BTCUSDT")


def test_rejects_price_and_cap_errors():
    with pytest.raises(PreflightError, match="ticker price"):
        market_order_preflight(info(), "NaN", "BTCUSDT")
    with pytest.raises(PreflightError, match="cannot exceed"):
        market_order_preflight(info(), "60000", "BTCUSDT", max_notional_usdt="26")


def test_rejects_minimum_notional_above_cap():
    with pytest.raises(PreflightError, match="minimum notional"):
        market_order_preflight(info(filters=[
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"},
        ]), "1000", "BTCUSDT")


def test_refuses_if_minimum_quantity_cannot_fit():
    with pytest.raises(PreflightError, match="minimum quantity"):
        market_order_preflight(info(filters=[
            {"filterType": "LOT_SIZE", "stepSize": "1", "minQty": "1", "maxQty": "100"},
        ]), "60000", "BTCUSDT")
