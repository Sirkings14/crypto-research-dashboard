from datetime import datetime, timedelta, timezone

from binance_usdm_worker import (
    assess_paper_readiness,
    classify_source_status,
    probe_binance_vision_archives,
    inspect_binance_vision_archives,
    previous_month_labels,
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


def test_previous_month_labels_are_calendar_correct_across_year_boundary():
    now = datetime(2027, 1, 4, tzinfo=timezone.utc)
    assert previous_month_labels(count=3, now=now) == [
        "2026-12", "2026-11", "2026-10"
    ]


def test_previous_month_labels_reject_naive_datetime():
    import pytest

    with pytest.raises(ValueError, match="timezone"):
        previous_month_labels(now=datetime(2026, 11, 4))


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


class DownloadResponse:
    def __init__(self, content, text=None, status_code=200):
        self.content = content
        self.text = text if text is not None else content.decode("utf-8", errors="replace")
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class ArchiveSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        return self.responses.pop(0)


def _archive_response(filename, rows):
    import hashlib
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename, rows)
    content = payload.getvalue()
    checksum = hashlib.sha256(content).hexdigest() + "  sample.zip\n"
    return DownloadResponse(content), DownloadResponse(checksum.encode())


def test_binance_vision_archive_inspection_verifies_checksum_and_reads_csv():
    names = [
        "BTCUSDT-1h-2026-10.csv",
        "BTCUSDT-markPrice-1h-2026-10.csv",
        "BTCUSDT-fundingRate-2026-10.csv",
    ]
    rows = [
        "1000,100,102,99,101,10,4599999,1000,5,4,400,0\n",
        "1000,100,102,99,101,0,4599999,0,0,0,0,0\n",
        "calc_time,funding_interval_hours,last_funding_rate\n1000,8,0.0001\n",
    ]
    responses = []
    for filename, content in zip(names, rows):
        responses.extend(_archive_response(filename, content))
    result = inspect_binance_vision_archives(
        session=ArchiveSession(responses), month="2026-10"
    )
    assert result["all_checksums_valid"] is True
    assert result["schema_inspection_only"] is True
    assert result["files"]["trade_klines"]["row_count"] == 1
    assert result["files"]["funding_rate"]["sample_rows"][0][0] == "calc_time"


def test_binance_vision_archive_inspection_rejects_checksum_mismatch():
    import io
    import zipfile

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("data.csv", "a,b\n1,2\n")
    archive_response = DownloadResponse(payload.getvalue())
    wrong_checksum = DownloadResponse(b"0" * 64 + b"  sample.zip\n")
    responses = [archive_response, wrong_checksum] * 3
    result = inspect_binance_vision_archives(
        session=ArchiveSession(responses), month="2026-10"
    )
    assert result["all_checksums_valid"] is False
    assert "checksum mismatch" in result["files"]["trade_klines"]["error"]


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


def test_readiness_accepts_funding_settlement_within_ten_ms_after_candle_open():
    trade = candles()
    record = {
        "funding_time": (
            datetime.fromisoformat(trade[20]["timestamp"]) + timedelta(milliseconds=5)
        ).isoformat(),
        "realized_rate": 0.0001,
        "is_realized": True,
    }
    result = assess_paper_readiness("BTCUSDT", trade, trade, [record], metadata())
    assert result["ready"] is True
    assert result["funding_settlement_alignment_tolerance_ms"] == 10


def test_readiness_blocks_funding_settlement_more_than_ten_ms_after_candle_open():
    trade = candles()
    record = {
        "funding_time": (
            datetime.fromisoformat(trade[20]["timestamp"]) + timedelta(milliseconds=11)
        ).isoformat(),
        "realized_rate": 0.0001,
        "is_realized": True,
    }
    result = assess_paper_readiness("BTCUSDT", trade, trade, [record], metadata())
    assert result["ready"] is False
    assert "funding_settlement_not_aligned_within_10ms_of_candle_open" in result["reasons"]


def test_readiness_blocks_funding_settlement_inside_window_if_not_candle_aligned():
    trade = candles()
    funding = [{
        "funding_time": (datetime.fromisoformat(trade[20]["timestamp"]) + timedelta(minutes=5)).isoformat(),
        "realized_rate": 0.0001,
        "is_realized": True,
    }]
    result = assess_paper_readiness("BTCUSDT", trade, trade, funding, metadata())
    assert result["ready"] is False
    assert "funding_settlement_not_aligned_within_10ms_of_candle_open" in result["reasons"]


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
