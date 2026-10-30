"""Public Binance Spot candle download. No account credentials or trading calls."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd
import requests

BASE_URL = "https://data-api.binance.vision/api/v3/klines"
INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def fetch_ohlcv(symbol: str = "BTCUSDT", interval: str = "5m", days: int = 90) -> pd.DataFrame:
    symbol = symbol.strip().upper()
    if not symbol.isalnum() or interval not in INTERVAL_MS or not 1 <= days <= 365:
        raise ValueError("Use a valid spot symbol, supported interval and a lookback from 1 to 365 days.")
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    step = INTERVAL_MS[interval]
    cursor = now_ms - days * 86_400_000
    rows = []
    session = requests.Session()
    session.headers.update({"User-Agent": "CryptoResearchLab/1.0"})
    while cursor < now_ms:
        response = None
        for attempt in range(5):
            response = session.get(BASE_URL, params={"symbol": symbol, "interval": interval,
                                     "startTime": cursor, "endTime": now_ms, "limit": 1000}, timeout=20)
            if response.status_code == 429 or response.status_code >= 500:
                time.sleep(min(float(response.headers.get("Retry-After", 1 + attempt)), 30))
                continue
            response.raise_for_status()
            break
        else:
            raise RuntimeError("Public candle service is rate limiting or unavailable; retry shortly or upload a CSV.")
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + step
        if next_cursor <= cursor:
            raise RuntimeError("Candle service returned no forward progress.")
        cursor = next_cursor
        if len(batch) < 1000:
            break
    if not rows:
        raise RuntimeError(f"No candles returned for {symbol}. Check the market symbol and retry.")
    frame = pd.DataFrame(rows, columns=["open_ms", "open", "high", "low", "close", "volume",
        "close_ms", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"])
    # Only completed candles are eligible for backtests and paper marks.
    frame = frame[pd.to_numeric(frame.close_ms) < now_ms]
    frame["timestamp"] = pd.to_datetime(pd.to_numeric(frame.open_ms), unit="ms", utc=True)
    frame = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame.reset_index(drop=True)
