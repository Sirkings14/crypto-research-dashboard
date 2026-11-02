from datetime import datetime, timedelta, timezone

import pytest

from futures_portfolio_coordinator import run_futures_portfolio_replay


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stamp(hour):
    return (BASE + timedelta(hours=hour)).isoformat()


def market(symbol, contract_value, currency, prices, sides):
    trades, marks = [], []
    for index, (open_price, close_price) in enumerate(prices):
        trades.append({"timestamp": stamp(index), "open": open_price, "close": close_price})
        marks.append({"timestamp": stamp(index), "open": open_price, "close": close_price})
    return {
        "trade_candles": trades,
        "mark_candles": marks,
        "funding_history": [],
        "instrument_metadata": {
            "instrument": f"{symbol[:-4]}-USDT-SWAP",
            "instrument_type": "SWAP", "contract_type": "linear",
            "settlement_currency": "USDT", "instrument_state": "live",
            "contract_value": contract_value, "contract_value_currency": currency,
            "contract_multiplier": "1", "lot_size_contracts": 1,
            "minimum_size_contracts": 1,
        },
        "target_sides": sides,
    }


def two_markets():
    return {
        "BTCUSDT": market("BTCUSDT", 0.01, "BTC",
                          [(60_000, 60_000), (60_000, 61_000), (61_000, 61_000)],
                          ["long", "long", "flat"]),
        "ETHUSDT": market("ETHUSDT", 0.1, "ETH",
                          [(3_000, 3_000), (3_000, 3_200), (3_200, 3_200)],
                          ["long", "long", "flat"]),
    }


def test_equal_sleeves_share_one_budget_and_aggregate_pnl():
    result = run_futures_portfolio_replay(
        markets=two_markets(), initial_equity_usdt=2_000,
        target_total_notional_usdt=1_000, max_total_notional_usdt=1_200,
        taker_fee_bps_per_side=0, slippage_bps_per_side=0,
    )
    assert result["market_count"] == 2
    assert result["per_symbol"]["BTCUSDT"]["initial_equity_usdt"] == 1_000
    assert result["per_symbol"]["ETHUSDT"]["initial_equity_usdt"] == 1_000
    assert result["final_equity_usdt"] == pytest.approx(2_030)
    assert result["maximum_observed_gross_notional_usdt"] <= 1_200
    assert result["live_trading_enabled"] is False


def test_rejects_market_timelines_that_do_not_match():
    markets = two_markets()
    markets["ETHUSDT"]["trade_candles"][1]["timestamp"] = stamp(3)
    markets["ETHUSDT"]["mark_candles"][1]["timestamp"] = stamp(3)
    with pytest.raises(ValueError, match="identical candle timestamps"):
        run_futures_portfolio_replay(markets=markets)


@pytest.mark.parametrize("target,cap,equity", [
    (600, 500, 1_000),
    (600, 600, 500),
])
def test_rejects_target_or_gross_cap_outside_budget(target, cap, equity):
    with pytest.raises(ValueError):
        run_futures_portfolio_replay(
            markets=two_markets(), initial_equity_usdt=equity,
            target_total_notional_usdt=target, max_total_notional_usdt=cap,
        )


def test_rejects_empty_portfolio_and_missing_market_inputs():
    with pytest.raises(ValueError, match="non-empty"):
        run_futures_portfolio_replay(markets={})
    with pytest.raises(ValueError, match="missing required fields"):
        run_futures_portfolio_replay(markets={"BTCUSDT": {}})


def test_each_market_is_independently_validated_by_single_symbol_ledger():
    markets = two_markets()
    markets["ETHUSDT"]["instrument_metadata"]["instrument"] = "BTC-USDT-SWAP"
    with pytest.raises(ValueError, match="does not match"):
        run_futures_portfolio_replay(markets=markets)


def test_portfolio_daily_loss_breaker_flattens_all_symbols_and_blocks_reentry():
    markets = two_markets()
    for symbol in markets:
        item = markets[symbol]
        if symbol == "BTCUSDT":
            prices = [(60_000, 60_000), (60_000, 54_000), (54_000, 54_000)]
        else:
            prices = [(3_000, 3_000), (3_000, 2_700), (2_700, 2_700)]
        item["trade_candles"] = [
            {"timestamp": stamp(i), "open": op, "close": close}
            for i, (op, close) in enumerate(prices)
        ]
        item["mark_candles"] = [
            {"timestamp": stamp(i), "open": op, "close": close}
            for i, (op, close) in enumerate(prices)
        ]
        item["target_sides"] = ["long", "long", "long"]

    result = run_futures_portfolio_replay(
        markets=markets,
        initial_equity_usdt=2_000,
        target_total_notional_usdt=1_000,
        max_total_notional_usdt=1_200,
        taker_fee_bps_per_side=0,
        slippage_bps_per_side=0,
        max_daily_loss_pct=4,
        max_drawdown_pct=50,
    )

    breaker = result["portfolio_risk_breaker"]
    assert breaker["triggered"] is True
    assert breaker["reason"] == "portfolio_daily_loss_limit"
    assert breaker["detection_timestamp"] == stamp(1)
    assert breaker["coordinated_exit_timestamp"] == stamp(2)
    for symbol in ("BTCUSDT", "ETHUSDT"):
        trades = result["per_symbol"][symbol]["trades"]
        assert any(
            trade["action"] == "close" and trade["timestamp"] == stamp(2)
            for trade in trades
        )
        assert not any(
            trade["action"] == "open" and trade["timestamp"] == stamp(2)
            for trade in trades
        )
        assert result["per_symbol"][symbol]["equity_curve"][-1]["position_side"] == "flat"
    assert result["live_trading_enabled"] is False
