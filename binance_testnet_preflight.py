"""Pure, conservative sizing preflight for Binance USD-M Futures Testnet."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from typing import Any


class PreflightError(ValueError):
    """Raised when exchange metadata cannot safely support an order."""


def _d(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PreflightError(f"{label} is invalid") from exc
    if not result.is_finite() or result <= 0:
        raise PreflightError(f"{label} must be finite and positive")
    return result


def market_order_preflight(
    exchange_info: dict[str, Any],
    ticker_price: Any,
    symbol: str,
    *,
    max_notional_usdt: Any = "25",
) -> dict[str, str]:
    """Validate a USDT perpetual and calculate a step-aligned MARKET quantity."""
    cap = _d(max_notional_usdt, "notional cap")
    if cap > Decimal("25"):
        raise PreflightError("notional cap cannot exceed 25 USDT")
    price = _d(ticker_price, "Testnet ticker price")
    if not isinstance(symbol, str) or not symbol.endswith("USDT") or symbol != symbol.upper():
        raise PreflightError("symbol must be an uppercase USDT pair")

    symbols = exchange_info.get("symbols") if isinstance(exchange_info, dict) else None
    if not isinstance(symbols, list):
        raise PreflightError("exchangeInfo symbols list is missing")
    matches = [item for item in symbols if isinstance(item, dict) and item.get("symbol") == symbol]
    if len(matches) != 1:
        raise PreflightError("symbol is missing or duplicated in Testnet exchangeInfo")
    info = matches[0]
    if info.get("status") != "TRADING":
        raise PreflightError("symbol is not TRADING on Testnet")
    if info.get("quoteAsset") != "USDT" or info.get("contractType") != "PERPETUAL":
        raise PreflightError("symbol is not a USDT perpetual contract")
    if "MARKET" not in info.get("orderTypes", []):
        raise PreflightError("MARKET orders are not allowed for this symbol")

    filters = info.get("filters")
    if not isinstance(filters, list):
        raise PreflightError("symbol filters are missing")
    by_type = {f.get("filterType"): f for f in filters if isinstance(f, dict)}
    lot = by_type.get("MARKET_LOT_SIZE") or by_type.get("LOT_SIZE")
    if not isinstance(lot, dict):
        raise PreflightError("market lot-size filter is missing")
    step = _d(lot.get("stepSize"), "stepSize")
    min_qty = _d(lot.get("minQty"), "minQty")
    max_qty = _d(lot.get("maxQty"), "maxQty")

    min_notional = Decimal("0")
    for filter_name in ("MIN_NOTIONAL", "NOTIONAL"):
        f = by_type.get(filter_name)
        if f:
            raw = f.get("notional", f.get("minNotional"))
            if raw is not None:
                candidate = _d(raw, "minimum notional")
                min_notional = max(min_notional, candidate)

    raw_qty = cap / price
    qty = (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    if qty < min_qty:
        raise PreflightError("25 USDT cap cannot meet the symbol minimum quantity")
    if qty > max_qty:
        qty = (max_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    notional = qty * price
    if qty <= 0 or notional > cap:
        raise PreflightError("no valid quantity fits within the notional cap")
    if notional < min_notional:
        raise PreflightError("25 USDT cap cannot meet the symbol minimum notional")
    return {
        "symbol": symbol,
        "reference_price": format(price, "f"),
        "quantity": format(qty, "f"),
        "estimated_notional_usdt": format(notional, "f"),
        "step_size": format(step, "f"),
        "min_quantity": format(min_qty, "f"),
        "max_quantity": format(max_qty, "f"),
        "min_notional_usdt": format(min_notional, "f"),
        "order_submission": "DISABLED",
    }
