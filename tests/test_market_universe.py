from __future__ import annotations

import pytest

from market_universe import (
    FUTURES_FUNDING_HISTORY_URL,
    FUTURES_INSTRUMENTS_URL,
    MARK_PRICE_CANDLES_URL,
    FUTURES_FUNDING_URL,
    FUTURES_KLINES_URL,
    SPOT_KLINES_URL,
    fetch_completed_candles,
    fetch_completed_mark_price_candles,
    fetch_current_funding_rate,
    fetch_funding_rate_history,
    fetch_swap_instrument_metadata,
)


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.params = None
        self.headers = {}

    def get(self, url, *, params, timeout):
        self.url = url
        self.params = params
        return FakeResponse(self.payload)


def binance_candle(open_ms: int, close_ms: int, *, close: str = "101"):
    return [open_ms, "100", "105", "99", close, "12.5", close_ms, "1250"]


def okx_candle(open_ms: int, *, close: str = "101", confirmed: str = "1"):
    return [str(open_ms), "100", "105", "99", close, "12.5", "1.25", "1250", confirmed]


def test_binance_spot_uses_correct_endpoint_and_filters_open_candle():
    session = FakeSession([
        binance_candle(1_700_000_000_000, 1_700_000_299_999),
        binance_candle(1_700_000_300_000, 1_700_000_599_999),
        binance_candle(1_700_000_600_000, 1_700_000_899_999),
    ])
    result = fetch_completed_candles(
        "spot", "BTCUSDT", "5m", 3, session=session, now_ms=1_700_000_800_000,
    )
    assert session.url == SPOT_KLINES_URL
    assert session.params["symbol"] == "BTCUSDT"
    assert len(result) == 2
    assert result[-1]["close"] == 101.0
    assert result[-1]["volume_unit"] == "base_asset"
    assert result[-1]["quote_volume"] == 1250.0


def test_okx_usdt_swap_uses_public_endpoint_and_normalizes_reverse_order():
    session = FakeSession({
        "code": "0",
        "msg": "",
        "data": [
            okx_candle(1_700_003_600_000),
            okx_candle(1_700_000_000_000),
        ],
    })
    result = fetch_completed_candles(
        "futures", "BTCUSDT", "1h", 2, session=session, now_ms=1_700_008_000_000,
    )
    assert session.url == FUTURES_KLINES_URL
    assert session.params["instId"] == "BTC-USDT-SWAP"
    assert session.params["bar"] == "1H"
    assert len(result) == 2
    assert result[0]["timestamp"] < result[1]["timestamp"]
    assert result[-1]["volume_unit"] == "contracts"
    assert result[-1]["base_volume"] == 1.25
    assert result[-1]["quote_volume"] == 1250.0


def test_okx_uses_confirm_flag_to_exclude_unclosed_candle():
    session = FakeSession({
        "code": "0",
        "data": [
            okx_candle(1_700_003_600_000, confirmed="0"),
            okx_candle(1_700_000_000_000, confirmed="1"),
        ],
    })
    result = fetch_completed_candles(
        "futures", "BTCUSDT", "1h", 2, session=session, now_ms=1_800_000_000_000,
    )
    assert len(result) == 1


def test_rejects_non_usdt_symbol():
    with pytest.raises(ValueError, match="USDT-quoted"):
        fetch_completed_candles("spot", "BTCUSD", session=FakeSession([]))


def test_rejects_out_of_order_spot_candles():
    session = FakeSession([
        binance_candle(1_700_000_300_000, 1_700_000_599_999),
        binance_candle(1_700_000_000_000, 1_700_000_299_999),
    ])
    with pytest.raises(ValueError, match="chronological"):
        fetch_completed_candles(
            "spot", "BTCUSDT", session=session, now_ms=1_800_000_000_000
        )


def test_rejects_inconsistent_ohlc():
    session = FakeSession([
        [1_700_000_000_000, "100", "100", "99", "101", "1", 1_700_000_299_999]
    ])
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        fetch_completed_candles(
            "spot", "BTCUSDT", session=session, now_ms=1_800_000_000_000
        )


def test_rejects_okx_api_error():
    session = FakeSession({"code": "51001", "msg": "Instrument ID does not exist", "data": []})
    with pytest.raises(ValueError, match="API error 51001"):
        fetch_completed_candles(
            "futures", "BTCUSDT", session=session, now_ms=1_800_000_000_000
        )



def test_okx_public_funding_rate_is_parsed_as_rate_not_probability():
    session = FakeSession({
        "code": "0",
        "msg": "",
        "data": [{
            "instId": "BTC-USDT-SWAP",
            "fundingRate": "0.0001",
            "nextFundingRate": "0.0002",
            "fundingTime": "1700000000000",
            "nextFundingTime": "1700028800000",
            "ts": "1699999900000",
            "formulaType": "withRate",
            "settState": "settled",
            "method": "current_period",
        }],
    })
    result = fetch_current_funding_rate("BTCUSDT", session=session)
    assert session.url == FUTURES_FUNDING_URL
    assert session.params == {"instId": "BTC-USDT-SWAP"}
    assert result["funding_rate"] == pytest.approx(0.0001)
    assert result["funding_rate_pct"] == pytest.approx(0.01)
    assert result["next_funding_rate_pct"] == pytest.approx(0.02)
    assert result["formula_type"] == "withRate"
    assert result["is_historical"] is False


def test_okx_funding_rejects_malformed_rate():
    session = FakeSession({
        "code": "0",
        "data": [{"fundingRate": "not-a-number", "fundingTime": "1700000000000"}],
    })
    with pytest.raises(ValueError, match="invalid funding fields"):
        fetch_current_funding_rate("BTCUSDT", session=session)



def test_okx_funding_history_is_sorted_and_keeps_realized_rate_distinct():
    session = FakeSession({
        "code": "0",
        "data": [
            {"fundingTime": "1700028800000", "fundingRate": "0.0002",
             "realizedRate": "0.00019", "formulaType": "withRate", "method": "current_period"},
            {"fundingTime": "1700000000000", "fundingRate": "0.0001",
             "realizedRate": "0.00009", "formulaType": "withRate", "method": "current_period"},
        ],
    })
    result = fetch_funding_rate_history("BTCUSDT", limit=2, session=session)
    assert session.url == FUTURES_FUNDING_HISTORY_URL
    assert session.params == {"instId": "BTC-USDT-SWAP", "limit": 2}
    assert result[0]["funding_time_ms"] == 1700000000000
    assert result[0]["predicted_rate"] == pytest.approx(0.0001)
    assert result[0]["realized_rate"] == pytest.approx(0.00009)
    assert result[0]["is_realized"] is True


def test_okx_funding_history_rejects_invalid_realized_rate():
    session = FakeSession({
        "code": "0",
        "data": [{"fundingTime": "1700000000000", "realizedRate": "nan"}],
    })
    with pytest.raises(ValueError, match="invalid record"):
        fetch_funding_rate_history("BTCUSDT", session=session)



def test_okx_linear_swap_metadata_preserves_contract_sizing_units():
    session = FakeSession({
        "code": "0",
        "data": [{
            "instId": "BTC-USDT-SWAP", "instType": "SWAP",
            "ctType": "linear", "ctVal": "0.01", "ctValCcy": "BTC",
            "ctMult": "1", "settleCcy": "USDT", "lotSz": "1",
            "minSz": "1", "tickSz": "0.1", "lever": "100",
            "state": "live", "upcChg": [],
        }],
    })
    result = fetch_swap_instrument_metadata("BTCUSDT", session=session)
    assert session.url == FUTURES_INSTRUMENTS_URL
    assert session.params == {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
    assert result["contract_type"] == "linear"
    assert result["contract_value"] == pytest.approx(0.01)
    assert result["contract_value_currency"] == "BTC"
    assert result["lot_size_contracts"] == 1
    assert result["minimum_size_contracts"] == 1
    assert result["settlement_currency"] == "USDT"
    assert result["live_trading_enabled"] is False


def test_okx_swap_metadata_rejects_inverse_contracts():
    session = FakeSession({
        "code": "0",
        "data": [{
            "instId": "BTC-USDT-SWAP", "instType": "SWAP",
            "ctType": "inverse", "settleCcy": "BTC",
        }],
    })
    with pytest.raises(ValueError, match="linear USDT-settled"):
        fetch_swap_instrument_metadata("BTCUSDT", session=session)



def mark_price_candle(open_ms: int, *, close: str = "101", confirmed: str = "1"):
    return [str(open_ms), "100", "105", "99", close, confirmed]


def test_okx_mark_price_candles_are_normalized_and_unclosed_rows_filtered():
    session = FakeSession({
        "code": "0",
        "data": [
            mark_price_candle(1_700_003_600_000, confirmed="0"),
            mark_price_candle(1_700_000_000_000, confirmed="1"),
        ],
    })
    result = fetch_completed_mark_price_candles(
        "BTCUSDT", "1h", 2, session=session, now_ms=1_800_000_000_000,
    )
    assert session.url == MARK_PRICE_CANDLES_URL
    assert session.params == {"instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 2}
    assert len(result) == 1
    assert result[0]["close"] == 101.0
    assert result[0]["timestamp"].endswith("+00:00")


def test_okx_mark_price_candles_reject_inconsistent_ohlc():
    session = FakeSession({
        "code": "0",
        "data": [["1700000000000", "100", "100", "99", "101", "1"]],
    })
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        fetch_completed_mark_price_candles(
            "BTCUSDT", "1h", 2, session=session, now_ms=1_800_000_000_000,
        )
