"""Contract-aware accounting primitives for linear USDT perpetual paper research.

No exchange connection, order placement, leverage selection, or liquidation model
is implemented here. Contract values and increments must come from public
instrument metadata; unsupported contract conventions fail closed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN
from math import isfinite
from typing import Any


def _positive_decimal(value: Any, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return parsed


def _instrument_values(metadata: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal, str]:
    if not isinstance(metadata, dict):
        raise ValueError("instrument metadata must be an object")
    if metadata.get("instrument_type") != "SWAP":
        raise ValueError("only perpetual SWAP instruments are supported")
    if metadata.get("contract_type") != "linear":
        raise ValueError("only linear contracts are supported")
    if metadata.get("settlement_currency") != "USDT":
        raise ValueError("only USDT-settled contracts are supported")
    if metadata.get("instrument_state") != "live":
        raise ValueError("instrument is not marked live by the metadata source")

    contract_value = _positive_decimal(metadata.get("contract_value"), "contract_value")
    lot_size = _positive_decimal(metadata.get("lot_size_contracts"), "lot_size_contracts")
    minimum = _positive_decimal(metadata.get("minimum_size_contracts"), "minimum_size_contracts")
    currency = metadata.get("contract_value_currency")
    if not isinstance(currency, str) or not currency:
        raise ValueError("contract_value_currency is required")

    multiplier_raw = metadata.get("contract_multiplier")
    multiplier = Decimal("1") if multiplier_raw in (None, "") else _positive_decimal(
        multiplier_raw, "contract_multiplier"
    )
    return contract_value, lot_size, minimum, currency, multiplier


def contract_notional_usdt(metadata: dict[str, Any], mark_price: float) -> float:
    """Return USDT notional represented by one contract at a mark price."""
    value, _, _, currency, multiplier = _instrument_values(metadata)
    mark = _positive_decimal(mark_price, "mark_price")
    if currency.upper() == "USDT":
        notional = value * multiplier
    else:
        # OKX linear base-currency contracts: contract value is base quantity.
        notional = value * multiplier * mark
    return float(notional)


def contracts_for_notional(
    metadata: dict[str, Any], *, target_notional_usdt: float, mark_price: float
) -> dict[str, float]:
    """Round a target notional down to valid contract lots; never round up."""
    target = _positive_decimal(target_notional_usdt, "target_notional_usdt")
    per_contract = _positive_decimal(
        contract_notional_usdt(metadata, mark_price), "contract_notional_usdt"
    )
    _, lot_size, minimum, _, _ = _instrument_values(metadata)
    raw_contracts = target / per_contract
    lots = (raw_contracts / lot_size).to_integral_value(rounding=ROUND_DOWN)
    contracts = lots * lot_size
    if contracts < minimum:
        raise ValueError("target notional is below the instrument minimum contract size")
    actual_notional = contracts * per_contract
    return {
        "contracts": float(contracts),
        "contract_lot_size": float(lot_size),
        "minimum_contracts": float(minimum),
        "notional_usdt": float(actual_notional),
        "target_notional_usdt": float(target),
        "notional_rounding_residual_usdt": float(target - actual_notional),
        "mark_price": float(_positive_decimal(mark_price, "mark_price")),
        "live_trading_enabled": False,
    }


def linear_pnl_usdt(
    metadata: dict[str, Any], *, contracts: float, entry_price: float,
    mark_price: float, side: str,
) -> float:
    """Unrealized P&L for a linear contract, valued at mark price."""
    value, lot_size, _, currency, multiplier = _instrument_values(metadata)
    size = _positive_decimal(contracts, "contracts")
    if size % lot_size != 0:
        raise ValueError("contracts must be an exact multiple of lot_size_contracts")
    entry = _positive_decimal(entry_price, "entry_price")
    mark = _positive_decimal(mark_price, "mark_price")
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    direction = Decimal("1") if side == "long" else Decimal("-1")

    if currency.upper() == "USDT":
        pnl = size * value * multiplier * (mark - entry) / entry
    else:
        pnl = size * value * multiplier * (mark - entry)
    return float(pnl * direction)


def estimated_taker_fee_usdt(notional_usdt: float, fee_bps: float) -> float:
    """Estimate one-side taker fee; caller must model entry and exit separately."""
    notional = _positive_decimal(notional_usdt, "notional_usdt")
    try:
        bps = Decimal(str(fee_bps))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("fee_bps must be finite and non-negative") from exc
    if not bps.is_finite() or bps < 0:
        raise ValueError("fee_bps must be finite and non-negative")
    return float(notional * bps / Decimal("10000"))


def mark_to_market_position(
    metadata: dict[str, Any], *, contracts: float, entry_price: float,
    mark_price: float, side: str, taker_fee_bps: float,
) -> dict[str, float]:
    """Return gross mark P&L and estimated exit fee, without claiming liquidation safety."""
    pnl = linear_pnl_usdt(
        metadata, contracts=contracts, entry_price=entry_price,
        mark_price=mark_price, side=side,
    )
    mark_notional = _positive_decimal(
        contracts, "contracts"
    ) * _positive_decimal(
        contract_notional_usdt(metadata, mark_price), "contract_notional_usdt"
    )
    exit_fee = estimated_taker_fee_usdt(float(mark_notional), taker_fee_bps)
    return {
        "gross_unrealized_pnl_usdt": pnl,
        "estimated_exit_taker_fee_usdt": exit_fee,
        "estimated_unrealized_after_exit_fee_usdt": pnl - exit_fee,
        "mark_notional_usdt": float(mark_notional),
        "live_trading_enabled": False,
    }
