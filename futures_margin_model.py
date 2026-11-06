"""Conservative isolated-margin risk estimates for linear USDT perpetuals.

This module is an analytical calculator, not an exchange liquidation replica.
It uses one caller-supplied maintenance-margin rate and liquidation-fee
reserve; it does not model exchange tiers, mark-price triggers, insurance funds,
ADL, partial liquidation, cross margin, or intrabar paths.
"""
from __future__ import annotations

from math import isfinite
from typing import Any

from futures_contract_math import contract_notional_usdt, linear_pnl_usdt, validate_linear_usdt_swap_metadata


def _positive(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return parsed


def _non_negative(value: Any, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and non-negative") from exc
    if not isfinite(parsed) or parsed < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return parsed


def _rate(value: Any, field: str) -> float:
    parsed = _non_negative(value, field)
    if parsed >= 1:
        raise ValueError(f"{field} must be less than 1")
    return parsed


def assess_isolated_margin(
    *,
    metadata: dict[str, Any],
    contracts: float,
    entry_price: float,
    mark_price: float,
    side: str,
    isolated_margin_usdt: float,
    maintenance_margin_rate: float,
    liquidation_fee_rate: float = 0.0,
    accrued_costs_usdt: float = 0.0,
) -> dict[str, Any]:
    """Assess a linear position against a single explicit maintenance rate.

    accrued_costs_usdt is a positive net cost (e.g. entry fees and funding
    debits); funding credits may be represented by a negative value.
    """
    validate_linear_usdt_swap_metadata(metadata)
    size = _positive(contracts, "contracts")
    entry = _positive(entry_price, "entry_price")
    mark = _positive(mark_price, "mark_price")
    margin = _positive(isolated_margin_usdt, "isolated_margin_usdt")
    mmr = _rate(maintenance_margin_rate, "maintenance_margin_rate")
    liq_fee = _rate(liquidation_fee_rate, "liquidation_fee_rate")
    if mmr + liq_fee >= 1:
        raise ValueError("maintenance margin plus liquidation fee rate must be less than 1")
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    try:
        costs = float(accrued_costs_usdt)
    except (TypeError, ValueError) as exc:
        raise ValueError("accrued_costs_usdt must be finite") from exc
    if not isfinite(costs):
        raise ValueError("accrued_costs_usdt must be finite")

    entry_notional = size * contract_notional_usdt(metadata, entry)
    mark_notional = size * contract_notional_usdt(metadata, mark)
    base_quantity = entry_notional / entry
    effective_leverage = entry_notional / margin
    if effective_leverage < 1.0 - 1e-12:
        raise ValueError("isolated margin cannot exceed position notional under this model")

    unrealized = linear_pnl_usdt(
        metadata, contracts=size, entry_price=entry, mark_price=mark, side=side
    )
    margin_equity = margin + unrealized - costs
    maintenance = mark_notional * mmr
    liquidation_reserve = mark_notional * liq_fee
    required_equity = maintenance + liquidation_reserve
    safe_equity = margin_equity - required_equity
    margin_ratio = margin_equity / mark_notional

    if side == "long":
        numerator = base_quantity * entry - margin + costs
        denominator = base_quantity * (1.0 - mmr - liq_fee)
    else:
        numerator = base_quantity * entry + margin - costs
        denominator = base_quantity * (1.0 + mmr + liq_fee)
    liquidation_price = numerator / denominator if numerator > 0 else None

    return {
        "side": side,
        "contracts": size,
        "entry_price": entry,
        "mark_price": mark,
        "entry_notional_usdt": entry_notional,
        "mark_notional_usdt": mark_notional,
        "isolated_margin_usdt": margin,
        "effective_leverage": effective_leverage,
        "unrealized_pnl_usdt": unrealized,
        "accrued_costs_usdt": costs,
        "margin_equity_usdt": margin_equity,
        "maintenance_margin_rate": mmr,
        "maintenance_margin_usdt": maintenance,
        "liquidation_fee_rate": liq_fee,
        "estimated_liquidation_fee_reserve_usdt": liquidation_reserve,
        "required_equity_usdt": required_equity,
        "equity_buffer_usdt": safe_equity,
        "margin_ratio": margin_ratio,
        "estimated_liquidation_price": liquidation_price,
        "liquidation_threshold_breached": safe_equity <= 0,
        "model": "isolated_linear_single_maintenance_rate",
        "live_trading_enabled": False,
    }
