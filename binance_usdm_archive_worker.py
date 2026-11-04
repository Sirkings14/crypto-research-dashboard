"""Manual all-symbol ingestion and descriptive analysis of Binance Vision archives."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

from binance_usdm_archive import load_monthly_symbol_archives
from binance_usdm_archive_analysis import analyze_archive_dataset
from market_universe import DEFAULT_SYMBOLS

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "binance_usdm_archive_latest.json"
ANALYSIS_OUTPUT = ROOT / "data" / "binance_usdm_archive_analysis_latest.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="2026-09", help="Complete UTC month (YYYY-MM)")
    args = parser.parse_args()
    client = requests.Session()
    results, failures, analyses, analysis_failures = {}, {}, {}, {}
    for symbol in DEFAULT_SYMBOLS:
        try:
            dataset = load_monthly_symbol_archives(symbol, args.month, session=client)
            results[symbol] = dataset
            print(
                f"{symbol}: trade={len(dataset['trade_candles'])}, "
                f"mark={len(dataset['mark_price_candles'])}, "
                f"funding={len(dataset['funding_history'])}, checksums=verified"
            )
            try:
                analyses[symbol] = analyze_archive_dataset(dataset)
                stats = analyses[symbol]
                print(
                    f"  descriptive: ann_vol={stats['hourly_trade_returns']['annualized_volatility_pct']:.2f}% "
                    f"buy_hold_dd={stats['hourly_trade_returns']['buy_and_hold_max_drawdown_pct']:.2f}% "
                    f"median_basis={stats['trade_vs_mark_basis_bps']['median']:.2f}bps"
                )
            except (TypeError, ValueError, KeyError) as exc:
                analysis_failures[symbol] = str(exc)
                print(f"  analysis FAILED: {exc}")
        except (requests.RequestException, ValueError, OSError) as exc:
            failures[symbol] = str(exc)
            print(f"{symbol}: FAILED: {exc}")

    fetched_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": 1,
        "mode": "binance_usdm_historical_archive_ingestion",
        "provider": "binance_vision_official_archive",
        "month": args.month,
        "fetched_at": fetched_at,
        "requested_symbols": list(DEFAULT_SYMBOLS),
        "success_count": len(results),
        "failure_count": len(failures),
        "results": results,
        "failures": failures,
        "live_trading_enabled": False,
        "disclaimer": (
            "Historical public data only. Archive funding CSV lacks event mark prices; "
            "any paper replay must disclose the mark-candle-open proxy."
        ),
    }
    analysis_payload = {
        "schema_version": 1,
        "mode": "binance_usdm_historical_market_descriptive_analysis_only",
        "provider": "binance_vision_official_archive",
        "month": args.month,
        "source_fetched_at": fetched_at,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "analysis_count": len(analyses),
        "failure_count": len(analysis_failures),
        "analyses": analyses,
        "failures": analysis_failures,
        "live_trading_enabled": False,
        "disclaimer": (
            "Descriptive market statistics only. This is not strategy performance, "
            "a forecast, or a profitability claim."
        ),
    }
    _write_json(OUTPUT, payload)
    _write_json(ANALYSIS_OUTPUT, analysis_payload)
    if failures:
        raise RuntimeError(f"Archive ingestion incomplete for {len(failures)} symbol(s); inspect artifacts")
    if analysis_failures:
        raise RuntimeError(f"Archive analysis incomplete for {len(analysis_failures)} symbol(s); inspect artifacts")


if __name__ == "__main__":
    main()
