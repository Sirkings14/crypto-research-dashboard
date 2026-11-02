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
                          [(3_000, 3_000), (3_000, 3_030), (3_030, 3_030)],
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
