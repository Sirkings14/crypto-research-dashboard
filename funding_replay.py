"""Replay realized perpetual funding costs for a fixed notional paper position.

This is an isolated research calculation. It does not simulate price PnL, margin,
liquidation, fees, slippage, changing exposure, or execute orders.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any


def replay_funding_cost(
    records: list[dict[str, Any]],
    *,
    notional_usdt: float,
    side: str,
) -> dict[str, Any]:
    """Calculate realized funding cashflows for constant notional and one side.

    Positive net_funding_cost_usdt means an expense; negative means a net credit.
    Positive realized rates charge longs and credit shorts, and vice versa.
    """
    try:
        notional = Decimal(str(notional_usdt))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("notional_usdt must be a finite positive number") from exc
    if notional <= 0 or notional.is_finite():
        raise ValueError("notional_usdt must be a finite positive number")
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if not isinstance(records, list):
        raise ValueError("records must be a list")

    direction = Decimal("1") if side == "long" else Decimal("-1")
    settlements = []
    previous_time: datetime | None = None
    total_expenses = Decimal("0")
    total_credits = Decimal("0")
    excluded = 0

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("each funding record must be an object")
        realized = record.get("realized_rate")
        if realized is None:
            excluded += 1
            continue
        try:
            rate = Decimal(str(realized))
            stamp = datetime.fromisoformat(str(record["funding_time"]).replace("Z", "+00:00"))
        except (InvalidOperation, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid realized funding record: {exc}") from exc
        if not rate.is_finite():
            raise ValueError("realized_rate must be finite")
        if stamp.tzinfo is None:
            raise ValueError("funding_time must include a timezone")
        if previous_time is not None and stamp <= previous_time:
            raise ValueError("realized funding records must be strictly chronological")
        previous_time = stamp

        cashflow_cost = notional * rate * direction
        if cashflow_cost > 0:
            total_expenses += cashflow_cost
        elif cashflow_cost < 0:
            total_credits += -cashflow_cost
        settlements.append({
            "funding_time": stamp.isoformat(),
            "realized_rate": float(rate),
            "funding_cost_usdt": float(cashflow_cost),
        })

    net_cost = total_expenses - total_credits
    return {
        "schema_version": 1,
        "mode": "fixed_notional_funding_replay_only",
        "side": side,
        "notional_usdt": float(notional),
        "settlement_count": len(settlements),
        "excluded_unrealized_records": excluded,
        "gross_funding_expenses_usdt": float(total_expenses),
        "gross_funding_credits_usdt": float(total_credits),
        "net_funding_cost_usdt": float(net_cost),
        "net_funding_cost_pct_of_notional": float(net_cost / notional * Decimal("100")),
        "settlements": settlements,
        "live_trading_enabled": False,
        "limitations": [
            "constant notional assumed across all settlements",
            "price profit and loss is not included",
            "fees, slippage, margin and liquidation are not included",
            "only realizedRate observations are used; predicted rates are excluded",
        ],
    }
