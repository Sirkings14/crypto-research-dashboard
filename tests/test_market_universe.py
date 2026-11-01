from __future__ import annotations

import pytest

from market_universe import (
    FUTURES_KLINES_URL,
    SPOT_KLINES_URL,
    fetch_completed_candles,
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
