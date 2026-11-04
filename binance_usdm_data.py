"""Read-only Binance USD-M public candles and funding history; no account or order access."""
from __future__ import annotations
import time
from datetime import datetime, timezone
from math import isfinite
from typing import Any
import requests

BASE_URL="https://fapi.binance.com"
KLINES_URL=f"{BASE_URL}/fapi/v1/klines"
MARK_KLINES_URL=f"{BASE_URL}/fapi/v1/markPriceKlines"
FUNDING_URL=f"{BASE_URL}/fapi/v1/fundingRate"
INTERVAL_MS={"5m":300000,"15m":900000,"1h":3600000,"4h":14400000,"1d":86400000}

def _validate(symbol, interval, limit, retries):
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if interval not in INTERVAL_MS: raise ValueError(f"interval must be one of: {', '.join(INTERVAL_MS)}")
    if not 2 <= limit <= 1000: raise ValueError("limit must be between 2 and 1000")
    if not 1 <= retries <= 5: raise ValueError("retries must be between 1 and 5")

def _client(session):
    client=session or requests.Session()
    client.headers.update({"User-Agent":"CryptoResearchLab/3.0"})
    return client

def _get(client,url,params,retries):
    for attempt in range(retries):
        response=client.get(url,params=params,timeout=20)
        if response.status_code==429 or response.status_code>=500:
            if attempt+1==retries: response.raise_for_status()
            try: delay=min(max(float(response.headers.get("Retry-After","1")),1),10)
            except (TypeError,ValueError): delay=1
            time.sleep(delay); continue
        response.raise_for_status()
        return response.json()
    raise RuntimeError("Binance USD-M request exhausted retries")

def _parse(rows,symbol,interval,now_ms,mark=False):
    if not isinstance(rows,list): raise ValueError(f"Binance USD-M {symbol}: expected candle list")
    result=[]; previous=-1; duration=INTERVAL_MS[interval]
    for row in rows:
        if not isinstance(row,list) or len(row)<7: raise ValueError(f"Binance USD-M {symbol}: malformed candle")
        try:
            opened=int(row[0]); o,h,l,c=(float(row[i]) for i in range(1,5)); closed=int(row[6])
            volume=None if mark else float(row[5]); quote=None if mark else float(row[7])
        except (TypeError,ValueError,IndexError) as exc: raise ValueError(f"Binance USD-M {symbol}: invalid candle field") from exc
        if opened<=previous: raise ValueError(f"Binance USD-M {symbol}: candles are not strictly chronological")
        previous=opened
        if not all(isfinite(v) and v>0 for v in (o,h,l,c)): raise ValueError("invalid OHLC price")
        if h<max(o,c) or l>min(o,c): raise ValueError("inconsistent OHLC values")
        if closed<opened or closed>=opened+duration: raise ValueError("invalid candle close timestamp")
        if closed>=now_ms: continue
        item={"timestamp":datetime.fromtimestamp(opened/1000,tz=timezone.utc).isoformat(),"open":o,"high":h,"low":l,"close":c}
        if not mark:
            if not isfinite(volume) or not isfinite(quote) or volume<0 or quote<0: raise ValueError("invalid candle volume")
            item.update({"volume":volume,"volume_unit":"base_asset","base_volume":volume,"quote_volume":quote,"quote_volume_unit":"USDT"})
        result.append(item)
    if not result: raise ValueError(f"Binance USD-M {symbol}: no completed candles returned")
    return result

def fetch_usdm_trade_candles(symbol,interval="1h",limit=200,*,session=None,now_ms=None,retries=3):
    """Fetch Binance USD-M trade-price candles, distinct from Spot."""
    _validate(symbol,interval,limit,retries)
    rows=_get(_client(session),KLINES_URL,{"symbol":symbol,"interval":interval,"limit":limit},retries)
    now=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    return _parse(rows,symbol,interval,now)

def fetch_usdm_mark_price_candles(symbol,interval="1h",limit=100,*,session=None,now_ms=None,retries=3):
    """Fetch Binance USD-M mark-price candles, kept separate from trade prices."""
    _validate(symbol,interval,limit,retries)
    rows=_get(_client(session),MARK_KLINES_URL,{"symbol":symbol,"interval":interval,"limit":limit},retries)
    now=now_ms if now_ms is not None else int(datetime.now(timezone.utc).timestamp()*1000)
    return _parse(rows,symbol,interval,now,mark=True)

def fetch_usdm_funding_history(symbol,limit=100,*,session=None,retries=3):
    """Fetch historical Binance USD-M funding records, sorted oldest first."""
    if not symbol.isalnum() or not symbol.isupper() or not symbol.endswith("USDT"):
        raise ValueError("symbol must be an uppercase USDT-quoted pair, e.g. BTCUSDT")
    if not 1<=limit<=1000: raise ValueError("funding history limit must be between 1 and 1000")
    if not 1<=retries<=5: raise ValueError("retries must be between 1 and 5")
    rows=_get(_client(session),FUNDING_URL,{"symbol":symbol,"limit":limit},retries)
    if not isinstance(rows,list): raise ValueError(f"Binance USD-M funding {symbol}: expected record list")
    records=[]; seen=set()
    for row in rows:
        if not isinstance(row,dict): raise ValueError("malformed funding record")
        try:
            if row.get("symbol")!=symbol: raise ValueError("funding symbol mismatch")
            stamp=int(row["fundingTime"]); rate=float(row["fundingRate"])
            mark=float(row["markPrice"]) if row.get("markPrice") not in (None,"") else None
            if not isfinite(rate) or (mark is not None and (not isfinite(mark) or mark<=0)): raise ValueError("non-finite funding field")
        except (KeyError,TypeError,ValueError) as exc: raise ValueError(f"Binance USD-M funding {symbol}: invalid record: {exc}") from exc
        if stamp in seen: continue
        seen.add(stamp)
        records.append({"provider":"binance_usdm_funding_history","symbol":symbol,"funding_time_ms":stamp,
          "funding_time":datetime.fromtimestamp(stamp/1000,tz=timezone.utc).isoformat(),
          "realized_rate":rate,"mark_price":mark,"is_realized":True})
    return sorted(records,key=lambda x:x["funding_time_ms"])
