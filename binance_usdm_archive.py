"""Checksum-verified Binance Vision monthly USD-M archive ingestion."""
from __future__ import annotations
import csv, hashlib, io, math, zipfile
from datetime import datetime, timezone
from typing import Any
import requests
ARCHIVE_BASE="https://data.binance.vision/data/futures/um/monthly"
HOUR_MS=3_600_000
def _month_bounds(month):
    if not isinstance(month,str) or len(month)!=7 or month[4]!="-": raise ValueError("month must use YYYY-MM format")
    try:
        y,m=(int(x) for x in month.split("-")); start=datetime(y,m,1,tzinfo=timezone.utc)
        end=datetime(y+(m==12),1 if m==12 else m+1,1,tzinfo=timezone.utc)
    except (TypeError,ValueError) as exc: raise ValueError("month must be a valid YYYY-MM value") from exc
    a,b=int(start.timestamp()*1000),int(end.timestamp()*1000)
    return a,b,(b-a)//HOUR_MS
def _iso(ms): return datetime.fromtimestamp(ms/1000,tz=timezone.utc).isoformat()
def _download_csv(session,url):
    response=session.get(url,timeout=30); response.raise_for_status()
    check=session.get(url+".CHECKSUM",timeout=15); check.raise_for_status()
    parts=check.text.strip().split()
    if not parts or len(parts[0])!=64: raise ValueError("archive checksum file has invalid SHA-256")
    digest=hashlib.sha256(response.content).hexdigest()
    if digest.lower()!=parts[0].lower(): raise ValueError("archive SHA-256 checksum mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            names=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(names)!=1: raise ValueError("archive must contain exactly one CSV file")
            with z.open(names[0]) as raw: rows=list(csv.reader(io.TextIOWrapper(raw,encoding="utf-8",newline="")))
    except (zipfile.BadZipFile,UnicodeDecodeError) as exc: raise ValueError(f"invalid archive ZIP/CSV: {exc}") from exc
    if rows and rows[0] and not rows[0][0].strip().isdigit(): rows=rows[1:]
    if not rows: raise ValueError("archive CSV contains no data rows")
    return rows,{"url":url,"sha256":digest,"checksum_valid":True,"archive_csv":names[0],"data_row_count":len(rows)}
def _parse_klines(rows,symbol,month,expected):
    out=[]; previous=None
    for n,row in enumerate(rows,1):
        if len(row)<12: raise ValueError(f"{symbol} {month}: kline row {n} has fewer than 12 columns")
        try:
            t=int(row[0]); o,h,l,c=(float(row[i]) for i in range(1,5)); v=float(row[5]); close_t=int(row[6])
            q=float(row[7]); count=int(row[8]); tb=float(row[9]); tq=float(row[10])
        except (TypeError,ValueError,IndexError) as exc: raise ValueError(f"{symbol} {month}: invalid kline row {n}") from exc
        vals=(o,h,l,c,v,q,tb,tq)
        if not all(math.isfinite(x) for x in vals): raise ValueError(f"{symbol} {month}: non-finite kline value at row {n}")
        if min(o,h,l,c)<=0 or min(v,q,tb,tq)<0 or h<max(o,c) or l>min(o,c) or l>h: raise ValueError(f"{symbol} {month}: invalid OHLC/volume at row {n}")
        if close_t<t or close_t>=t+HOUR_MS: raise ValueError(f"{symbol} {month}: invalid candle close time at row {n}")
        if previous is not None and t<=previous: raise ValueError(f"{symbol} {month}: kline timestamps are not strictly increasing")
        previous=t
        out.append({"timestamp":_iso(t),"open":o,"high":h,"low":l,"close":c,"volume":v,"volume_unit":"base_asset","base_volume":v,"quote_volume":q,"quote_volume_unit":"USDT","trade_count":count,"taker_buy_base_volume":tb,"taker_buy_quote_volume":tq})
    if len(out)!=expected: raise ValueError(f"{symbol} {month}: expected {expected} hourly candles, found {len(out)}")
    start,_,_=_month_bounds(month)
    stamps=[int(datetime.fromisoformat(x["timestamp"]).timestamp()*1000) for x in out]
    if stamps!=[start+i*HOUR_MS for i in range(expected)]: raise ValueError(f"{symbol} {month}: hourly coverage incomplete or outside month")
    return out
def _parse_funding(rows,symbol,month):
    start,end,_=_month_bounds(month); out=[]; previous=None
    for n,row in enumerate(rows,1):
        if len(row)<3: raise ValueError(f"{symbol} {month}: funding row {n} has fewer than 3 columns")
        try: t=int(row[0]); interval=float(row[1]); rate=float(row[2])
        except (TypeError,ValueError,IndexError) as exc: raise ValueError(f"{symbol} {month}: invalid funding row {n}") from exc
        if not math.isfinite(interval) or interval<=0 or not math.isfinite(rate): raise ValueError(f"{symbol} {month}: invalid funding interval/rate at row {n}")
        if not start<=t<end: raise ValueError(f"{symbol} {month}: funding timestamp outside requested month")
        if t%HOUR_MS>10: raise ValueError(f"{symbol} {month}: funding timestamp is more than 10ms after hourly open")
        if previous is not None and t<=previous: raise ValueError(f"{symbol} {month}: funding timestamps are not strictly increasing")
        previous=t
        out.append({"provider":"binance_vision_monthly_archive","symbol":symbol,"funding_time_ms":t,"funding_time":_iso(t),"realized_rate":rate,"mark_price":None,"is_realized":True,"funding_interval_hours":interval,"mark_price_source":"mark_candle_open_proxy"})
    if not out: raise ValueError(f"{symbol} {month}: no realized funding rows found")
    return out
def load_monthly_symbol_archives(symbol,month,*,session=None):
    """Download, checksum-verify and strictly parse one symbol's monthly files."""
    if not isinstance(symbol,str) or not symbol.isalnum() or symbol!=symbol.upper() or not symbol.endswith("USDT"): raise ValueError("symbol must be an uppercase USDT-quoted pair")
    _,_,hours=_month_bounds(month); client=session or requests.Session()
    urls={"trade_klines":f"{ARCHIVE_BASE}/klines/{symbol}/1h/{symbol}-1h-{month}.zip","mark_price_klines":f"{ARCHIVE_BASE}/markPriceKlines/{symbol}/1h/{symbol}-1h-{month}.zip","funding_rate":f"{ARCHIVE_BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"}
    raw={}; provenance={}
    for name,url in urls.items(): raw[name],provenance[name]=_download_csv(client,url)
    trade=_parse_klines(raw["trade_klines"],symbol,month,hours); marks=_parse_klines(raw["mark_price_klines"],symbol,month,hours)
    if [x["timestamp"] for x in trade]!=[x["timestamp"] for x in marks]: raise ValueError(f"{symbol} {month}: trade and mark-price candle timestamps differ")
    funding=_parse_funding(raw["funding_rate"],symbol,month)
    return {"schema_version":1,"mode":"binance_usdm_historical_archive_ingestion","provider":"binance_vision_official_archive","venue":"binance","product":"usd_m_perpetual","symbol":symbol,"instrument":symbol,"interval":"1h","month":month,"trade_candles":trade,"mark_price_candles":marks,"funding_history":funding,"archive_provenance":provenance,"validation":{"all_checksums_valid":True,"expected_hourly_candles":hours,"trade_candle_count":len(trade),"mark_price_candle_count":len(marks),"funding_record_count":len(funding),"trade_mark_timestamps_identical":True,"hourly_gaps":0,"funding_timestamp_tolerance_ms":10,"funding_mark_price_is_proxy_when_missing":True},"live_trading_enabled":False}
