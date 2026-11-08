from __future__ import annotations

import hashlib
import hmac

import pytest

from bybit_demo_readonly import (
    ACCOUNT_INFO_URL,
    BASE_URL,
    BybitDemoError,
    read_only_account_check,
)


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_demo_host_is_fixed_and_authenticated_request_is_read_only():
    session = Session(Response(payload={"retCode": 0, "result": {"unifiedMarginStatus": 3}}))
    result = read_only_account_check("demo-key", "demo-secret", session=session, clock_ms=1700000000000)
    assert BASE_URL == "https://api-demo.bybit.com"
    assert session.calls[0][0] == ACCOUNT_INFO_URL
    headers = session.calls[0][1]["headers"]
    payload = "1700000000000demo-key5000"
    expected = hmac.new(b"demo-secret", payload.encode(), hashlib.sha256).hexdigest()
    assert headers["X-BAPI-SIGN"] == expected
    assert result["result"] == "AUTHENTICATED"
    assert result["read_only"] is True
    assert result["order_submissions"] == "NOT_ATTEMPTED"


def test_nonzero_bybit_code_is_sanitized():
    session = Session(Response(payload={"retCode": 10003, "retMsg": "sensitive server detail"}))
    with pytest.raises(BybitDemoError, match="bybit_ret_code=10003"):
        read_only_account_check("demo-key", "demo-secret", session=session, clock_ms=1)


def test_http_error_does_not_expose_response_body():
    session = Session(Response(status=451, payload={"secret": "must not appear"}))
    with pytest.raises(BybitDemoError, match="http_status=451"):
        read_only_account_check("demo-key", "demo-secret", session=session, clock_ms=1)


def test_missing_credentials_rejected_before_request():
    session = Session(Response())
    with pytest.raises(ValueError, match="credentials are required"):
        read_only_account_check("", "", session=session)
    assert session.calls == []
