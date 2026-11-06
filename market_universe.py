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
FUTURES_FUNDING_HISTORY_URL = "https://openapi.okx.com/api/v5/public/funding-rate-history"
FUTURES_INSTRUMENTS_URL = "https://openapi.okx.com/api/v5/public/instruments"
MARK_PRICE_CANDLES_URL = "https://openapi.okx.com/api/v5/market/mark-price-candles"
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



def fetch_funding_rate_history(
    symbol: str,
    limit: int = 100,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
) -> list[dict[str, Any]]:
    """Fetch recent settled funding observations; predicted rates stay separately labelled."""
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if not 1 <= limit <= 400:
        raise ValueError("funding history limit must be between 1 and 400")
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    instrument = f"{symbol[:-4]}-USDT-SWAP"
    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.2"})
    payload = _request_json(
        client, FUTURES_FUNDING_HISTORY_URL,
        {"instId": instrument, "limit": limit}, retries,
    )
    if not isinstance(payload, dict) or payload.get("code") != "0":
        code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
        message = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise ValueError(f"OKX funding history {instrument}: API error {code}: {message}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"OKX funding history {instrument}: expected data list")

    records = []
    seen_times = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"OKX funding history {instrument}: malformed record")
        try:
            time_ms = int(row["fundingTime"])
            predicted = float(row["fundingRate"]) if row.get("fundingRate") not in (None, "") else None
            realized = float(row["realizedRate"]) if row.get("realizedRate") not in (None, "") else None
            if predicted is not None and not isfinite(predicted):
                raise ValueError("fundingRate must be finite")
            if realized is not None and not isfinite(realized):
                raise ValueError("realizedRate must be finite")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"OKX funding history {instrument}: invalid record: {exc}") from exc
        if time_ms in seen_times:
            continue
        seen_times.add(time_ms)
        records.append({
            "funding_time": datetime.fromtimestamp(time_ms / 1000, tz=timezone.utc).isoformat(),
            "funding_time_ms": time_ms,
            "predicted_rate": predicted,
            "realized_rate": realized,
            "formula_type": row.get("formulaType"),
            "method": row.get("method"),
            "is_realized": realized is not None,
        })
    records.sort(key=lambda item: item["funding_time_ms"])
    return records



def fetch_swap_instrument_metadata(
    symbol: str,
    *,
    session: requests.Session | None = None,
    retries: int = 3,
) -> dict[str, Any]:
    """Fetch public contract sizing constraints for one linear USDT swap."""
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    instrument = f"{symbol[:-4]}-USDT-SWAP"
    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.2"})
    payload = _request_json(
        client, FUTURES_INSTRUMENTS_URL,
        {"instType": "SWAP", "instId": instrument}, retries,
    )
    if not isinstance(payload, dict) or payload.get("code") != "0":
        code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
        message = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise ValueError(f"OKX instrument {instrument}: API error {code}: {message}")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError(f"OKX instrument {instrument}: missing instrument metadata")
    row = rows[0]
    if row.get("instId") != instrument:
        raise ValueError(f"OKX instrument metadata mismatch for {instrument}")
    if row.get("ctType") != "linear" or row.get("settleCcy") != "USDT":
        raise ValueError(f"OKX instrument {instrument}: expected a linear USDT-settled swap")

    numeric = {}
    for key in ("ctVal", "lotSz", "minSz", "tickSz"):
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"OKX instrument {instrument}: invalid {key}") from exc
        if not isfinite(value) or value <= 0:
            raise ValueError(f"OKX instrument {instrument}: {key} must be finite and positive")
        numeric[key] = value

    return {
        "provider": "okx_public_instruments",
        "instrument": instrument,
        "instrument_type": row.get("instType"),
        "contract_type": row.get("ctType"),
        "contract_value": numeric["ctVal"],
        "contract_value_currency": row.get("ctValCcy"),
        "contract_multiplier": row.get("ctMult"),
        "settlement_currency": row.get("settleCcy"),
        "lot_size_contracts": numeric["lotSz"],
        "minimum_size_contracts": numeric["minSz"],
        "tick_size": numeric["tickSz"],
        "exchange_max_leverage_ceiling": row.get("lever"),
        "instrument_state": row.get("state"),
        "upcoming_changes": row.get("upcChg", []),
        "metadata_is_account_specific": False,
        "live_trading_enabled": False,
    }



def fetch_completed_mark_price_candles(
    symbol: str,
    interval: str = "1h",
    limit: int = 100,
    *,
    session: requests.Session | None = None,
    now_ms: int | None = None,
    retries: int = 3,
) -> list[dict[str, Any]]:
    """Fetch completed OKX mark-price OHLC candles for a linear USDT swap."""
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of: {', '.join(INTERVALS)}")
    if not 2 <= limit <= 100:
        raise ValueError("mark-price candle limit must be between 2 and 100")
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    instrument = f"{symbol[:-4]}-USDT-SWAP"
    bar, duration_ms = INTERVALS[interval]
    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.2"})
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    payload = _request_json(
        client, MARK_PRICE_CANDLES_URL,
        {"instId": instrument, "bar": bar, "limit": limit}, retries,
    )
    if not isinstance(payload, dict) or payload.get("code") != "0":
        code = payload.get("code") if isinstance(payload, dict) else "invalid_response"
        message = payload.get("msg", "unexpected response") if isinstance(payload, dict) else "unexpected response"
        raise ValueError(f"OKX mark price {instrument}: API error {code}: {message}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"OKX mark price {instrument}: expected data list")

    candles = []
    previous_open_ms = -1
    for row in reversed(rows):
        if not isinstance(row, list) or len(row) < 6:
            raise ValueError(f"OKX mark price {instrument}: malformed candle row")
        try:
            open_ms = int(row[0])
            open_price, high, low, close = (float(row[i]) for i in range(1, 5))
            confirmed = str(row[5]) == "1"
        except (TypeError, ValueError, IndexError) as exc:
            raise ValueError(f"OKX mark price {instrument}: invalid candle field") from exc
        close_ms = open_ms + duration_ms - 1
        if open_ms <= previous_open_ms:
            raise ValueError(f"OKX mark price {instrument}: candles are not strictly chronological")
        previous_open_ms = open_ms
        if min(open_price, high, low, close) <= 0 or not all(
            isfinite(value) for value in (open_price, high, low, close)
        ):
            raise ValueError(f"OKX mark price {instrument}: invalid OHLC price")
        if high < max(open_price, close) or low > min(open_price, close):
            raise ValueError(f"OKX mark price {instrument}: inconsistent OHLC values")
        if not confirmed or close_ms >= current_ms:
            continue
        candles.append({
            "timestamp": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).isoformat(),
            "open": open_price, "high": high, "low": low, "close": close,
        })
    if not candles:
        raise ValueError(f"OKX mark price {instrument}: no completed candles returned")
    return candles


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
                    try:
                        dataset["funding_history"] = fetch_funding_rate_history(
                            symbol, limit=100, session=session
                        )
                        dataset["funding_history_error"] = None
                    except (requests.RequestException, ValueError) as exc:
                        dataset["funding_history"] = []
                        dataset["funding_history_error"] = str(exc)
                    try:
                        dataset["instrument_metadata"] = fetch_swap_instrument_metadata(
                            symbol, session=session
                        )
                        dataset["instrument_metadata_error"] = None
                    except (requests.RequestException, ValueError) as exc:
                        dataset["instrument_metadata"] = None
                        dataset["instrument_metadata_error"] = str(exc)
                    try:
                        dataset["mark_price_candles"] = fetch_completed_mark_price_candles(
                            symbol, interval, limit=min(limit, 100), session=session, now_ms=now_ms
                        )
                        dataset["mark_price_candle_count"] = len(dataset["mark_price_candles"])
                        dataset["mark_price_error"] = None
                    except (requests.RequestException, ValueError) as exc:
                        dataset["mark_price_candles"] = []
                        dataset["mark_price_candle_count"] = 0
                        dataset["mark_price_error"] = str(exc)
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
