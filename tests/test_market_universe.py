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


def candle(open_ms: int, close_ms: int, *, close: str = "101"):
    return [open_ms, "100", "105", "99", close, "12.5", close_ms]


@pytest.mark.parametrize(
    ("market", "expected_url"),
    [("spot", SPOT_KLINES_URL), ("futures", FUTURES_KLINES_URL)],
)
def test_uses_correct_public_market_endpoint_and_filters_open_candle(market, expected_url):
    session = FakeSession([
        candle(1_700_000_000_000, 1_700_000_299_999),
        candle(1_700_000_300_000, 1_700_000_599_999),
        candle(1_700_000_600_000, 1_700_000_899_999),
    ])

    result = fetch_completed_candles(
        market, "BTCUSDT", "5m", 3, session=session,
        now_ms=1_700_000_800_000,
    )

    assert session.url == expected_url
    assert session.params["symbol"] == "BTCUSDT"
    assert len(result) == 2
    assert result[-1]["close"] == 101.0


def test_rejects_non_usdt_symbol():
    with pytest.raises(ValueError, match="USDT-quoted"):
        fetch_completed_candles("spot", "BTCUSD", session=FakeSession([]))


def test_rejects_out_of_order_candles():
    session = FakeSession([
        candle(1_700_000_300_000, 1_700_000_599_999),
        candle(1_700_000_000_000, 1_700_000_299_999),
    ])
    with pytest.raises(ValueError, match="chronological"):
        fetch_completed_candles(
            "spot", "BTCUSDT", session=session, now_ms=1_800_000_000_000
        )


def test_rejects_inconsistent_ohlc():
    session = FakeSession([[1_700_000_000_000, "100", "100", "99", "101", "1", 1_700_000_299_999]])
    with pytest.raises(ValueError, match="inconsistent OHLC"):
        fetch_completed_candles(
            "spot", "BTCUSDT", session=session, now_ms=1_800_000_000_000
        )
