from datetime import datetime, timedelta, timezone

import pytest

from futures_paper_ledger import run_futures_paper_replay


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stamp(hours):
    return (BASE + timedelta(hours=hours)).isoformat()


def rows(prices):
    trade, mark = [], []
    for i, (op, cl) in enumerate(prices):
        trade.append({"timestamp": stamp(i), "open": op, "close": cl})
        mark.append({"timestamp": stamp(i), "open": op, "close": cl})
    return trade, mark


def meta(**overrides):
    value = {
        "instrument": "BTC-USDT-SWAP",
        "instrument_type": "SWAP", "contract_type": "linear",
        "settlement_currency": "USDT", "instrument_state": "live",
        "contract_value": 0.01, "contract_value_currency": "BTC",
        "contract_multiplier": "1", "lot_size_contracts": 1,
        "minimum_size_contracts": 1,
    }
    value.update(overrides)
    return value


def replay(trade, mark, sides, funding=None, **kwargs):
    return run_futures_paper_replay(
        symbol="BTCUSDT", trade_candles=trade, mark_candles=mark,
        funding_history=funding or [], instrument_metadata=meta(),
        target_sides=sides, initial_equity_usdt=2_000,
        target_notional_usdt=600, max_notional_usdt=600,
        taker_fee_bps_per_side=0, slippage_bps_per_side=0,
        **kwargs,
    )


def test_long_position_marked_and_closed_at_end():
    trade, mark = rows([(60_000, 60_000), (60_000, 61_000), (62_000, 62_000)])
    result = replay(trade, mark, ["long", "long", "flat"])
    assert result["final_equity_usdt"] == pytest.approx(2_020)
    assert result["trade_event_count"] == 2
    assert result["live_trading_enabled"] is False


def test_short_position_profit_and_loss_direction():
    trade, mark = rows([(60_000, 60_000), (59_000, 59_000)])
    result = replay(trade, mark, ["short", "flat"])
    assert result["final_equity_usdt"] == pytest.approx(2_010)


def test_realized_funding_only_charged_if_position_carried_into_settlement():
    trade, mark = rows([(60_000, 60_000), (60_000, 61_000), (61_000, 61_000)])
    funding = [{"funding_time": stamp(1), "realized_rate": 0.001}]
    result = replay(trade, mark, ["long", "long", "flat"], funding)
    assert result["funding_settlement_count"] == 1
    assert result["net_funding_cost_usdt"] == pytest.approx(0.6)
    assert result["final_equity_usdt"] == pytest.approx(2_009.4)


def test_new_position_does_not_pay_funding_at_its_entry_timestamp():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    funding = [{"funding_time": stamp(0), "realized_rate": 0.01}]
    result = replay(trade, mark, ["long", "flat"], funding)
    assert result["funding_settlement_count"] == 0
    assert result["net_funding_cost_usdt"] == 0


def test_fee_and_slippage_reduce_returns():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    result = run_futures_paper_replay(
        symbol="BTCUSDT", trade_candles=trade, mark_candles=mark,
        funding_history=[], instrument_metadata=meta(),
        target_sides=["long", "flat"], initial_equity_usdt=2_000,
        target_notional_usdt=600, max_notional_usdt=600,
        taker_fee_bps_per_side=10, slippage_bps_per_side=5,
    )
    assert result["fees_paid_usdt"] > 0
    assert result["final_equity_usdt"] < 2_000


def test_rejects_misaligned_mark_candles():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    mark[1]["timestamp"] = stamp(9)
    with pytest.raises(ValueError, match="timestamp mismatch"):
        replay(trade, mark, ["long", "flat"])


def test_rejects_funding_timestamp_not_on_candle_boundary():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    funding = [{"funding_time": (BASE + timedelta(minutes=30)).isoformat(),
                "realized_rate": 0.001}]
    with pytest.raises(ValueError, match="does not align"):
        replay(trade, mark, ["long", "flat"], funding)


def test_rejects_gaps_and_bad_signals():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000), (60_000, 60_000)])
    trade[2]["timestamp"] = stamp(4)
    mark[2]["timestamp"] = stamp(4)
    with pytest.raises(ValueError, match="gaps"):
        replay(trade, mark, ["long", "flat", "flat"])
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    with pytest.raises(ValueError, match="target side"):
        replay(trade, mark, ["buy", "flat"])


def test_daily_loss_guard_blocks_new_entries():
    trade, mark = rows([(60_000, 60_000), (60_000, 50_000), (50_000, 50_000)])
    result = replay(
        trade, mark, ["long", "long", "long"],
        max_daily_loss_pct=1.0, max_drawdown_pct=50.0,
    )
    assert result["risk_entries_blocked"] is True
    assert result["risk_block_reason"] == "daily_loss_limit"
    assert result["trade_event_count"] == 2  # first open and close; later entry blocked


def test_end_of_replay_closes_position_and_charges_exit_fee():
    trade, mark = rows([(60_000, 60_000), (60_000, 61_000)])
    result = run_futures_paper_replay(
        symbol="BTCUSDT", trade_candles=trade, mark_candles=mark,
        funding_history=[], instrument_metadata=meta(),
        target_sides=["long", "long"], initial_equity_usdt=2_000,
        target_notional_usdt=600, max_notional_usdt=600,
        taker_fee_bps_per_side=10, slippage_bps_per_side=0,
    )
    assert result["trades"][-1]["reason"] == "end_of_replay"
    assert result["final_equity_usdt"] < 2_010


def test_funding_history_must_be_chronological_and_realized_only():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000), (60_000, 60_000)])
    funding = [
        {"funding_time": stamp(1), "realized_rate": None, "predicted_rate": 0.5},
        {"funding_time": stamp(2), "realized_rate": 0.001},
    ]
    result = replay(trade, mark, ["long", "long", "flat"], funding)
    assert result["excluded_unrealized_funding_records"] == 1
    assert result["funding_settlement_count"] == 1


def test_rejects_target_notional_above_cap():
    trade, mark = rows([(60_000, 60_000), (60_000, 60_000)])
    with pytest.raises(ValueError, match="cannot exceed"):
        run_futures_paper_replay(
            symbol="BTCUSDT", trade_candles=trade, mark_candles=mark,
            funding_history=[], instrument_metadata=meta(),
            target_sides=["long", "flat"], initial_equity_usdt=2_000,
            target_notional_usdt=700, max_notional_usdt=600,
        )
