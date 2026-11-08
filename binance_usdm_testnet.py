"""Explicitly gated Binance USD-M Futures Testnet execution adapter.

This module is deliberately pinned to Binance's Futures Testnet host. It has
no configurable base URL, no production fallback, no leverage/account-setting
methods, and no withdrawal/transfer methods. Order submission is disabled
unless the caller explicitly opts in and supplies a conservative notional cap.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode

import requests

TESTNET_BASE_URL = "https://testnet.binancefuture.com"
EXCHANGE_INFO_PATH = "/fapi/v1/exchangeInfo"
TICKER_PRICE_PATH = "/fapi/v1/ticker/price"
ORDER_PATH = "/fapi/v1/order"
ACCOUNT_PATH = "/fapi/v2/account"
_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}USDT$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_./:-]{1,36}$")
_ALLOWED_ORDER_TYPES = {"MARKET"}


class BinanceTestnetError(RuntimeError):
    """Raised when Binance Testnet rejects a request or response."""


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return parsed


class BinanceUsdMTestnetClient:
    """Minimal signed REST client restricted to the USD-M Futures Testnet."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        allow_order_submission: bool = False,
        max_notional_usdt: float = 25.0,
        recv_window_ms: int = 5_000,
        timeout_seconds: float = 10.0,
        session: Any = None,
        clock_ms=None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("a Testnet API key is required")
        if not isinstance(api_secret, str) or not api_secret.strip():
            raise ValueError("a Testnet API secret is required")
        if not isinstance(allow_order_submission, bool):
            raise ValueError("allow_order_submission must be boolean")
        cap = _positive_decimal(max_notional_usdt, "max_notional_usdt")
        if cap > Decimal("25"):
            raise ValueError("Testnet order notional cap cannot exceed 25 USDT")
        if not 1_000 <= int(recv_window_ms) <= 60_000:
            raise ValueError("recv_window_ms must be between 1000 and 60000")
        if not 1 <= float(timeout_seconds) <= 30:
            raise ValueError("timeout_seconds must be between 1 and 30")
        self._api_key = api_key.strip()
        self._api_secret = api_secret.encode("utf-8")
        self.allow_order_submission = allow_order_submission
        self.max_notional_usdt = cap
        self.recv_window_ms = int(recv_window_ms)
        self.timeout_seconds = float(timeout_seconds)
        self._session = session or requests.Session()
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        if hasattr(self._session, "headers"):
            self._session.headers.update({
                "X-MBX-APIKEY": self._api_key,
                "User-Agent": "CryptoResearchLab-Testnet/1.0",
            })

    @staticmethod
    def _validate_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise ValueError("symbol must be an uppercase USDT-margined pair")
        return symbol

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, *, signed: bool = False):
        allowed_methods = {
            EXCHANGE_INFO_PATH: {"GET"},
            TICKER_PRICE_PATH: {"GET"},
            ACCOUNT_PATH: {"GET"},
            ORDER_PATH: {"GET", "POST", "DELETE"},
        }
        if path not in allowed_methods:
            raise ValueError("endpoint is not permitted by the Testnet adapter")
        if method not in allowed_methods[path]:
            raise ValueError("HTTP method is not permitted for this Testnet endpoint")
        payload = dict(params or {})
        if signed:
            payload["timestamp"] = int(self._clock_ms())
            payload["recvWindow"] = self.recv_window_ms
            query = urlencode(payload)
            payload["signature"] = hmac.new(
                self._api_secret, query.encode("utf-8"), hashlib.sha256
            ).hexdigest()
        url = TESTNET_BASE_URL + path
        try:
            response = self._session.request(
                method,
                url,
                params=payload if method == "GET" else None,
                data=payload if method in {"POST", "DELETE"} else None,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as exc:
            raise BinanceTestnetError("Binance Futures Testnet request failed") from exc
        except ValueError as exc:
            raise BinanceTestnetError("Binance Futures Testnet returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise BinanceTestnetError("Binance Futures Testnet response must be a JSON object")
        if "code" in result and isinstance(result.get("code"), int) and result["code"] < 0:
            raise BinanceTestnetError(f"Binance Futures Testnet error {result['code']}: {result.get('msg', 'unknown error')}")
        return result

    def exchange_info(self) -> dict[str, Any]:
        """Fetch public Testnet symbol filters; never uses production metadata."""
        return self._request("GET", EXCHANGE_INFO_PATH)

    def ticker_price(self, *, symbol: str) -> dict[str, Any]:
        """Fetch a public Testnet ticker price for a validated symbol."""
        symbol = self._validate_symbol(symbol)
        return self._request("GET", TICKER_PRICE_PATH, {"symbol": symbol})

    def account_status(self) -> dict[str, Any]:
        """Read authenticated account permissions from Testnet; never mutates state."""
        return self._request("GET", ACCOUNT_PATH, signed=True)

    def place_market_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: Any,
        reference_price: Any,
        client_order_id: str | None = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Submit a capped MARKET order only after explicit Testnet opt-in."""
        symbol = self._validate_symbol(symbol)
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        qty = _positive_decimal(quantity, "quantity")
        price = _positive_decimal(reference_price, "reference_price")
        if qty * price > self.max_notional_usdt:
            raise ValueError("estimated order notional exceeds the configured Testnet cap")
        if not isinstance(reduce_only, bool):
            raise ValueError("reduce_only must be boolean")
        if client_order_id is not None and (
            not isinstance(client_order_id, str) or not _CLIENT_ID.fullmatch(client_order_id)
        ):
            raise ValueError("client_order_id contains unsupported characters or is too long")
        if not self.allow_order_submission:
            raise PermissionError("Testnet order submission is disabled; explicit opt-in is required")
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": format(qty, "f"),
            "newOrderRespType": "RESULT",
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        return self._request("POST", ORDER_PATH, params, signed=True)

    def get_order(self, *, symbol: str, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
        symbol = self._validate_symbol(symbol)
        if (order_id is None) == (client_order_id is None):
            raise ValueError("provide exactly one of order_id or client_order_id")
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
                raise ValueError("order_id must be a positive integer")
            params["orderId"] = order_id
        else:
            if not isinstance(client_order_id, str) or not _CLIENT_ID.fullmatch(client_order_id):
                raise ValueError("client_order_id is invalid")
            params["origClientOrderId"] = client_order_id
        return self._request("GET", ORDER_PATH, params, signed=True)

    def cancel_order(self, *, symbol: str, order_id: int | None = None, client_order_id: str | None = None) -> dict[str, Any]:
        symbol = self._validate_symbol(symbol)
        if (order_id is None) == (client_order_id is None):
            raise ValueError("provide exactly one of order_id or client_order_id")
        params: dict[str, Any] = {"symbol": symbol}
        if order_id is not None:
            if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
                raise ValueError("order_id must be a positive integer")
            params["orderId"] = order_id
        else:
            if not isinstance(client_order_id, str) or not _CLIENT_ID.fullmatch(client_order_id):
                raise ValueError("client_order_id is invalid")
            params["origClientOrderId"] = client_order_id
        return self._request("DELETE", ORDER_PATH, params, signed=True)
