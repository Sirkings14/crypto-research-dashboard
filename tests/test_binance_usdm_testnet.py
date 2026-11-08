import hashlib
import hmac
from urllib.parse import urlencode

import pytest

from binance_usdm_testnet import (
    BinanceUsdMTestnetClient,
    ORDER_PATH,
    TICKER_PRICE_PATH,
    TESTNET_BASE_URL,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload=None):
        self.headers = {}
        self.payload = payload or {"orderId": 123, "status": "FILLED"}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse(self.payload)


def client(*, enabled=False, cap=25, payload=None):
    session = FakeSession(payload)
    instance = BinanceUsdMTestnetClient(
        "test-key", "test-secret", allow_order_submission=enabled,
        max_notional_usdt=cap, session=session, clock_ms=lambda: 1_700_000_000_000,
    )
    return instance, session


def test_host_is_pinned_to_futures_testnet_and_never_production():
    instance, session = client(enabled=True)
    instance.place_market_order(
        symbol="BTCUSDT", side="BUY", quantity="0.0001",
        reference_price="60000", client_order_id="paper-test-001",
    )
    method, url, _ = session.calls[0]
    assert method == "POST"
    assert url == TESTNET_BASE_URL + ORDER_PATH
    assert "fapi.binance.com" not in url


def test_order_submission_is_disabled_by_default():
    instance, session = client()
    with pytest.raises(PermissionError, match="disabled"):
        instance.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity="0.0001",
            reference_price="60000",
        )
    assert session.calls == []


def test_notional_cap_is_enforced_before_network_call():
    instance, session = client(enabled=True, cap=20)
    with pytest.raises(ValueError, match="notional"):
        instance.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity="0.001",
            reference_price="60000",
        )
    assert session.calls == []


def test_cap_cannot_be_configured_above_25_usdt():
    with pytest.raises(ValueError, match="cannot exceed 25"):
        client(enabled=True, cap=25.01)


def test_signed_request_contains_valid_hmac_signature():
    instance, session = client(enabled=True)
    instance.place_market_order(
        symbol="BTCUSDT", side="BUY", quantity="0.0001",
        reference_price="60000",
    )
    _, _, kwargs = session.calls[0]
    data = kwargs["data"]
    signature = data.pop("signature")
    canonical = urlencode(data)
    expected = hmac.new(b"test-secret", canonical.encode(), hashlib.sha256).hexdigest()
    assert signature == expected
    assert data["timestamp"] == 1_700_000_000_000


def test_market_order_has_expected_parameters_and_reduce_only_flag():
    instance, session = client(enabled=True)
    instance.place_market_order(
        symbol="ETHUSDT", side="SELL", quantity="0.01",
        reference_price="2000", reduce_only=True,
    )
    _, _, kwargs = session.calls[0]
    assert kwargs["data"]["type"] == "MARKET"
    assert kwargs["data"]["side"] == "SELL"
    assert kwargs["data"]["reduceOnly"] == "true"
    assert kwargs["data"]["newOrderRespType"] == "RESULT"


@pytest.mark.parametrize("side", ["LONG", "BUY/SELL", "", None])
def test_rejects_invalid_side(side):
    instance, session = client(enabled=True)
    with pytest.raises(ValueError, match="side"):
        instance.place_market_order(
            symbol="BTCUSDT", side=side, quantity="0.0001",
            reference_price="60000",
        )
    assert session.calls == []


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN", "Infinity"])
def test_rejects_invalid_quantity(quantity):
    instance, session = client(enabled=True)
    with pytest.raises(ValueError, match="quantity"):
        instance.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity=quantity,
            reference_price="60000",
        )
    assert session.calls == []


def test_rejects_non_usdt_symbol_and_invalid_order_identifier():
    instance, session = client(enabled=True)
    with pytest.raises(ValueError, match="USDT"):
        instance.place_market_order(
            symbol="BTCBUSD", side="BUY", quantity="0.0001",
            reference_price="60000",
        )
    with pytest.raises(ValueError, match="client_order_id"):
        instance.place_market_order(
            symbol="BTCUSDT", side="BUY", quantity="0.0001",
            reference_price="60000", client_order_id="bad id",
        )
    assert session.calls == []


def test_query_and_cancel_use_signed_testnet_order_endpoint():
    instance, session = client(enabled=True)
    queried = instance.get_order(symbol="BTCUSDT", order_id=123)
    canceled = instance.cancel_order(symbol="BTCUSDT", client_order_id="paper-123")
    assert queried["status"] == "FILLED"
    assert canceled["orderId"] == 123
    assert [call[0] for call in session.calls] == ["GET", "DELETE"]
    assert all(call[1] == TESTNET_BASE_URL + ORDER_PATH for call in session.calls)


def test_order_lookup_requires_exactly_one_identifier():
    instance, session = client(enabled=True)
    with pytest.raises(ValueError, match="exactly one"):
        instance.get_order(symbol="BTCUSDT")
    with pytest.raises(ValueError, match="exactly one"):
        instance.cancel_order(symbol="BTCUSDT", order_id=1, client_order_id="x")
    assert session.calls == []


def test_public_ticker_price_uses_pinned_testnet_endpoint():
    instance, session = client()
    session.payload = {"symbol": "BTCUSDT", "price": "60000.00"}
    result = instance.ticker_price(symbol="BTCUSDT")
    assert result["price"] == "60000.00"
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == TESTNET_BASE_URL + TICKER_PRICE_PATH
    assert kwargs["params"] == {"symbol": "BTCUSDT"}
