"""Read-only Binance USD-M Futures data scan and paper-readiness diagnostics.

This worker never accesses private account endpoints or places orders. Binance
USD-M is kept in a separate dataset from the existing OKX swap research feed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from binance_usdm_data import (
    fetch_usdm_funding_history,
    fetch_usdm_mark_price_candles,
    fetch_usdm_trade_candles,
)
from binance_usdm_instrument import (
    fetch_usdm_exchange_info,
    parse_usdm_instrument_metadata,
)
from market_analysis import analyze_dataset
from market_universe import DEFAULT_SYMBOLS

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOT_OUTPUT = DATA_DIR / "binance_usdm_latest.json"
ANALYSIS_OUTPUT = DATA_DIR / "binance_usdm_analysis_latest.json"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def assess_paper_readiness(
    symbol: str,
    trade_candles: list[dict[str, Any]],
    mark_candles: list[dict[str, Any]],
    funding_history: list[dict[str, Any]],
    instrument_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed unless all required Binance datasets align for replay."""
    reasons: list[str] = []
    if len(trade_candles) < 60:
        reasons.append("insufficient_completed_trade_history")
    if not funding_history:
        reasons.append("realized_funding_history_unavailable")
    trade_stamps = [row.get("timestamp") for row in trade_candles]
    mark_stamps = [row.get("timestamp") for row in mark_candles]
    if not trade_stamps or trade_stamps != mark_stamps:
        reasons.append("trade_mark_timestamps_not_identical")

    if not isinstance(instrument_metadata, dict):
        reasons.append("binance_instrument_metadata_unavailable")
    else:
        if instrument_metadata.get("symbol") != symbol or instrument_metadata.get("instrument") != symbol:
            reasons.append("binance_instrument_identity_mismatch")
        if instrument_metadata.get("provider") != "binance_usdm_exchange_info":
            reasons.append("unexpected_instrument_metadata_provider")

    if trade_stamps:
        parsed_stamps = [
            datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            for value in trade_stamps
        ]
        if any(
            (right - left).total_seconds() != 3600
            for left, right in zip(parsed_stamps, parsed_stamps[1:])
        ):
            reasons.append("trade_candle_timeline_has_gaps")
        start, end = parsed_stamps[0], parsed_stamps[-1]
        candle_stamps = set(parsed_stamps)
        for record in funding_history:
            try:
                if record.get("realized_rate") is None or record.get("is_realized") is not True:
                    reasons.append("funding_history_contains_non_realized_record")
                    break
                stamp = datetime.fromisoformat(record["funding_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                reasons.append("invalid_funding_timestamp")
                break
            if start <= stamp <= end and stamp not in candle_stamps:
                reasons.append("funding_settlement_not_aligned_to_candle_open")
                break

    return {
        "ready": not reasons,
        "reasons": reasons,
        "trade_candle_count": len(trade_candles),
        "mark_price_candle_count": len(mark_candles),
        "funding_history_count": len(funding_history),
        "live_trading_enabled": False,
    }


def collect_snapshot(
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS,
    *,
    session=None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Collect 100 aligned completed trade/mark candles plus funding and rules."""
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    client = session or requests.Session()

    try:
        exchange_info = fetch_usdm_exchange_info(session=client)
        exchange_info_error = None
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        exchange_info = None
        exchange_info_error = str(exc)

    for symbol in symbols:
        try:
            trades = fetch_usdm_trade_candles(
                symbol, interval="1h", limit=100, session=client, now_ms=now_ms
            )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            failures.append({"market": "futures_binance", "symbol": symbol, "component": "trade_candles", "error": str(exc)})
            continue

        try:
            marks = fetch_usdm_mark_price_candles(
                symbol, interval="1h", limit=100, session=client, now_ms=now_ms
            )
            mark_error = None
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            marks = []
            mark_error = str(exc)

        try:
            funding = fetch_usdm_funding_history(symbol, limit=100, session=client)
            funding_error = None
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            funding = []
            funding_error = str(exc)

        try:
            if exchange_info is None:
                raise ValueError(exchange_info_error or "exchangeInfo unavailable")
            metadata = parse_usdm_instrument_metadata(exchange_info, symbol)
            metadata_error = None
        except (ValueError, TypeError) as exc:
            metadata = None
            metadata_error = str(exc)

        readiness = assess_paper_readiness(symbol, trades, marks, funding, metadata)
        dataset = {
            "market": "futures_binance",
            "provider": "binance_usdm_swap",
            "venue": "binance",
            "product": "usd_m_perpetual",
            "symbol": symbol,
            "instrument": symbol,
            "interval": "1h",
            "candle_count": len(trades),
            "latest_candle": trades[-1],
            "candles": trades,
            "mark_price_candles": marks,
            "mark_price_error": mark_error,
            "funding_history": funding,
            "funding_history_error": funding_error,
            "funding_history_is_realized": True,
            "instrument_metadata": metadata,
            "instrument_metadata_error": metadata_error,
            "paper_replay_readiness": readiness,
            "live_trading_enabled": False,
        }
        results.append(dataset)

    return {
        "schema_version": 1,
        "mode": "binance_usdm_public_data_only",
        "venue": "binance",
        "product": "usd_m_perpetual",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "interval": "1h",
        "requested_symbols": list(symbols),
        "markets": ["futures_binance"],
        "success_count": len(results),
        "ready_count": sum(1 for row in results if row["paper_replay_readiness"]["ready"]),
        "failure_count": len(failures),
        "exchange_info_error": exchange_info_error,
        "results": results,
        "failures": failures,
        "live_trading_enabled": False,
        "disclaimer": "Public market-data diagnostics only; no account access, order placement, or profitability claim.",
    }


def main() -> None:
    snapshot = collect_snapshot()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_json(SNAPSHOT_OUTPUT, snapshot)

    analyses = []
    analysis_failures = []
    for dataset in snapshot["results"]:
        try:
            analyses.append(analyze_dataset(dataset))
        except (TypeError, ValueError) as exc:
            analysis_failures.append({"symbol": dataset["symbol"], "error": str(exc)})

    analysis = {
        "schema_version": 1,
        "mode": "binance_usdm_research_analysis_only",
        "venue": "binance",
        "product": "usd_m_perpetual",
        "source_fetched_at": snapshot["fetched_at"],
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "dataset_count": len(analyses),
        "failure_count": len(analysis_failures),
        "analyses": analyses,
        "failures": analysis_failures,
        "live_trading_enabled": False,
    }
    _atomic_json(ANALYSIS_OUTPUT, analysis)

    print(
        f"Binance USD-M scan: {snapshot['success_count']} datasets collected; "
        f"{snapshot['ready_count']} paper-ready; {snapshot['failure_count']} source failures."
    )
    if snapshot["success_count"] == 0:
        raise RuntimeError("No Binance USD-M trade datasets were collected; inspect the snapshot artifact.")
    if snapshot["ready_count"] == 0:
        raise RuntimeError("No Binance USD-M dataset passed paper-readiness checks; inspect diagnostics.")
    if analysis["dataset_count"] == 0:
        raise RuntimeError("No Binance USD-M datasets could be analyzed.")


if __name__ == "__main__":
    main()
