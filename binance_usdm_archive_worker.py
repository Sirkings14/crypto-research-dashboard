"""Manual all-symbol ingestion of checksum-verified Binance Vision archives."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
import requests
from binance_usdm_archive import load_monthly_symbol_archives
from market_universe import DEFAULT_SYMBOLS
ROOT=Path(__file__).resolve().parent
OUTPUT=ROOT/"data"/"binance_usdm_archive_latest.json"
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--month",default="2026-09",help="Complete UTC month (YYYY-MM)"); args=parser.parse_args()
    client=requests.Session(); results={}; failures={}
    for symbol in DEFAULT_SYMBOLS:
        try:
            results[symbol]=load_monthly_symbol_archives(symbol,args.month,session=client); item=results[symbol]
            print(f"{symbol}: trade={len(item['trade_candles'])}, mark={len(item['mark_price_candles'])}, funding={len(item['funding_history'])}, checksums=verified")
        except (requests.RequestException,ValueError,OSError) as exc: failures[symbol]=str(exc); print(f"{symbol}: FAILED: {exc}")
    payload={"schema_version":1,"mode":"binance_usdm_historical_archive_ingestion","provider":"binance_vision_official_archive","month":args.month,"fetched_at":datetime.now(timezone.utc).isoformat(),"requested_symbols":list(DEFAULT_SYMBOLS),"success_count":len(results),"failure_count":len(failures),"results":results,"failures":failures,"live_trading_enabled":False,"disclaimer":"Historical public data only. Archive funding CSV lacks event mark prices; any paper replay must disclose the mark-candle-open proxy."}
    OUTPUT.parent.mkdir(parents=True,exist_ok=True); temp=OUTPUT.with_suffix(".tmp"); temp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); temp.replace(OUTPUT)
    if failures: raise RuntimeError(f"Archive ingestion incomplete for {len(failures)} symbol(s); inspect artifact diagnostics")
if __name__=="__main__": main()
