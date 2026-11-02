"""Read-only multi-asset Spot and USDT perpetual market data.

Spot candles come from Binance public market data. USDT perpetual swap candles
come from OKX's unauthenticated V5 public market-data endpoint. This module
contains no account, signing, leverage, or order-placement methods.
"""
from __future__ import annotations

import time
from math import isfinite
from datetime import datetime, timezone
from typing import Any

import requests

SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
FUTURES_KLINES_URL = "https://openapi.okx.com/api/v5/market/candles"
FUTURES_FUNDING_URL = "https://openapi.okx.com/api/v5/public/funding-rate"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT")
INTERVALS = {
    "5m": ("5m", 300_000), "15m": ("15m", 900_000),
    "1h": ("1H", 3_600_000), "4h": ("4H", 14_400_000),
    "1d": ("1D", 86_400_000),
}


def _validate_request(market: str, symbol: str, interval: str, limit: int) -> None:
    if market not in {"spot", "futures"}:
        raise ValueError("market must be 'spot' or 'futures'")
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of: {', '.join(INTERVALS)}")
    if not 2 <= limit <= 1000:
        raise ValueError("limit must be between 2 and 1000")


def _request_json(
    client: requests.Session, url: str, params: dict[str, Any], retries: int
) -> Any:
    response = None
    for attempt in range(retries):
        response = client.get(url, params=params, timeout=20)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt + 1 == retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After", "1")
            try:
                delay = min(max(float(retry_after), 1.0), 10.0)
            except ValueError:
                delay = 1.0
            time.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Market-data request exhausted retries")


def fetch_completed_candles(
    market: str,
    symbol: str,
    interval: str = "1h",
    limit: int = 200,
    *,
    session: requests.Session | None = None,
    now_ms: int | None = None,
    retries: int = 3,
) -> list[dict[str, Any]]:
    """Fetch validated, fully closed candles for one market and symbol."""
    _validate_request(market, symbol, interval, limit)
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.2"})
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)

    if market == "spot":
        payload = _request_json(
            client, SPOT_KLINES_URL,
            {"symbol": symbol, "interval": interval, "limit": limit}, retries,
        )
        if not isinstance(payload, list):
            raise ValueError(f"spot {symbol}: expected a candle list")
        raw_rows = [
            (row, int(row[6]) if isinstance(row, list) and len(row) > 6 else None, True)
            for row in payload
        ]
        provider = "binance_spot"
    else:
        okx_bar, duration_ms = INTERVALS[interval]
        instrument = f"{symbol[:-4]}-USDT-SWAP"
        payload = _request_json(
            client, FUTURES_KLINES_URL,
            {"instId": instrument, "bar": okx_bar, "limit": limit}, retries,
        )
        if not isinstance(payload, dict) or payload.get("code") != "0":
            code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
            message = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
            raise ValueError(f"OKX USDT swap {instrument}: API error {code}: {message}")
        rows = payload.get("data", [])
        if not isinstance(rows, list):
            raise ValueError(f"OKX USDT swap {instrument}: expected data list")
        # OKX returns newest-first; normalize to ascending before validation.
        raw_rows = [
            (row, int(row[0]) + duration_ms - 1 if isinstance(row, list) and row else None,
             len(row) <= 8 or str(row[8]) == "1")
            for row in reversed(rows)
        ]
        provider = "okx_usdt_swap"

    candles: list[dict[str, Any]] = []
    previous_open_ms = -1
    for item, close_ms, provider_confirmed in raw_rows:
        if not isinstance(item, list) or len(item) < 6 or close_ms is None:
            raise ValueError(f"{market} {symbol}: malformed candle row")
        try:
            open_ms = int(item[0])
            values = [float(item[index]) for index in range(1, 6)]
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"{market} {symbol}: non-numeric candle field") from exc

        if open_ms <= previous_open_ms:
            raise ValueError(f"{market} {symbol}: candles are not strictly chronological")
        previous_open_ms = open_ms
        open_price, high, low, close, volume = values
        if min(open_price, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"{market} {symbol}: invalid price or volume")
        if high < max(open_price, close) or low > min(open_price, close):
            raise ValueError(f"{market} {symbol}: inconsistent OHLC values")
        if not provider_confirmed or close_ms >= current_ms:
            continue

        if market == "spot":
            base_volume = volume
            quote_volume = float(item[7]) if len(item) > 7 else None
            volume_unit = "base_asset"
        else:
            base_volume = float(item[6]) if len(item) > 6 else None
            quote_volume = float(item[7]) if len(item) > 7 else None
            volume_unit = "contracts"

        candles.append({
            "timestamp": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).isoformat(),
            "open": open_price, "high": high, "low": low,
            "close": close, "volume": volume, "volume_unit": volume_unit,
            "base_volume": base_volume, "quote_volume": quote_volume,
            "quote_volume_unit": "USDT",
        })

    if not candles:
        raise ValueError(f"{market} {symbol}: no completed candles returned")
    return candles



def fetch_current_funding_rate(
    symbol: str,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Fetch current public OKX perpetual funding context; no account access."""
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    instrument = f"{symbol[:-4]}-USDT-SWAP"
    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.2"})
    payload = _request_json(client, FUTURES_FUNDING_URL, {"instId": instrument}, retries)
    if not isinstance(payload, dict) or payload.get("code") != "0":
        code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
        message = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise ValueError(f"OKX funding {instrument}: API error {code}: {message}")

    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError(f"OKX funding {instrument}: missing funding data")
    row = rows[0]
    try:
        rate = float(row["fundingRate"])
        if not isfinite(rate):
            raise ValueError("fundingRate must be finite")
        funding_time_ms = int(row["fundingTime"])
        timestamp_ms = int(row["ts"]) if row.get("ts") else None
        next_rate = float(row["nextFundingRate"]) if row.get("nextFundingRate") not in (None, "") else None
        if next_rate is not None and not isfinite(next_rate):
            raise ValueError("nextFundingRate must be finite")
        next_time_ms = int(row["nextFundingTime"]) if row.get("nextFundingTime") else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"OKX funding {instrument}: invalid funding fields: {exc}") from exc

    return {
        "provider": "okx_public_funding",
        "instrument": instrument,
        "funding_rate": rate,
        "funding_rate_pct": rate * 100.0,
        "next_funding_rate": next_rate,
        "next_funding_rate_pct": next_rate * 100.0 if next_rate is not None else None,
        "funding_time": datetime.fromtimestamp(funding_time_ms / 1000, tz=timezone.utc).isoformat(),
        "next_funding_time": datetime.fromtimestamp(next_time_ms / 1000, tz=timezone.utc).isoformat() if next_time_ms else None,
        "observed_at": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat() if timestamp_ms else None,
        "formula_type": row.get("formulaType"),
        "settlement_state": row.get("settState"),
        "method": row.get("method"),
        "is_historical": False,
    }


def scan_universe(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    interval: str = "1h",
    limit: int = 200,
    *,
    session: requests.Session | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Collect a bounded universe sequentially and report partial failures."""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for market in ("spot", "futures"):
        for symbol in symbols:
            try:
                candles = fetch_completed_candles(
                    market, symbol, interval, limit, session=session, now_ms=now_ms
                )
                dataset = {
                    "market": market,
                    "provider": "binance_spot" if market == "spot" else "okx_usdt_swap",
                    "symbol": symbol,
                    "interval": interval,
                    "candle_count": len(candles),
                    "latest_candle": candles[-1],
                    "candles": candles,
                }
                if market == "futures":
                    try:
                        dataset["funding"] = fetch_current_funding_rate(
                            symbol, session=session
                        )
                        dataset["funding_error"] = None
                    except (requests.RequestException, ValueError) as exc:
                        # Keep candle data usable if the separate funding endpoint is unavailable.
                        dataset["funding"] = None
                        dataset["funding_error"] = str(exc)
                results.append(dataset)
            except (requests.RequestException, ValueError) as exc:
                failures.append({"market": market, "symbol": symbol, "error": str(exc)})

    return {
        "schema_version": 1,
        "mode": "public_market_data_only",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "interval": interval,
        "requested_symbols": list(symbols),
        "markets": ["spot", "futures"],
        "success_count": len(results),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
        "live_trading_enabled": False,
    }
