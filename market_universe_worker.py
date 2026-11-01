"""Collect public candles and generate a separate non-executing analysis report."""
from __future__ import annotations

import json
from pathlib import Path

from market_analysis import analyze_snapshot
from market_universe import scan_universe

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
SNAPSHOT_OUTPUT = DATA_DIR / "market_universe_latest.json"
ANALYSIS_OUTPUT = DATA_DIR / "market_analysis_latest.json"


def _atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    snapshot = scan_universe(interval="1h", limit=200)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_json(SNAPSHOT_OUTPUT, snapshot)

    analysis = analyze_snapshot(snapshot)
    _atomic_json(ANALYSIS_OUTPUT, analysis)

    print(
        f"Public market scan: {snapshot['success_count']} successful "
        f"market/pair datasets; {snapshot['failure_count']} unavailable."
    )
    print(
        f"Research analysis: {analysis['dataset_count']} datasets analyzed; "
        f"{analysis['failure_count']} analysis failures; "
        f"{len(analysis['spot_futures_comparison'])} Spot/Swap comparisons."
    )
    if snapshot["success_count"] == 0:
        raise RuntimeError("No market datasets were collected; see failure details in the snapshot.")
    if analysis["dataset_count"] == 0:
        raise RuntimeError("No datasets could be analyzed; see analysis failure details.")


if __name__ == "__main__":
    main()
