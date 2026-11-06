"""Adversarial portfolio-level Futures replay scenarios.

These are deterministic paper-account stress tests, not strategy-performance
claims. They exercise simultaneous market shocks, realized funding shocks and
fail-closed handling of invalid risk reference data.
"""
from datetime import datetime, timedelta, timezone

import pytest

from futures_portfolio_coordinator import run_futures_portfolio_replay


BASE = datetime(2026, 2, 1, tzinfo=timezone.utc)


def stamp(hour):
    return (BASE + timedelta(hours=hour)).isoformat()


def make_market(symbol, contract_value, currency, prices, funding=None):
    trades = []
    marks = []
    for index, (open_price, close_price) in enumerate(prices):
        trades.append({
            "timestamp": stamp(index),
            "open": open_price,
            "close": close_price,
        })
        marks.append({
            "timestamp": stamp(index),
            "open": open_price,
            "close": close_price,
        })
    return {
        "trade_candles": trades,
        "mark_candles": marks,
        "funding_history": funding or [],
        "instrument_metadata": {
            "instrument": f"{symbol[:-4]}-USDT-SWAP",
            "instrument_type": "SWAP",
            "contract_type": "linear",
            "settlement_currency": "USDT",
            "instrument_state": "live",
            "contract_value": contract_value,
            "contract_value_currency": currency,
            "contract_multiplier": "1",
            "lot_size_contracts": 1,
            "minimum_size_contracts": 1,
        },
        "target_sides": ["long", "long", "long", "flat"],
    }


def stress_markets(*, prices_btc=None, prices_eth=None, funding_btc=None, funding_eth=None):
    return {
        "BTCUSDT": make_market(
            "BTCUSDT", 0.01, "BTC",
            prices_btc or [
                (60_000, 60_000), (60_000, 60_000),
                (60_000, 60_000), (60_000, 60_000),
            ],
            funding_btc,
        ),
        "ETHUSDT": make_market(
            "ETHUSDT", 0.1, "ETH",
            prices_eth or [
                (3_000, 3_000), (3_000, 3_000),
                (3_000, 3_000), (3_000, 3_000),
            ],
            funding_eth,
        ),
    }


def replay(markets, **overrides):
    config = {
        "markets": markets,
        "initial_equity_usdt": 5_000,
        "target_total_notional_usdt": 1_500,
        "max_total_notional_usdt": 2_000,
        "taker_fee_bps_per_side": 0,
        "slippage_bps_per_side": 0,
        "max_daily_loss_pct": 2.0,
        "max_drawdown_pct": 100.0,
    }
    config.update(overrides)
    return run_futures_portfolio_replay(**config)


def test_simultaneous_cross_asset_crash_triggers_one_portfolio_exit():
    markets = stress_markets(
        prices_btc=[
            (60_000, 60_000), (60_000, 36_000),
            (36_000, 36_000), (36_000, 36_000),
        ],
        prices_eth=[
            (3_000, 3_000), (3_000, 1_800),
            (1_800, 1_800), (1_800, 1_800),
        ],
    )

    result = replay(markets)

    breaker = result["portfolio_risk_breaker"]
    assert breaker["triggered"] is True
    assert breaker["reason"] in {
        "portfolio_daily_loss_limit",
        "portfolio_max_drawdown_limit",
    }
    assert breaker["detection_timestamp"] == stamp(1)
    assert breaker["coordinated_exit_timestamp"] == stamp(2)
    assert breaker["coordinated_exit_execution"] == "next_candle_open"
    for symbol in ("BTCUSDT", "ETHUSDT"):
        exits = [
            trade for trade in result["per_symbol"][symbol]["trades"]
            if trade["action"] == "close"
        ]
        assert exits
        assert exits[0]["timestamp"] == stamp(2)
        assert exits[0]["reason"] == "signal_change"
        assert result["per_symbol"][symbol]["equity_curve"][2]["position_side"] == "flat"


def test_simultaneous_realized_funding_shock_triggers_portfolio_breaker():
    markets = stress_markets(
        funding_btc=[{"funding_time": stamp(1), "realized_rate": 0.02}],
        funding_eth=[{"funding_time": stamp(1), "realized_rate": 0.02}],
    )

    result = replay(markets, max_daily_loss_pct=0.2)

    assert result["portfolio_risk_breaker"]["triggered"] is True
    assert result["portfolio_risk_breaker"]["reason"] == "portfolio_daily_loss_limit"
    assert result["portfolio_risk_breaker"]["detection_timestamp"] == stamp(1)
    assert result["portfolio_risk_breaker"]["coordinated_exit_timestamp"] == stamp(2)
    assert result["per_symbol"]["BTCUSDT"]["funding_settlement_count"] == 1
    assert result["per_symbol"]["ETHUSDT"]["funding_settlement_count"] == 1
    assert result["per_symbol"]["BTCUSDT"]["net_funding_cost_usdt"] > 0
    assert result["per_symbol"]["ETHUSDT"]["net_funding_cost_usdt"] > 0


def test_invalid_mark_price_in_any_symbol_fails_closed_before_replay():
    markets = stress_markets()
    markets["ETHUSDT"]["mark_candles"][1]["close"] = float("nan")

    with pytest.raises(ValueError, match="mark close must be a finite positive price"):
        replay(markets)
