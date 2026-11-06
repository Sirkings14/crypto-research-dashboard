"""Minimum-lot-aware portfolio allocation for linear USDT Futures research.

This module allocates a shared notional budget without rounding exposure up.
It does not place orders or model margin/liquidation.
"""
from __future__ import annotations

from math import isfinite
from typing import Any


def _positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite and {'non-negative' if allow_zero else 'positive'}") from exc
    if not isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError(f"{field} must be finite and {'non-negative' if allow_zero else 'positive'}")
    return number


def allocate_portfolio_notional(
    *,
    minimum_notionals_usdt: dict[str, float],
    target_total_notional_usdt: float,
    max_total_notional_usdt: float,
    weights: dict[str, float] | None = None,
    max_symbol_allocation_pct: float = 50.0,
) -> dict[str, Any]:
    """Allocate target and maximum notional with weighted water-filling.

    Minimum valid contract notionals are reserved first. Remaining budget is
    distributed by positive weights, subject to a per-symbol concentration cap
    expressed as a percentage of the corresponding portfolio budget.
    """
    if not isinstance(minimum_notionals_usdt, dict) or not minimum_notionals_usdt:
        raise ValueError("minimum_notionals_usdt must be a non-empty mapping")
    symbols = sorted(minimum_notionals_usdt)
    if any(not isinstance(s, str) or not s for s in symbols):
        raise ValueError("symbol keys must be non-empty strings")
    minimums = {
        symbol: _positive(minimum_notionals_usdt[symbol], f"{symbol} minimum notional")
        for symbol in symbols
    }
    target = _positive(target_total_notional_usdt, "target_total_notional_usdt")
    maximum = _positive(max_total_notional_usdt, "max_total_notional_usdt")
    if target > maximum + 1e-9:
        raise ValueError("target total cannot exceed maximum total")
    minimum_total = sum(minimums.values())
    if minimum_total > target + 1e-9:
        raise ValueError("combined minimum contract notional exceeds target budget")
    if minimum_total > maximum + 1e-9:
        raise ValueError("combined minimum contract notional exceeds maximum budget")

    concentration = _positive(
        max_symbol_allocation_pct, "max_symbol_allocation_pct"
    )
    if concentration > 100:
        raise ValueError("max_symbol_allocation_pct cannot exceed 100")

    if weights is None:
        weights = {symbol: 1.0 for symbol in symbols}
    if not isinstance(weights, dict) or set(weights) != set(symbols):
        raise ValueError("weights must contain exactly one entry for every market symbol")
    clean_weights = {
        symbol: _positive(weights[symbol], f"{symbol} weight")
        for symbol in symbols
    }

    def distribute(budget: float, label: str) -> dict[str, float]:
        caps = {s: budget * concentration / 100.0 for s in symbols}
        for symbol in symbols:
            if minimums[symbol] > caps[symbol] + 1e-9:
                raise ValueError(
                    f"{symbol} minimum contract notional exceeds its {label} concentration cap"
                )
        allocations = dict(minimums)
        remaining = max(0.0, budget - minimum_total)
        active = {s for s in symbols if allocations[s] < caps[s] - 1e-9}
        # Weighted water-filling: saturated symbols leave the active pool.
        while remaining > 1e-9 and active:
            weight_sum = sum(clean_weights[s] for s in active)
            proposed = {
                s: remaining * clean_weights[s] / weight_sum for s in active
            }
            consumed = 0.0
            saturated = set()
            for symbol in active:
                room = max(0.0, caps[symbol] - allocations[symbol])
                addition = min(room, proposed[symbol])
                allocations[symbol] += addition
                consumed += addition
                if room <= proposed[symbol] + 1e-9:
                    saturated.add(symbol)
            remaining -= consumed
            active -= saturated
            if consumed <= 1e-12:
                break
        if remaining > 1e-7:
            raise ValueError(
                f"{label} budget cannot be allocated under the configured concentration cap"
            )
        # Floating point guard: never return an allocation above its cap/budget.
        if sum(allocations.values()) > budget + 1e-7:
            raise ValueError(f"{label} allocations exceed the portfolio budget")
        return allocations

    target_allocations = distribute(target, "target")
    maximum_allocations = distribute(maximum, "maximum")
    return {
        "symbols": symbols,
        "minimum_total_notional_usdt": minimum_total,
        "target_total_notional_usdt": target,
        "maximum_total_notional_usdt": maximum,
        "max_symbol_allocation_pct": concentration,
        "weights": clean_weights,
        "target_allocations_usdt": target_allocations,
        "maximum_allocations_usdt": maximum_allocations,
        "target_allocation_total_usdt": sum(target_allocations.values()),
        "maximum_allocation_total_usdt": sum(maximum_allocations.values()),
        "live_trading_enabled": False,
    }
