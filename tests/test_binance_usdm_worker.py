from datetime import datetime, timedelta, timezone

from binance_usdm_worker import (
    assess_paper_readiness,
    classify_source_status,
    probe_binance_vision_archives,
)


def candles(count=100):
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    return [
        {"timestamp": (start + timedelta(hours=i)).isoformat(), "open": 100, "high": 102, "low": 99, "close": 101}
        for i in range(count)
    ]


def metadata():
    return {
        "provider": "binance_usdm_exchange_info",
        "symbol": "BTCUSDT",
        "instrument": "BTCUSDT",
        "instrument_type": "SWAP",
        "contract_type": "linear",
        "settlement_currency": "USDT",
        "instrument_state": "live",
        "base_asset": "BTC",
        "contract_value": "1",
        "contract_value_currency": "BTC",
        "contract_multiplier": "1",
        "lot_size_contracts": "0.001",
        "minimum_size_contracts": "0.001",
        "maximum_size_contracts": "100",
        "minimum_notional_usdt": "50",
    }


def funding():
    return [{
        "funding_time": candles()[0]["timestamp"],
        "realized_rate": 0.0001,
        "is_realized": True,
    }]


def test_http_451_is_classified_as_runner_block_not_market_data():
    failures = [
        {"symbol": symbol, "error": "451 Client Error: unavailable"}
        for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT")
    ]
    assert classify_source_status(
        "451 Client Error: unavailable", failures, 0, 3
    ) == "blocked_by_http_451"


def test_partial_source_failures_are_not_misclassified_as_http_451_skip():
    failures = [
        {"symbol": "BTCUSDT", "error": "451 Client Error: unavailable"},
        {"symbol": "ETHUSDT", "error": "timeout"},
    ]
    assert classify_source_status(
        "451 Client Error: unavailable", failures, 0, 2
    ) == "unavailable"


class HeadResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class HeadSession:
    def __init__(self, statuses):
        self.statuses = statuses
        self.urls = []

    def head(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return HeadResponse(self.statuses.pop(0))


def test_binance_vision_archive_probe_checks_separate_official_datasets():
    client = HeadSession([200, 200, 200])
    result = probe_binance_vision_archives(
        session=client, month="2026-10", symbol="BTCUSDT"
    )
    assert result["all_available"] is True
    assert result["historical_only"] is True
    assert set(result["files"]) == {
        "trade_klines", "mark_price_klines", "funding_rate"
    }
    assert all(kwargs["timeout"] == 15 for _, kwargs in client.urls)
    assert all("data.binance.vision/data/futures/um/monthly/" in url for url, _ in client.urls)


def test_binance_vision_archive_probe_fails_closed_when_any_dataset_is_missing():
    result = probe_binance_vision_archives(
        session=HeadSession([200, 404, 200]), month="2026-10"
    )
    assert result["all_available"] is False
    assert result["files"]["mark_price_klines"]["available"] is False


def test_binance_vision_archive_probe_rejects_invalid_month():
    import pytest

    with pytest.raises(ValueError, match="valid YYYY-MM"):
        probe_binance_vision_archives(session=HeadSession([]), month="2026-13")


def test_readiness_requires_matching_completed_trade_and_mark_timestamps():
    trade = candles()
    mark = candles()
    result = assess_paper_readiness("BTCUSDT", trade, mark, funding(), metadata())
    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["live_trading_enabled"] is False


def test_readiness_blocks_mismatched_mark_price_history():
    trade = candles()
    mark = candles()
    mark.pop()
    result = assess_paper_readiness("BTCUSDT", trade, mark, funding(), metadata())
    assert result["ready"] is False
    assert "trade_mark_timestamps_not_identical" in result["reasons"]


def test_readiness_blocks_short_history_and_missing_metadata():
    trade = candles(30)
    result = assess_paper_readiness("BTCUSDT", trade, trade, funding(), None)
    assert result["ready"] is False
    assert "insufficient_completed_trade_history" in result["reasons"]
    assert "binance_instrument_metadata_unavailable" in result["reasons"]


def test_readiness_blocks_funding_settlement_inside_window_if_not_candle_aligned():
    trade = candles()
    funding = [{
        "funding_time": (datetime.fromisoformat(trade[20]["timestamp"]) + timedelta(minutes=5)).isoformat(),
        "realized_rate": 0.0001,
        "is_realized": True,
    }]
    result = assess_paper_readiness("BTCUSDT", trade, trade, funding, metadata())
    assert result["ready"] is False
    assert "funding_settlement_not_aligned_to_candle_open" in result["reasons"]


def test_readiness_blocks_wrong_native_instrument_identity():
    result = assess_paper_readiness("BTCUSDT", candles(), candles(), [], {
        **metadata(), "instrument": "BTC-USDT-SWAP"
    })
    assert result["ready"] is False
    assert "binance_instrument_identity_mismatch" in result["reasons"]



def test_readiness_blocks_missing_realized_funding_history():
    trade = candles()
    result = assess_paper_readiness("BTCUSDT", trade, trade, [], metadata())
    assert result["ready"] is False
    assert "realized_funding_history_unavailable" in result["reasons"]


def test_readiness_blocks_gaps_in_hourly_trade_candles():
    trade = candles()
    trade[30]["timestamp"] = (datetime.fromisoformat(trade[30]["timestamp"]) + timedelta(hours=1)).isoformat()
    result = assess_paper_readiness("BTCUSDT", trade, trade, funding(), metadata())
    assert result["ready"] is False
    assert "trade_candle_timeline_has_gaps" in result["reasons"]


def test_readiness_blocks_non_realized_funding_rows():
    trade = candles()
    records = [{**funding()[0], "is_realized": False}]
    result = assess_paper_readiness("BTCUSDT", trade, trade, records, metadata())
    assert result["ready"] is False
    assert "funding_history_contains_non_realized_record" in result["reasons"]
