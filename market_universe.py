"""Read-only multi-asset Spot and USD-M perpetual market data.

This module deliberately exposes no account, signing, or order-placement methods.
It collects completed OHLCV candles from public Binance market-data endpoints.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

SPOT_KLINES_URL = "https://data-api.binance.vision/api/v3/klines"
FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT")
SUPPORTED_INTERVALS = {"5m", "15m", "1h", "4h", "1d"}
MARKET_URLS = {"spot": SPOT_KLINES_URL, "futures": FUTURES_KLINES_URL}


def _validate_request(market: str, symbol: str, interval: str, limit: int) -> None:
    if market not in MARKET_URLS:
        raise ValueError("market must be 'spot' or 'futures'")
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"interval must be one of: {', '.join(sorted(SUPPORTED_INTERVALS))}")
    if not 2 <= limit <= 1000:
        raise ValueError("limit must be between 2 and 1000")


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
    """Fetch and validate only fully closed candles for one market and symbol."""
    _validate_request(market, symbol, interval, limit)
    if retries < 1 or retries > 5:
        raise ValueError("retries must be between 1 and 5")

    client = session or requests.Session()
    client.headers.update({"User-Agent": "CryptoResearchLab/2.0"})
    response = None
    for attempt in range(retries):
        response = client.get(
            MARKET_URLS[market],
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=20,
        )
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
        break

    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError(f"{market} {symbol}: expected a candle list")
    current_ms = now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    candles: list[dict[str, Any]] = []
    previous_open_ms = -1

    for item in payload:
        if not isinstance(item, list) or len(item) < 6:
            raise ValueError(f"{market} {symbol}: malformed candle row")
        try:
            open_ms = int(item[0])
            close_ms = int(item[6]) if len(item) > 6 else open_ms
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
        if close_ms >= current_ms:
            continue

        candles.append({
            "timestamp": datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    if not candles:
        raise ValueError(f"{market} {symbol}: no completed candles returned")
    return candles


def scan_universe(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    interval: str = "1h",
    limit: int = 200,
    *,
    session: requests.Session | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Collect a bounded universe sequentially; report partial failures explicitly."""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for market in ("spot", "futures"):
        for symbol in symbols:
            try:
                candles = fetch_completed_candles(
                    market, symbol, interval, limit, session=session, now_ms=now_ms
                )
                results.append({
                    "market": market,
                    "symbol": symbol,
                    "interval": interval,
                    "candle_count": len(candles),
                    "latest_candle": candles[-1],
                    "candles": candles,
                })
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
