"""Run a bounded public Spot/Futures market-data scan and persist its snapshot."""
from __future__ import annotations

import json
from pathlib import Path

from market_universe import scan_universe

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "market_universe_latest.json"


def main() -> None:
    snapshot = scan_universe(interval="1h", limit=200)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)

    print(
        f"Public market scan: {snapshot['success_count']} successful "
        f"market/pair datasets; {snapshot['failure_count']} unavailable."
    )
    if snapshot["success_count"] == 0:
        raise RuntimeError("No market datasets were collected; see failure details in the snapshot.")


if __name__ == "__main__":
    main()
