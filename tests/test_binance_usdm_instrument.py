import pytest

from binance_usdm_instrument import (
    EXCHANGE_INFO_URL,
    fetch_usdm_instrument_metadata,
    parse_usdm_instrument_metadata,
)


def exchange_info(**overrides):
    item = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "contractType": "PERPETUAL",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "marginAsset": "USDT",
        "liquidationFee": "0.0125",
        "maintMarginPercent": "2.5000",
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0.10", "maxPrice": "1000000"},
            {"filterType": "LOT_SIZE", "minQty": "0.001", "maxQty": "1000", "stepSize": "0.001"},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0.001", "maxQty": "120", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "50"},
        ],
    }
    item.update(overrides)
    return {"symbols": [item]}


def test_normalizes_binance_quantity_as_base_asset_units():
    result = parse_usdm_instrument_metadata(exchange_info(), "BTCUSDT")
    assert result["instrument_type"] == "SWAP"
    assert result["contract_type"] == "linear"
    assert result["settlement_currency"] == "USDT"
    assert result["contract_value"] == "1"
    assert result["contract_value_currency"] == "BTC"
    assert result["contract_unit"] == "base_asset_quantity"
    assert result["instrument"] == "BTCUSDT"
    assert result["lot_size_contracts"] == "0.001"
    assert result["minimum_size_contracts"] == "0.001"
    assert result["maximum_size_contracts"] == "120"
    assert result["limit_order_lot_size"]["maximum_quantity"] == "1000"
    assert result["price_tick_size"] == "0.10"
    assert result["minimum_notional_usdt"] == "50"
    assert result["live_trading_enabled"] is False


def test_metadata_is_compatible_with_existing_linear_contract_math():
    from futures_contract_math import contract_notional_usdt, contracts_for_notional
    metadata = parse_usdm_instrument_metadata(exchange_info(), "BTCUSDT")
    assert contract_notional_usdt(metadata, 60_000) == pytest.approx(60_000)
    sized = contracts_for_notional(metadata, target_notional_usdt=1_000, mark_price=60_000)
    assert sized["contracts"] == pytest.approx(0.016)
    assert sized["notional_usdt"] == pytest.approx(960)


@pytest.mark.parametrize(
    "symbol,overrides,match",
    [
        ("btcusdt", {}, "uppercase"),
        ("BTCUSDT", {"status": "HALT"}, "TRADING"),
        ("BTCUSDT", {"contractType": "CURRENT_QUARTER"}, "PERPETUAL"),
        ("BTCUSDT", {"marginAsset": "BTC"}, "linear USDT"),
        ("BTCUSDT", {"quoteAsset": "BUSD"}, "linear USDT"),
        ("BTCUSDT", {"filters": []}, "missing required filters"),
    ],
)
def test_rejects_unsupported_or_incomplete_metadata(symbol, overrides, match):
    with pytest.raises(ValueError, match=match):
        parse_usdm_instrument_metadata(exchange_info(**overrides), symbol)


def test_rejects_invalid_filter_numbers():
    payload = exchange_info()
    payload["symbols"][0]["filters"][1]["stepSize"] = "nan"
    with pytest.raises(ValueError, match="finite and positive"):
        parse_usdm_instrument_metadata(payload, "BTCUSDT")


def test_fetch_uses_public_exchange_info_endpoint():
    class Response:
        status_code = 200
        headers = {}
        def raise_for_status(self):
            pass
        def json(self):
            return exchange_info()

    class Session:
        def __init__(self):
            self.headers = {}
            self.url = None
        def get(self, url, *, timeout):
            self.url = url
            return Response()

    session = Session()
    result = fetch_usdm_instrument_metadata("BTCUSDT", session=session)
    assert session.url == EXCHANGE_INFO_URL
    assert result["symbol"] == "BTCUSDT"
