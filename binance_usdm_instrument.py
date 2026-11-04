"""Read-only Binance USD-M perpetual instrument metadata.

Binance USD-M order quantity is expressed in base-asset units. The normalized
contract fields below deliberately encode one quantity unit as one base unit
so the existing linear-contract accounting primitives can size it correctly.
No account endpoints, credentials, leverage changes, or order methods exist.
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

BASE_URL = "https://fapi.binance.com"
EXCHANGE_INFO_URL = f"{BASE_URL}/fapi/v1/exchangeInfo"


def _decimal(value: Any, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not parsed.is_finite() or (parsed < 0 if allow_zero else parsed <= 0):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ValueError(f"{field} must be finite and {qualifier}")
    return parsed


def _request_exchange_info(*, session=None, retries: int = 3) -> dict[str, Any]:
    if not 1 <= retries <= 5:
        raise ValueError("retries must be between 1 and 5")
    client = session or requests.Session()
    if hasattr(client, "headers"):
        client.headers.update({"User-Agent": "CryptoResearchLab/3.0"})
    for attempt in range(retries):
        response = client.get(EXCHANGE_INFO_URL, timeout=20)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 == retries:
                response.raise_for_status()
            try:
                delay = min(max(float(response.headers.get("Retry-After", "1")), 1), 10)
            except (TypeError, ValueError):
                delay = 1
            time.sleep(delay)
            continue
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
            raise ValueError("Binance exchangeInfo response is missing its symbols list")
        return payload
    raise RuntimeError("Binance exchangeInfo request exhausted retries")


def parse_usdm_instrument_metadata(exchange_info: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Normalize one active linear USDT perpetual from /fapi/v1/exchangeInfo."""
    if not isinstance(symbol, str) or not symbol.isalnum() or symbol != symbol.upper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if not isinstance(exchange_info, dict) or not isinstance(exchange_info.get("symbols"), list):
        raise ValueError("exchange_info must contain a symbols list")

    matches = [item for item in exchange_info["symbols"] if isinstance(item, dict) and item.get("symbol") == symbol]
    if len(matches) != 1:
        raise ValueError(f"{symbol}: expected exactly one matching exchange symbol")
    item = matches[0]

    if item.get("status") != "TRADING":
        raise ValueError(f"{symbol}: exchange status is not TRADING")
    if item.get("contractType") != "PERPETUAL":
        raise ValueError(f"{symbol}: only PERPETUAL contracts are supported")
    base = item.get("baseAsset")
    if not isinstance(base, str) or not base or item.get("quoteAsset") != "USDT" or item.get("marginAsset") != "USDT":
        raise ValueError(f"{symbol}: only linear USDT-quoted and USDT-margined contracts are supported")

    filters = item.get("filters")
    if not isinstance(filters, list):
        raise ValueError(f"{symbol}: exchange filters are missing")
    by_type = {f.get("filterType"): f for f in filters if isinstance(f, dict) and isinstance(f.get("filterType"), str)}
    required = ("PRICE_FILTER", "LOT_SIZE", "MARKET_LOT_SIZE", "MIN_NOTIONAL")
    missing = [name for name in required if name not in by_type]
    if missing:
        raise ValueError(f"{symbol}: missing required filters: {', '.join(missing)}")

    price = by_type["PRICE_FILTER"]
    lot = by_type["LOT_SIZE"]
    market_lot = by_type["MARKET_LOT_SIZE"]
    notional = by_type["MIN_NOTIONAL"]

    tick_size = _decimal(price.get("tickSize"), "PRICE_FILTER.tickSize")
    min_qty = _decimal(lot.get("minQty"), "LOT_SIZE.minQty")
    max_qty = _decimal(lot.get("maxQty"), "LOT_SIZE.maxQty")
    step_size = _decimal(lot.get("stepSize"), "LOT_SIZE.stepSize")
    market_min_qty = _decimal(market_lot.get("minQty"), "MARKET_LOT_SIZE.minQty")
    market_max_qty = _decimal(market_lot.get("maxQty"), "MARKET_LOT_SIZE.maxQty")
    market_step_size = _decimal(market_lot.get("stepSize"), "MARKET_LOT_SIZE.stepSize")
    min_notional = _decimal(notional.get("notional"), "MIN_NOTIONAL.notional")

    if min_qty > max_qty or market_min_qty > market_max_qty:
        raise ValueError(f"{symbol}: quantity filter minimum exceeds maximum")

    # Binance USD-M quantity is base-asset quantity, not an OKX contract count.
    return {
        "provider": "binance_usdm_exchange_info",
        "symbol": symbol,
        "instrument_type": "SWAP",
        "contract_type": "linear",
        "contract_type_source": item.get("contractType"),
        "settlement_currency": "USDT",
        "instrument_state": "live",
        "exchange_status": item.get("status"),
        "base_asset": base,
        "quote_asset": "USDT",
        "margin_asset": "USDT",
        "contract_value": "1",
        "contract_value_currency": base,
        "contract_multiplier": "1",
        "contract_unit": "base_asset_quantity",
        "lot_size_contracts": str(step_size),
        "minimum_size_contracts": str(min_qty),
        "maximum_size_contracts": str(max_qty),
        "market_lot_size": {
            "step_size": str(market_step_size),
            "minimum_quantity": str(market_min_qty),
            "maximum_quantity": str(market_max_qty),
        },
        "price_tick_size": str(tick_size),
        "minimum_notional_usdt": str(min_notional),
        "liquidation_fee_rate_reference": item.get("liquidationFee"),
        "exchange_maintenance_margin_percent_reference": item.get("maintMarginPercent"),
        "maintenance_tier_source": "not_provided_by_exchangeInfo",
        "live_trading_enabled": False,
    }


def fetch_usdm_instrument_metadata(symbol: str, *, session=None, retries: int = 3) -> dict[str, Any]:
    """Fetch and normalize current Binance USD-M metadata for one symbol."""
    payload = _request_exchange_info(session=session, retries=retries)
    return parse_usdm_instrument_metadata(payload, symbol)
