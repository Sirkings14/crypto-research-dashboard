"""Explainable, non-executing technical analysis for public market snapshots.

Scores are deterministic research heuristics, not probabilities or trade instructions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

import numpy as np

MIN_HISTORY = 60
MAX_SNAPSHOT_AGE_HOURS = 8


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    alpha = 2.0 / (span + 1.0)
    out = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < span:
        return out
    out[span - 1] = float(np.mean(values[:span]))
    for index in range(span, values.size):
        out[index] = alpha * values[index] + (1.0 - alpha) * out[index - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> float:
    delta = np.diff(close)
    if delta.size < period:
        return float("nan")
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, delta.size):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def _validate_candles(candles: list[dict[str, Any]]) -> tuple[np.ndarray, ...]:
    if not isinstance(candles, list) or len(candles) < MIN_HISTORY:
        raise ValueError(f"at least {MIN_HISTORY} completed candles are required")
    timestamps: list[datetime] = []
    columns = {key: [] for key in ("open", "high", "low", "close")}
    for row in candles:
        try:
            stamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("timestamp must include a timezone")
            timestamps.append(stamp.astimezone(timezone.utc))
            for key in columns:
                value = float(row[key])
                if not isfinite(value) or value <= 0:
                    raise ValueError(f"{key} must be finite and positive")
                columns[key].append(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid candle: {exc}") from exc
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError("candles must be strictly chronological")
    arrays = tuple(np.asarray(columns[key], dtype=np.float64) for key in ("open", "high", "low", "close"))
    open_, high, low, close = arrays
    if np.any(high < np.maximum(open_, close)) or np.any(low > np.minimum(open_, close)):
        raise ValueError("inconsistent OHLC values")
    return (*arrays, timestamps)


def analyze_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Analyze one spot or swap dataset without generating executable orders."""
    candles = dataset.get("candles")
    open_, high, low, close, timestamps = _validate_candles(candles)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    rsi14 = _rsi(close)
    returns_24h = float(close[-1] / close[-25] - 1.0) if close.size >= 25 else float("nan")
    true_ranges = np.maximum(high[1:] - low[1:],
                             np.maximum(np.abs(high[1:] - close[:-1]), np.abs(low[1:] - close[:-1])))
    atr14 = float(np.mean(true_ranges[-14:])) if true_ranges.size >= 14 else float("nan")
    atr_pct = atr14 / close[-1] * 100.0 if isfinite(atr14) else float("nan")

    score = 0
    reasons: list[str] = []
    if close[-1] > ema20[-1] > ema50[-1]:
        score += 2
        reasons.append("price above rising EMA20/EMA50 trend stack")
    elif close[-1] < ema20[-1] < ema50[-1]:
        score -= 2
        reasons.append("price below falling EMA20/EMA50 trend stack")
    elif close[-1] > ema50[-1]:
        score += 1
        reasons.append("price above EMA50")
    elif close[-1] < ema50[-1]:
        score -= 1
        reasons.append("price below EMA50")

    if isfinite(returns_24h):
        if returns_24h > 0.01:
            score += 1
            reasons.append("positive 24-hour momentum")
        elif returns_24h < -0.01:
            score -= 1
            reasons.append("negative 24-hour momentum")

    if isfinite(rsi14):
        if 50.0 <= rsi14 <= 70.0:
            score += 1
            reasons.append("RSI supports positive momentum without extreme overbought reading")
        elif rsi14 >= 75.0:
            score -= 1
            reasons.append("RSI is stretched to the upside")
        elif rsi14 <= 30.0:
            reasons.append("RSI is oversold; no automatic reversal assumed")

    if score >= 2:
        bias = "bullish"
    elif score <= -2:
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "market": dataset.get("market"),
        "provider": dataset.get("provider"),
        "symbol": dataset.get("symbol"),
        "interval": dataset.get("interval"),
        "candle_count": int(close.size),
        "latest_candle": timestamps[-1].isoformat(),
        "latest_close": float(close[-1]),
        "ema20": float(ema20[-1]),
        "ema50": float(ema50[-1]),
        "rsi14": float(rsi14) if isfinite(rsi14) else None,
        "return_24h_pct": float(returns_24h * 100.0) if isfinite(returns_24h) else None,
        "atr14_pct": float(atr_pct) if isfinite(atr_pct) else None,
        "research_score": int(score),
        "research_bias": bias,
        "reasons": reasons,
        "funding_context": dataset.get("funding"),
        "funding_error": dataset.get("funding_error"),
        "funding_included_in_research_score": False,
        "signal_is_probability": False,
        "order_instruction": None,
    }


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Analyze each available market independently and compare paired Spot/Swap bias."""
    analyses: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for dataset in snapshot.get("results", []):
        try:
            analyses.append(analyze_dataset(dataset))
        except (TypeError, ValueError) as exc:
            failures.append({
                "market": str(dataset.get("market", "unknown")),
                "symbol": str(dataset.get("symbol", "unknown")),
                "error": str(exc),
            })

    by_pair: dict[str, dict[str, str]] = {}
    for item in analyses:
        by_pair.setdefault(item["symbol"], {})[item["market"]] = item["research_bias"]
    comparisons = []
    for symbol, markets in sorted(by_pair.items()):
        spot = markets.get("spot")
        futures = markets.get("futures")
        if spot and futures:
            alignment = "aligned" if spot == futures else "divergent"
            comparisons.append({"symbol": symbol, "spot_bias": spot,
                                "futures_bias": futures, "alignment": alignment})

    return {
        "schema_version": 1,
        "mode": "research_analysis_only",
        "source_fetched_at": snapshot.get("fetched_at"),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "dataset_count": len(analyses),
        "failure_count": len(failures),
        "analyses": analyses,
        "spot_futures_comparison": comparisons,
        "failures": failures,
        "live_trading_enabled": False,
        "disclaimer": "Heuristic research scores are not calibrated probabilities, financial advice, or executable trade instructions.",
    }
