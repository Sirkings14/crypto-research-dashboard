"""Read-only Binance USD-M Futures data scan and paper-readiness diagnostics.

This worker never accesses private account endpoints or places orders. Binance
USD-M is kept in a separate dataset from the existing OKX swap research feed.
"""
from __future__ import annotations

from bisect import bisect_right
import csv
import hashlib
import io
import json
import zipfile
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
from futures_contract_math import validate_linear_usdt_swap_metadata
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


def previous_month_labels(*, count: int = 2, now: datetime | None = None) -> list[str]:
    """Return the most recent complete UTC calendar months, newest first."""
    if not isinstance(count, int) or count < 1 or count > 12:
        raise ValueError("count must be an integer between 1 and 12")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    month_index = current.year * 12 + current.month - 1
    labels = []
    for offset in range(1, count + 1):
        index = month_index - offset
        year, month_number = divmod(index, 12)
        labels.append(f"{year:04d}-{month_number + 1:02d}")
    return labels


def classify_source_status(
    exchange_info_error: str | None,
    failures: list[dict[str, str]],
    success_count: int,
    requested_count: int,
) -> str:
    """Distinguish a runner-level HTTP 451 block from genuine collector defects."""
    if success_count:
        return "available" if not failures else "partial"
    all_market_requests_blocked = (
        requested_count > 0
        and len(failures) == requested_count
        and all("451" in failure.get("error", "") for failure in failures)
    )
    if exchange_info_error and "451" in exchange_info_error and all_market_requests_blocked:
        return "blocked_by_http_451"
    return "unavailable"


def probe_binance_vision_archives(
    *,
    session=None,
    month: str | None = None,
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    """Probe official Binance historical archive files without downloading them.

    This is a historical-data reachability check only. Archive availability
    does not provide current candles or establish paper-replay readiness.
    """
    if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isupper():
        raise ValueError("symbol must be an uppercase alphanumeric Binance symbol")
    if month is None:
        now = datetime.now(timezone.utc)
        year, month_number = now.year, now.month - 1
        if month_number == 0:
            year, month_number = year - 1, 12
        month = f"{year:04d}-{month_number:02d}"
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise ValueError("month must use YYYY-MM format")
    try:
        year, month_number = (int(part) for part in month.split("-"))
        datetime(year, month_number, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use a valid YYYY-MM value") from exc

    client = session or requests.Session()
    base = "https://data.binance.vision/data/futures/um/monthly"
    paths = {
        "trade_klines": f"{base}/klines/{symbol}/1h/{symbol}-1h-{month}.zip",
        "mark_price_klines": f"{base}/markPriceKlines/{symbol}/1h/{symbol}-1h-{month}.zip",
        "funding_rate": f"{base}/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip",
    }
    files: dict[str, dict[str, Any]] = {}
    for name, url in paths.items():
        try:
            response = client.head(url, timeout=15, allow_redirects=True)
            files[name] = {
                "url": url,
                "status_code": int(response.status_code),
                "available": int(response.status_code) == 200,
            }
        except requests.RequestException as exc:
            files[name] = {"url": url, "available": False, "error": str(exc)}
    return {
        "provider": "binance_vision_official_archive",
        "month": month,
        "symbol": symbol,
        "files": files,
        "all_available": all(item.get("available") is True for item in files.values()),
        "historical_only": True,
        "live_trading_enabled": False,
    }


def inspect_binance_vision_archives(
    *,
    session=None,
    month: str,
    symbol: str = "BTCUSDT",
) -> dict[str, Any]:
    """Download and checksum one-symbol archive samples to verify real file schemas."""
    if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isupper():
        raise ValueError("symbol must be an uppercase alphanumeric Binance symbol")
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-":
        raise ValueError("month must use YYYY-MM format")
    try:
        year, month_number = (int(part) for part in month.split("-"))
        datetime(year, month_number, 1, tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use a valid YYYY-MM value") from exc

    client = session or requests.Session()
    base = "https://data.binance.vision/data/futures/um/monthly"
    archives = {
        "trade_klines": f"{base}/klines/{symbol}/1h/{symbol}-1h-{month}.zip",
        "mark_price_klines": f"{base}/markPriceKlines/{symbol}/1h/{symbol}-1h-{month}.zip",
        "funding_rate": f"{base}/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip",
    }
    inspected: dict[str, dict[str, Any]] = {}
    for name, url in archives.items():
        try:
            archive_response = client.get(url, timeout=30)
            archive_response.raise_for_status()
            checksum_response = client.get(url + ".CHECKSUM", timeout=15)
            checksum_response.raise_for_status()
            checksum_parts = checksum_response.text.strip().split()
            if len(checksum_parts) < 1 or len(checksum_parts[0]) != 64:
                raise ValueError("archive checksum file has an invalid SHA-256 value")
            actual_sha256 = hashlib.sha256(archive_response.content).hexdigest()
            checksum_valid = actual_sha256.lower() == checksum_parts[0].lower()
            if not checksum_valid:
                raise ValueError("archive SHA-256 checksum mismatch")

            with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
                csv_names = [item for item in archive.namelist() if item.lower().endswith(".csv")]
                if len(csv_names) != 1:
                    raise ValueError("archive must contain exactly one CSV file")
                with archive.open(csv_names[0]) as raw:
                    reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", newline=""))
                    sample = []
                    row_count = 0
                    timestamps = []
                    funding_intervals = []
                    for row in reader:
                        row_count += 1
                        if len(sample) < 3:
                            sample.append(row)
                        if row and row[0].isdigit():
                            timestamps.append(int(row[0]))
                            if name == "funding_rate" and len(row) > 1:
                                funding_intervals.append(row[1])
            if row_count == 0:
                raise ValueError("archive CSV is empty")
            duplicate_count = len(timestamps) - len(set(timestamps))
            ordered = all(right > left for left, right in zip(timestamps, timestamps[1:]))
            coverage = {
                "timestamp_count": len(timestamps),
                "first_timestamp_ms": timestamps[0] if timestamps else None,
                "last_timestamp_ms": timestamps[-1] if timestamps else None,
                "duplicate_timestamp_count": duplicate_count,
                "strictly_increasing": ordered,
            }
            if name in {"trade_klines", "mark_price_klines"} and len(timestamps) > 1:
                diffs = [right - left for left, right in zip(timestamps, timestamps[1:])]
                coverage["hourly_gap_count"] = sum(1 for diff in diffs if diff != 3_600_000)
                coverage["missing_hour_count"] = sum(
                    max(0, diff // 3_600_000 - 1) for diff in diffs
                )
                coverage["gap_intervals"] = [
                    {
                        "previous_open_time_ms": left,
                        "next_open_time_ms": right,
                        "missing_hours": max(0, (right - left) // 3_600_000 - 1),
                    }
                    for left, right in zip(timestamps, timestamps[1:])
                    if right - left != 3_600_000
                ]
            if name == "funding_rate":
                coverage["timestamp_offsets_within_hour_ms"] = sorted(
                    {stamp % 3_600_000 for stamp in timestamps}
                )
                coverage["funding_interval_hours"] = sorted(set(funding_intervals))
            inspected[name] = {
                "url": url,
                "archive_file": csv_names[0],
                "sha256": actual_sha256,
                "checksum_valid": True,
                "row_count": row_count,
                "sample_rows": sample,
                "coverage": coverage,
            }
        except (requests.RequestException, ValueError, zipfile.BadZipFile, OSError) as exc:
            inspected[name] = {"url": url, "checksum_valid": False, "error": str(exc)}

    return {
        "provider": "binance_vision_official_archive",
        "symbol": symbol,
        "month": month,
        "files": inspected,
        "all_checksums_valid": all(item.get("checksum_valid") is True for item in inspected.values()),
        "schema_inspection_only": True,
        "historical_only": True,
        "live_trading_enabled": False,
    }


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
        try:
            validate_linear_usdt_swap_metadata(instrument_metadata)
        except ValueError:
            reasons.append("invalid_linear_usdt_instrument_metadata")
        if instrument_metadata.get("contract_value_currency") != symbol[:-4]:
            reasons.append("unexpected_contract_quantity_currency")
        for field in ("lot_size_contracts", "minimum_size_contracts", "maximum_size_contracts", "minimum_notional_usdt"):
            try:
                value = float(instrument_metadata[field])
                if value <= 0:
                    raise ValueError(field)
            except (KeyError, TypeError, ValueError):
                reasons.append(f"invalid_{field}")

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
        funding_window_end = end.timestamp() + 0.010
        for record in funding_history:
            try:
                if record.get("realized_rate") is None or record.get("is_realized") is not True:
                    reasons.append("funding_history_contains_non_realized_record")
                    break
                stamp = datetime.fromisoformat(record["funding_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError):
                reasons.append("invalid_funding_timestamp")
                break
            if start <= stamp and stamp.timestamp() <= funding_window_end:
                candle_index = bisect_right(parsed_stamps, stamp) - 1
                if candle_index < 0:
                    reasons.append("funding_settlement_not_aligned_within_10ms_of_candle_open")
                    break
                offset_ms = (stamp - parsed_stamps[candle_index]).total_seconds() * 1000.0
                if offset_ms < 0 or offset_ms > 10:
                    reasons.append("funding_settlement_not_aligned_within_10ms_of_candle_open")
                    break

    return {
        "ready": not reasons,
        "reasons": reasons,
        "trade_candle_count": len(trade_candles),
        "mark_price_candle_count": len(mark_candles),
        "funding_history_count": len(funding_history),
        "funding_settlement_alignment_tolerance_ms": 10,
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
                symbol, interval="1h", limit=101, session=client, now_ms=now_ms
            )
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            failures.append({"market": "futures_binance", "symbol": symbol, "component": "trade_candles", "error": str(exc)})
            continue

        try:
            marks = fetch_usdm_mark_price_candles(
                symbol, interval="1h", limit=101, session=client, now_ms=now_ms
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

    source_status = classify_source_status(
        exchange_info_error, failures, len(results), len(symbols)
    )
    return {
        "schema_version": 1,
        "mode": "binance_usdm_public_data_only",
        "venue": "binance",
        "product": "usd_m_perpetual",
        "source_status": source_status,
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
    if snapshot["source_status"] == "blocked_by_http_451":
        archive_session = requests.Session()
        archive_audits = {}
        for month in previous_month_labels(count=2):
            archive_probe = probe_binance_vision_archives(
                session=archive_session, month=month
            )
            month_audit = {"probe": archive_probe, "symbols": {}}
            if archive_probe.get("all_available") is True:
                month_audit["symbols"] = {
                    symbol: inspect_binance_vision_archives(
                        session=archive_session, month=month, symbol=symbol
                    )
                    for symbol in DEFAULT_SYMBOLS
                }
            archive_audits[month] = month_audit
        snapshot["official_archive_audits"] = archive_audits
        _atomic_json(SNAPSHOT_OUTPUT, snapshot)
        print(
            "SKIPPED: GitHub-hosted runner is blocked by HTTP 451 on Binance USD-M REST endpoints. "
            "No Binance dataset is paper-ready; auditing the two latest complete official archive months."
        )
        for month, month_audit in archive_audits.items():
            print(f"Binance Vision archive month {month}:")
            if not month_audit["symbols"]:
                print("  archive files unavailable")
                continue
            for symbol, inspection in month_audit["symbols"].items():
                print(
                    f"  {symbol}: all_checksums_valid={inspection.get('all_checksums_valid', False)}"
                )
                for name, result in inspection["files"].items():
                    coverage = result.get("coverage", {})
                    print(
                        f"    {name}: checksum_valid={result.get('checksum_valid', False)} "
                        f"rows={result.get('row_count', 0)} "
                        f"missing_hours={coverage.get('missing_hour_count', 'n/a')} "
                        f"funding_offsets_ms={coverage.get('timestamp_offsets_within_hour_ms', 'n/a')}"
                    )
        return
    if snapshot["success_count"] == 0:
        raise RuntimeError("No Binance USD-M trade datasets were collected; inspect the snapshot artifact.")
    if snapshot["ready_count"] == 0:
        raise RuntimeError("No Binance USD-M dataset passed paper-readiness checks; inspect diagnostics.")
    if analysis["dataset_count"] == 0:
        raise RuntimeError("No Binance USD-M datasets could be analyzed.")


if __name__ == "__main__":
    main()
