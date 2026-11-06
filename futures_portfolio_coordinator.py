"""Ring-fenced multi-symbol Futures paper replay with a portfolio risk breaker.

The coordinator funds each instrument's minimum valid contract lot first,
then uses weighted water-filling to allocate target and maximum notional under
per-symbol concentration caps. A synchronized first pass detects portfolio-
wide loss/drawdown breaches; a second pass latches the breaker and closes all
sleeves at the next candle open. This remains a research replay, not a cross-
margin or liquidation model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from math import ceil

from futures_contract_math import (
    contract_notional_usdt,
    validate_linear_usdt_swap_metadata,
)
from futures_paper_ledger import run_futures_paper_replay
from futures_portfolio_sizing import allocate_portfolio_notional


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
    allocation_weights: dict[str, float] | None = None,
    max_symbol_allocation_pct: float = 100.0,
) -> dict[str, Any]:
    """Replay synchronized symbols with a latched portfolio-wide risk breaker.

    A completed-candle portfolio equity breach is detected on the first pass.
    On the next candle open, every sleeve is forced flat and all later entries
    are blocked. Minimum contract lots are funded first; weighted water-filling
    allocates the remaining target/cap under per-symbol concentration limits.
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
    baseline_timestamps: list[str] | None = None
    results: dict[str, dict[str, Any]] = {}
    minimum_notionals: dict[str, float] = {}

    # Validate data alignment and instrument identity before attempting sizing.
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
        if not stamps:
            raise ValueError(f"{symbol} must contain completed candles")

        metadata = item["instrument_metadata"]
        validate_linear_usdt_swap_metadata(metadata)
        expected_instruments = {f"{symbol[:-4]}-USDT-SWAP", symbol}
        if metadata.get("instrument") not in expected_instruments:
            raise ValueError(
                f"{symbol} instrument metadata {metadata.get('instrument')!r} does not match "
                f"expected one of {sorted(expected_instruments)!r}"
            )
        if metadata.get("provider") == "binance_usdm_exchange_info" and metadata.get("instrument") != symbol:
            raise ValueError(f"{symbol} Binance USD-M metadata must identify the native symbol")
        per_contract = contract_notional_usdt(metadata, marks[0].get("close"))
        lot_size = _positive(metadata.get("lot_size_contracts"), f"{symbol} lot_size_contracts")
        minimum_contracts = _positive(
            metadata.get("minimum_size_contracts"), f"{symbol} minimum_size_contracts"
        )
        minimum_lots = ceil(minimum_contracts / lot_size)
        minimum_required_notional = float(metadata.get("minimum_notional_usdt", 0) or 0)
        if not isfinite(minimum_required_notional) or minimum_required_notional < 0:
            raise ValueError(f"{symbol} minimum_notional_usdt must be finite and non-negative")
        if minimum_required_notional:
            notional_lots = ceil(minimum_required_notional / (lot_size * per_contract))
            minimum_lots = max(minimum_lots, notional_lots)
        minimum_notionals[symbol] = minimum_lots * lot_size * per_contract

    if not baseline_timestamps:
        raise ValueError("portfolio markets must contain aligned completed candles")
    minimum_total = sum(minimum_notionals.values())
    if target_total + 1e-9 < minimum_total:
        raise ValueError(
            "target_total_notional_usdt is below the combined minimum contract "
            f"notional across markets ({minimum_total:.8f} USDT)"
        )
    if cap_total + 1e-9 < minimum_total:
        raise ValueError(
            "max_total_notional_usdt is below the combined minimum contract "
            f"notional across markets ({minimum_total:.8f} USDT)"
        )

    allocation_plan = allocate_portfolio_notional(
        minimum_notionals_usdt=minimum_notionals,
        target_total_notional_usdt=target_total,
        max_total_notional_usdt=cap_total,
        weights=allocation_weights,
        max_symbol_allocation_pct=max_symbol_allocation_pct,
    )
    target_allocations = allocation_plan["target_allocations_usdt"]
    cap_allocations = allocation_plan["maximum_allocations_usdt"]
    # Ring-fenced sleeve capital follows each symbol's share of the maximum
    # portfolio budget. This guarantees no sleeve is assigned more notional
    # capacity than its own starting equity under the 1x research assumption.
    sleeve_equity_by_symbol = {
        symbol: initial * cap_allocations[symbol] / cap_total
        for symbol in symbols
    }
    for symbol in symbols:
        if cap_allocations[symbol] > sleeve_equity_by_symbol[symbol] + 1e-7:
            raise ValueError(f"{symbol} notional cap exceeds its ring-fenced sleeve equity")

    def replay_all(signal_overrides: dict[str, list[str]] | None = None):
        replayed = {}
        for symbol in symbols:
            item = markets[symbol]
            replayed[symbol] = run_futures_paper_replay(
                symbol=symbol,
                trade_candles=item["trade_candles"],
                mark_candles=item["mark_candles"],
                funding_history=item["funding_history"],
                instrument_metadata=item["instrument_metadata"],
                target_sides=(signal_overrides or {}).get(symbol, item["target_sides"]),
                initial_equity_usdt=sleeve_equity_by_symbol[symbol],
                target_notional_usdt=target_allocations[symbol],
                max_notional_usdt=cap_allocations[symbol],
                taker_fee_bps_per_side=taker_fee_bps_per_side,
                slippage_bps_per_side=slippage_bps_per_side,
                # Sleeve-level guards are intentionally neutralized. The
                # synchronized controller below owns the portfolio limits.
                max_daily_loss_pct=100.0,
                max_drawdown_pct=100.0,
            )
        return replayed

    def aggregate_curve(replayed):
        curve = []
        for index, timestamp in enumerate(baseline_timestamps):
            total_equity = sum(
                replayed[symbol]["equity_curve"][index]["equity_usdt"]
                for symbol in symbols
            )
            total_open_exposure = sum(
                replayed[symbol]["equity_curve"][index]["gross_notional_open_usdt"]
                for symbol in symbols
            )
            total_close_exposure = sum(
                replayed[symbol]["equity_curve"][index]["gross_notional_usdt"]
                for symbol in symbols
            )
            curve.append({
                "timestamp": timestamp,
                "equity_usdt": total_equity,
                "gross_notional_open_usdt": total_open_exposure,
                "gross_notional_usdt": total_close_exposure,
                "gross_exposure_pct_of_starting_equity": total_close_exposure / initial * 100.0,
            })
        return curve

    # First pass identifies the earliest completed-candle global breach.
    exploratory = replay_all()
    exploratory_curve = aggregate_curve(exploratory)
    peak = initial
    daily_open_equity = initial
    current_day = datetime.fromisoformat(
        baseline_timestamps[0].replace("Z", "+00:00")
    ).astimezone(timezone.utc).date()
    breaker_index = None
    breaker_exit_index = None
    breaker_execution = None
    breaker_reason = None
    breaker_metrics = None
    max_portfolio_drawdown = 0.0
    for index, point in enumerate(exploratory_curve):
        day = datetime.fromisoformat(
            point["timestamp"].replace("Z", "+00:00")
        ).astimezone(timezone.utc).date()
        if day != current_day:
            daily_open_equity = exploratory_curve[index - 1]["equity_usdt"]
            current_day = day
        equity = point["equity_usdt"]
        peak = max(peak, equity)
        daily_loss_pct = max(0.0, (daily_open_equity - equity) / daily_open_equity * 100.0)
        drawdown_pct = max(0.0, (peak - equity) / peak * 100.0)
        max_portfolio_drawdown = max(max_portfolio_drawdown, drawdown_pct)
        gross_notional = point["gross_notional_usdt"]
        open_gross_notional = point["gross_notional_open_usdt"]
        if daily_loss_pct >= max_daily_loss_pct:
            breaker_index, breaker_reason = index, "portfolio_daily_loss_limit"
            breaker_exit_index = min(index + 1, len(baseline_timestamps) - 1)
            breaker_execution = (
                "next_candle_open" if index < len(baseline_timestamps) - 1
                else "final_candle_close"
            )
        elif drawdown_pct >= max_drawdown_pct:
            breaker_index, breaker_reason = index, "portfolio_max_drawdown_limit"
            breaker_exit_index = min(index + 1, len(baseline_timestamps) - 1)
            breaker_execution = (
                "next_candle_open" if index < len(baseline_timestamps) - 1
                else "final_candle_close"
            )
        elif open_gross_notional > cap_total + 1e-7:
            # A gap or new entry can breach the cap at this candle's open.
            # The second pass forces all sleeves flat at this same open.
            breaker_index, breaker_reason = index, "portfolio_gross_exposure_limit"
            breaker_exit_index = index
            breaker_execution = "same_candle_open"
        elif gross_notional > cap_total + 1e-7:
            # A completed close breach is acted on at the next available open.
            breaker_index, breaker_reason = index, "portfolio_gross_exposure_limit"
            breaker_exit_index = min(index + 1, len(baseline_timestamps) - 1)
            breaker_execution = (
                "next_candle_open" if index < len(baseline_timestamps) - 1
                else "final_candle_close"
            )
        if breaker_index is not None:
            breaker_metrics = {
                "daily_loss_pct": daily_loss_pct,
                "drawdown_pct": drawdown_pct,
                "equity_usdt": equity,
                "gross_notional_open_usdt": open_gross_notional,
                "gross_notional_usdt": gross_notional,
                "gross_notional_limit_usdt": cap_total,
                "gross_exposure_pct_of_starting_equity": (
                    gross_notional / initial * 100.0
                ),
            }
            break

    breaker_triggered = breaker_index is not None
    if breaker_triggered:
        # Open breaches are flattened at that same candle open; completed-close
        # breaches are flattened at the next open. A final-close breach uses
        # the ledger's normal end-of-replay close because no next open exists.
        overridden = {}
        for symbol in symbols:
            original = markets[symbol]["target_sides"]
            if breaker_execution == "final_candle_close":
                overridden[symbol] = list(original)
            else:
                overridden[symbol] = [
                    side if index < breaker_exit_index else "flat"
                    for index, side in enumerate(original)
                ]
        results = replay_all(overridden)
    else:
        results = exploratory

    portfolio_curve = aggregate_curve(results)
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
        "portfolio_risk_breaker": {
            "triggered": breaker_triggered,
            "reason": breaker_reason,
            "detection_timestamp": (
                baseline_timestamps[breaker_index] if breaker_triggered else None
            ),
            "coordinated_exit_timestamp": (
                baseline_timestamps[breaker_exit_index] if breaker_triggered else None
            ),
            "coordinated_exit_execution": breaker_execution,
            "metrics_at_detection": breaker_metrics,
            "behavior": "latch permanently; force all sleeves flat at the next candle open (or final candle close if no next candle exists); block all later entries",
            "gross_exposure_limit_usdt": cap_total,
            "daily_loss_limit_pct": max_daily_loss_pct,
            "max_drawdown_limit_pct": max_drawdown_pct,
        },
        "configured_target_total_notional_usdt": target_total,
        "configured_max_total_notional_usdt": cap_total,
        "portfolio_sizing": allocation_plan,
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
            "allocation": "ring-fenced capital sleeves with minimum contract lots funded first",
            "sleeve_initial_equity_by_symbol_usdt": sleeve_equity_by_symbol,
            "allocation_method": "minimum valid contract lot per market, then weighted water-filling of remaining budget subject to per-symbol concentration caps",
            "allocation_weights": allocation_plan["weights"],
            "max_symbol_allocation_pct": max_symbol_allocation_pct,
            "minimum_notional_by_symbol_usdt": minimum_notionals,
            "target_notional_by_symbol_usdt": target_allocations,
            "max_notional_by_symbol_usdt": cap_allocations,
            "gross_notional_limit": "sum of sleeve caps; cannot exceed starting equity (1x)",
            "timeline": "all symbols require identical candle timestamps",
            "risk_controls": "synchronized portfolio-wide daily-loss, peak-to-trough drawdown, and mark-notional gross-exposure breaker; all sleeves flatten together at next candle open",
            "gross_exposure_control": "checks combined mark-price notional at every candle open after signal execution and at every completed candle close; an open breach triggers a same-open coordinated flatten, a close breach triggers a next-open flatten, and all later entries are blocked; no partial rebalancing or intrabar protection",
            "cross_margin": "not implemented",
            "portfolio_wide_intraday_loss_guard": "not implemented; checks completed candle marks only",
            "liquidation_model": "not implemented",
        },
        "live_trading_enabled": False,
    }
