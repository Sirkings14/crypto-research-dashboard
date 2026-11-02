"""Deterministic single-instrument linear USDT perpetual paper replay.

Signals are assumed to be known at each candle open (i.e. produced from prior
completed information). Funding is settled at exact candle-open timestamps
before a new signal is applied, so a position opened at that timestamp does
not receive/pay that settlement. No exchange connection or live execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from futures_contract_math import (
    contracts_for_notional,
    contract_notional_usdt,
    estimated_taker_fee_usdt,
    linear_pnl_usdt,
    validate_linear_usdt_swap_metadata,
)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _price(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive price") from exc
    if not isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive price")
    return result


def _bps(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and non-negative") from exc
    if not isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def _validate_candles(
    trade_candles: list[dict[str, Any]],
    mark_candles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[datetime]]:
    if not isinstance(trade_candles, list) or len(trade_candles) < 2:
        raise ValueError("at least two completed trade candles are required")
    if not isinstance(mark_candles, list) or len(mark_candles) != len(trade_candles):
        raise ValueError("mark-price candles must exactly match trade candle count")

    trades, marks, stamps = [], [], []
    for index, (trade, mark) in enumerate(zip(trade_candles, mark_candles)):
        if not isinstance(trade, dict) or not isinstance(mark, dict):
            raise ValueError("candle rows must be objects")
        trade_time = _timestamp(trade.get("timestamp"), "trade timestamp")
        mark_time = _timestamp(mark.get("timestamp"), "mark timestamp")
        if trade_time != mark_time:
            raise ValueError(f"trade/mark timestamp mismatch at row {index}")
        if stamps and trade_time <= stamps[-1]:
            raise ValueError("candle timestamps must be strictly chronological")
        trades.append({
            "timestamp": trade_time,
            "open": _price(trade.get("open"), "trade open"),
            "close": _price(trade.get("close"), "trade close"),
        })
        marks.append({
            "open": _price(mark.get("open"), "mark open"),
            "close": _price(mark.get("close"), "mark close"),
        })
        stamps.append(trade_time)
    deltas = [(stamps[i] - stamps[i - 1]).total_seconds() for i in range(1, len(stamps))]
    if len(set(deltas)) != 1:
        raise ValueError("candle series contains gaps or inconsistent intervals")
    return trades, marks, stamps


def run_futures_paper_replay(
    *,
    symbol: str,
    trade_candles: list[dict[str, Any]],
    mark_candles: list[dict[str, Any]],
    funding_history: list[dict[str, Any]],
    instrument_metadata: dict[str, Any],
    target_sides: list[str],
    initial_equity_usdt: float = 1_000.0,
    target_notional_usdt: float = 100.0,
    max_notional_usdt: float = 100.0,
    taker_fee_bps_per_side: float = 5.0,
    slippage_bps_per_side: float = 2.0,
    max_daily_loss_pct: float = 3.0,
    max_drawdown_pct: float = 10.0,
) -> dict[str, Any]:
    """Replay long/short/flat targets with mark P&L, realized funding and costs.

    The target signal at candle i is executed at that candle's trade open.
    Funding at the same timestamp is applied first to the position carried
    into the timestamp. Exposure is fixed-notional and capped; there is no
    leverage, margin, or liquidation simulation.
    """
    if not isinstance(symbol, str) or not symbol.endswith("USDT") or not symbol[:-4].isalnum() or not symbol.isupper():
        raise ValueError("symbol must be an uppercase USDT-quoted pair")
    if not isinstance(instrument_metadata, dict):
        raise ValueError("instrument metadata must be an object")
    validate_linear_usdt_swap_metadata(instrument_metadata)
    expected_instrument = f"{symbol[:-4]}-USDT-SWAP"
    if instrument_metadata.get("instrument") != expected_instrument:
        raise ValueError("instrument metadata does not match the requested symbol")
    if str(instrument_metadata.get("contract_value_currency", "")).upper() not in {symbol[:-4], "USDT"}:
        raise ValueError("contract value currency is not supported for the requested symbol")
    trades, marks, stamps = _validate_candles(trade_candles, mark_candles)
    if not isinstance(target_sides, list) or len(target_sides) != len(trades):
        raise ValueError("target_sides must have one signal per candle")
    if any(side not in {"flat", "long", "short"} for side in target_sides):
        raise ValueError("each target side must be 'flat', 'long', or 'short'")
    if not isinstance(funding_history, list):
        raise ValueError("funding_history must be a list")

    initial = _price(initial_equity_usdt, "initial_equity_usdt")
    target = _price(target_notional_usdt, "target_notional_usdt")
    cap = _price(max_notional_usdt, "max_notional_usdt")
    if target > cap:
        raise ValueError("target_notional_usdt cannot exceed max_notional_usdt")
    fee_bps = _bps(taker_fee_bps_per_side, "taker_fee_bps_per_side")
    slip_bps = _bps(slippage_bps_per_side, "slippage_bps_per_side")
    daily_limit = _bps(max_daily_loss_pct, "max_daily_loss_pct")
    drawdown_limit = _bps(max_drawdown_pct, "max_drawdown_pct")
    if daily_limit > 100 or drawdown_limit > 100:
        raise ValueError("risk limits must not exceed 100 percent")

    funding_by_time: dict[datetime, float] = {}
    previous_funding_time = None
    excluded_unrealized = 0
    excluded_outside_window = 0
    for record in funding_history:
        if not isinstance(record, dict):
            raise ValueError("funding records must be objects")
        rate = record.get("realized_rate")
        if rate is None:
            excluded_unrealized += 1
            continue
        stamp = _timestamp(record.get("funding_time"), "funding_time")
        try:
            rate_value = float(rate)
        except (TypeError, ValueError) as exc:
            raise ValueError("realized funding rate must be finite") from exc
        if not isfinite(rate_value):
            raise ValueError("realized funding rate must be finite")
        if previous_funding_time is not None and stamp <= previous_funding_time:
            raise ValueError("funding history must be strictly chronological")
        previous_funding_time = stamp
        if stamp < stamps[0] or stamp > stamps[-1]:
            excluded_outside_window += 1
            continue
        if stamp in funding_by_time:
            raise ValueError("duplicate funding settlement timestamp")
        funding_by_time[stamp] = rate_value

    if any(stamp not in stamps for stamp in funding_by_time):
        raise ValueError("funding settlement does not align to a candle boundary; refusing to approximate")

    cash = initial
    position: dict[str, Any] | None = None
    fees_paid = 0.0
    funding_paid = 0.0
    realized_pnl = 0.0
    trades_log: list[dict[str, Any]] = []
    funding_log: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    peak_equity = initial
    max_dd = 0.0
    daily_open_equity = initial
    current_day = stamps[0].date()
    risk_blocked = False
    risk_block_reason = None

    def equity_at(mark_price: float) -> float:
        unrealized = 0.0
        if position is not None:
            unrealized = linear_pnl_usdt(
                instrument_metadata, contracts=position["contracts"],
                entry_price=position["entry_price"], mark_price=mark_price,
                side=position["side"],
            )
        return cash + unrealized

    def close_position(timestamp: datetime, raw_price: float, reason: str) -> None:
        nonlocal cash, fees_paid, realized_pnl, position
        if position is None:
            return
        side = position["side"]
        fill = raw_price * (1.0 - slip_bps / 10_000) if side == "long" else raw_price * (1.0 + slip_bps / 10_000)
        pnl = linear_pnl_usdt(
            instrument_metadata, contracts=position["contracts"],
            entry_price=position["entry_price"], mark_price=fill, side=side,
        )
        notional = position["contracts"] * contract_notional_usdt(instrument_metadata, fill)
        fee = estimated_taker_fee_usdt(notional, fee_bps)
        cash += pnl - fee
        realized_pnl += pnl
        fees_paid += fee
        trades_log.append({
            "timestamp": timestamp.isoformat(), "action": "close", "side": side,
            "contracts": position["contracts"], "fill_price": fill,
            "gross_realized_pnl_usdt": pnl, "fee_usdt": fee,
            "reason": reason,
        })
        position = None

    for index, (trade, mark, timestamp, desired_side) in enumerate(zip(trades, marks, stamps, target_sides)):
        if timestamp.date() != current_day:
            daily_open_equity = equity_at(mark["open"])
            current_day = timestamp.date()

        # Settlement applies only to the position carried into this timestamp.
        if timestamp in funding_by_time and position is not None:
            rate = funding_by_time[timestamp]
            mark_notional = position["contracts"] * contract_notional_usdt(
                instrument_metadata, mark["open"]
            )
            direction = 1.0 if position["side"] == "long" else -1.0
            cost = mark_notional * rate * direction
            cash -= cost
            funding_paid += cost
            funding_log.append({
                "funding_time": timestamp.isoformat(), "side": position["side"],
                "realized_rate": rate, "mark_notional_usdt": mark_notional,
                "funding_cost_usdt": cost,
            })

        if position is not None and position["side"] != desired_side:
            close_position(timestamp, trade["open"], "signal_change")

        current_equity = equity_at(mark["open"])
        daily_loss_pct = max(0.0, (daily_open_equity - current_equity) / daily_open_equity * 100.0)
        current_drawdown = max(0.0, (peak_equity - current_equity) / peak_equity * 100.0)
        if daily_loss_pct >= daily_limit:
            risk_blocked, risk_block_reason = True, "daily_loss_limit"
        if current_drawdown >= drawdown_limit:
            risk_blocked, risk_block_reason = True, "max_drawdown_limit"

        if desired_side != "flat" and position is None and not risk_blocked:
            fill = trade["open"] * (1.0 + slip_bps / 10_000) if desired_side == "long" else trade["open"] * (1.0 - slip_bps / 10_000)
            sizing = contracts_for_notional(
                instrument_metadata, target_notional_usdt=target,
                mark_price=fill,
            )
            if sizing["notional_usdt"] > cap + 1e-9:
                raise ValueError("rounded position exceeds configured notional cap")
            side = desired_side
            contracts = sizing["contracts"]
            entry_notional = contracts * contract_notional_usdt(instrument_metadata, fill)
            fee = estimated_taker_fee_usdt(entry_notional, fee_bps)
            cash -= fee
            fees_paid += fee
            position = {
                "side": side, "contracts": contracts, "entry_price": fill,
                "entry_timestamp": timestamp.isoformat(),
            }
            trades_log.append({
                "timestamp": timestamp.isoformat(), "action": "open", "side": side,
                "contracts": contracts, "fill_price": fill,
                "notional_usdt": entry_notional, "fee_usdt": fee,
                "reason": "target_signal",
            })

        mark_equity = equity_at(mark["close"])
        peak_equity = max(peak_equity, mark_equity)
        drawdown = max(0.0, (peak_equity - mark_equity) / peak_equity * 100.0)
        max_dd = max(max_dd, drawdown)
        equity_curve.append({
            "timestamp": timestamp.isoformat(), "equity_usdt": mark_equity,
            "cash_usdt": cash, "mark_price": mark["close"],
            "position_side": position["side"] if position else "flat",
            "contracts": position["contracts"] if position else 0.0,
            "gross_notional_open_usdt": (
                position["contracts"] * contract_notional_usdt(
                    instrument_metadata, mark["open"]
                ) if position else 0.0
            ),
            "gross_notional_usdt": (
                position["contracts"] * contract_notional_usdt(
                    instrument_metadata, mark["close"]
                ) if position else 0.0
            ),
            "drawdown_pct": drawdown,
        })

    if position is not None:
        close_position(stamps[-1], trades[-1]["close"], "end_of_replay")
        final_equity = cash
        if equity_curve:
            equity_curve[-1]["equity_usdt"] = final_equity
            equity_curve[-1]["cash_usdt"] = cash
            equity_curve[-1]["position_side"] = "flat"
            equity_curve[-1]["contracts"] = 0.0
            equity_curve[-1]["gross_notional_open_usdt"] = 0.0
            equity_curve[-1]["gross_notional_usdt"] = 0.0
    else:
        final_equity = cash

    return {
        "schema_version": 1,
        "mode": "futures_paper_replay_only",
        "symbol": symbol,
        "settlement_currency": "USDT",
        "initial_equity_usdt": initial,
        "final_equity_usdt": final_equity,
        "net_pnl_usdt": final_equity - initial,
        "return_pct": (final_equity / initial - 1.0) * 100.0,
        "realized_gross_pnl_usdt": realized_pnl,
        "fees_paid_usdt": fees_paid,
        "net_funding_cost_usdt": funding_paid,
        "trade_event_count": len(trades_log),
        "funding_settlement_count": len(funding_log),
        "excluded_unrealized_funding_records": excluded_unrealized,
        "excluded_funding_outside_replay_window": excluded_outside_window,
        "max_drawdown_pct": max_dd,
        "risk_entries_blocked": risk_blocked,
        "risk_block_reason": risk_block_reason,
        "trades": trades_log,
        "funding": funding_log,
        "equity_curve": equity_curve,
        "assumptions": {
            "target_notional_usdt": target,
            "maximum_notional_usdt": cap,
            "leverage": 1,
            "taker_fee_bps_per_side": fee_bps,
            "slippage_bps_per_side": slip_bps,
            "execution": "trade candle open with adverse fixed slippage",
            "valuation": "mark-price candle close",
            "funding": "realized rates only; exact candle-open settlements; prior position only",
            "risk_limits": {
                "max_daily_loss_pct": daily_limit,
                "max_drawdown_pct": drawdown_limit,
                "behavior": "block new entries; existing positions still follow signals and are closed at replay end",
            },
            "liquidation_model": "not implemented",
            "margin_model": "not implemented; leverage fixed at 1x for paper assumptions",
        },
        "live_trading_enabled": False,
    }
