from datetime import datetime, timedelta, timezone

from binance_usdm_worker import assess_paper_readiness


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
    }


def test_readiness_requires_matching_completed_trade_and_mark_timestamps():
    trade = candles()
    mark = candles()
    result = assess_paper_readiness("BTCUSDT", trade, mark, [], metadata())
    assert result["ready"] is True
    assert result["reasons"] == []
    assert result["live_trading_enabled"] is False


def test_readiness_blocks_mismatched_mark_price_history():
    trade = candles()
    mark = candles()
    mark.pop()
    result = assess_paper_readiness("BTCUSDT", trade, mark, [], metadata())
    assert result["ready"] is False
    assert "trade_mark_timestamps_not_identical" in result["reasons"]


def test_readiness_blocks_short_history_and_missing_metadata():
    trade = candles(30)
    result = assess_paper_readiness("BTCUSDT", trade, trade, [], None)
    assert result["ready"] is False
    assert "insufficient_completed_trade_history" in result["reasons"]
    assert "binance_instrument_metadata_unavailable" in result["reasons"]


def test_readiness_blocks_funding_settlement_inside_window_if_not_candle_aligned():
    trade = candles()
    funding = [{
        "funding_time": (datetime.fromisoformat(trade[20]["timestamp"]) + timedelta(minutes=5)).isoformat(),
        "realized_rate": 0.0001,
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
