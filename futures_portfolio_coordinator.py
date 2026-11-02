"""Ring-fenced multi-symbol Futures paper portfolio coordinator.

This v1 coordinator splits one portfolio budget into equal, independent
capital sleeves and runs the validated single-instrument ledger for each.
It enforces a shared aggregate notional ceiling by construction, requires
identical candle timelines, and aggregates equity/exposure. It is not a
cross-margin account or a unified intraday risk engine.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from futures_contract_math import contract_notional_usdt
from futures_paper_ledger import run_futures_paper_replay


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and positive") from exc
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def run_futures_portfolio_replay(
    *,
    markets: dict[str, dict[str, Any]],
    initial_equity_usdt: float = 1_000.0,
    target_total_notional_usdt: float = 300.0,
    max_total_notional_usdt: float = 500.0,
    taker_fee_bps_per_side: float = 5.0,
    slippage_bps_per_side: float = 2.0,
    max_daily_loss_pct: float = 3.0,
    max_drawdown_pct: float = 10.0,
) -> dict[str, Any]:
    """Replay several instruments with equal ring-fenced capital/notional sleeves.

    Each market value must contain trade_candles, mark_candles, funding_history,
    instrument_metadata and target_sides. All markets must share the exact same
    candle timestamps. Each sleeve receives 1/N of initial equity and both
    target and maximum notional. Thus the sum of all sleeve caps cannot exceed
    max_total_notional_usdt, and the aggregate cap cannot exceed portfolio
    starting equity (a conservative 1x gross-notional ceiling).
    """
    if not isinstance(markets, dict) or not markets:
        raise ValueError("markets must be a non-empty symbol-to-market mapping")
    symbols = sorted(markets)
    if len(set(symbols)) != len(symbols):
        raise ValueError("market symbols must be unique")
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol.isupper():
            raise ValueError("all market keys must be uppercase USDT-quoted symbols")
        if not isinstance(markets[symbol], dict):
            raise ValueError(f"{symbol} market input must be an object")

    initial = _positive(initial_equity_usdt, "initial_equity_usdt")
    target_total = _positive(target_total_notional_usdt, "target_total_notional_usdt")
    cap_total = _positive(max_total_notional_usdt, "max_total_notional_usdt")
    if target_total > cap_total:
        raise ValueError("target_total_notional_usdt cannot exceed max_total_notional_usdt")
    if cap_total > initial + 1e-9:
        raise ValueError("max_total_notional_usdt cannot exceed initial equity in 1x portfolio mode")

    count = len(symbols)
    sleeve_equity = initial / count
    sleeve_target = target_total / count
    sleeve_cap = cap_total / count
    baseline_timestamps: list[str] | None = None
    results: dict[str, dict[str, Any]] = {}

    for symbol in symbols:
        item = markets[symbol]
        required = {
            "trade_candles", "mark_candles", "funding_history",
            "instrument_metadata", "target_sides",
        }
        missing = required.difference(item)
        if missing:
            raise ValueError(f"{symbol} is missing required fields: {sorted(missing)}")

        trades = item["trade_candles"]
        marks = item["mark_candles"]
        if not isinstance(trades, list) or not isinstance(marks, list):
            raise ValueError(f"{symbol} trade and mark candles must be lists")
        stamps = [row.get("timestamp") for row in trades if isinstance(row, dict)]
        if len(stamps) != len(trades):
            raise ValueError(f"{symbol} trade candle rows must be objects")
        mark_stamps = [row.get("timestamp") for row in marks if isinstance(row, dict)]
        if len(mark_stamps) != len(marks):
            raise ValueError(f"{symbol} mark candle rows must be objects")
        if stamps != mark_stamps:
            raise ValueError(f"{symbol} trade/mark timestamps must match exactly")
        if baseline_timestamps is None:
            baseline_timestamps = stamps
        elif stamps != baseline_timestamps:
            raise ValueError("all portfolio markets must share identical candle timestamps")

        results[symbol] = run_futures_paper_replay(
            symbol=symbol,
            trade_candles=trades,
            mark_candles=marks,
            funding_history=item["funding_history"],
            instrument_metadata=item["instrument_metadata"],
            target_sides=item["target_sides"],
            initial_equity_usdt=sleeve_equity,
            target_notional_usdt=sleeve_target,
            max_notional_usdt=sleeve_cap,
            taker_fee_bps_per_side=taker_fee_bps_per_side,
            slippage_bps_per_side=slippage_bps_per_side,
            max_daily_loss_pct=max_daily_loss_pct,
            max_drawdown_pct=max_drawdown_pct,
        )

    if not baseline_timestamps:
        raise ValueError("portfolio markets must contain aligned completed candles")

    portfolio_curve = []
    for index, timestamp in enumerate(baseline_timestamps):
        total_equity = sum(
            results[symbol]["equity_curve"][index]["equity_usdt"]
            for symbol in symbols
        )
        total_exposure = 0.0
        for symbol in symbols:
            point = results[symbol]["equity_curve"][index]
            contracts = point["contracts"]
            if contracts:
                total_exposure += contracts * contract_notional_usdt(
                    markets[symbol]["instrument_metadata"], point["mark_price"]
                )
        portfolio_curve.append({
            "timestamp": timestamp,
            "equity_usdt": total_equity,
            "gross_notional_usdt": total_exposure,
            "gross_exposure_pct_of_starting_equity": total_exposure / initial * 100.0,
        })

    final_equity = sum(results[symbol]["final_equity_usdt"] for symbol in symbols)
    peak = initial
    max_portfolio_drawdown = 0.0
    for point in portfolio_curve:
        peak = max(peak, point["equity_usdt"])
        drawdown = max(0.0, (peak - point["equity_usdt"]) / peak * 100.0)
        max_portfolio_drawdown = max(max_portfolio_drawdown, drawdown)

    return {
        "schema_version": 1,
        "mode": "ring_fenced_multi_symbol_futures_paper_replay",
        "symbols": symbols,
        "market_count": count,
        "initial_equity_usdt": initial,
        "final_equity_usdt": final_equity,
        "net_pnl_usdt": final_equity - initial,
        "return_pct": (final_equity / initial - 1.0) * 100.0,
        "max_portfolio_drawdown_pct": max_portfolio_drawdown,
        "configured_target_total_notional_usdt": target_total,
        "configured_max_total_notional_usdt": cap_total,
        "maximum_observed_gross_notional_usdt": max(
            point["gross_notional_usdt"] for point in portfolio_curve
        ),
        "maximum_observed_gross_exposure_pct": max(
            point["gross_exposure_pct_of_starting_equity"] for point in portfolio_curve
        ),
        "risk_entries_blocked_by_symbol": {
            symbol: results[symbol]["risk_entries_blocked"] for symbol in symbols
        },
        "per_symbol": results,
        "portfolio_equity_curve": portfolio_curve,
        "assumptions": {
            "allocation": "equal, ring-fenced capital sleeves",
            "sleeve_initial_equity_usdt": sleeve_equity,
            "sleeve_target_notional_usdt": sleeve_target,
            "sleeve_max_notional_usdt": sleeve_cap,
            "gross_notional_limit": "sum of sleeve caps; cannot exceed starting equity (1x)",
            "timeline": "all symbols require identical candle timestamps",
            "risk_controls": "daily-loss and drawdown entry guards operate per sleeve, not globally",
            "cross_margin": "not implemented",
            "portfolio_wide_intraday_loss_guard": "not implemented",
            "liquidation_model": "not implemented",
        },
        "live_trading_enabled": False,
    }
