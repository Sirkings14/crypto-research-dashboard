from __future__ import annotations
import pytest
from binance_usdm_data import FUNDING_URL,KLINES_URL,MARK_KLINES_URL,fetch_usdm_trade_candles,fetch_usdm_mark_price_candles,fetch_usdm_funding_history

class Response:
    status_code=200
    headers={}
    def __init__(self,payload): self.payload=payload
    def raise_for_status(self): pass
    def json(self): return self.payload

class Session:
    def __init__(self,payload): self.payload=payload; self.headers={}; self.url=None; self.params=None
    def get(self,url,*,params,timeout): self.url=url; self.params=params; return Response(self.payload)

def candle(t,close_t):
    return [t,"100","105","99","101","2.5",close_t,"250","1","1","100","0"]

def test_trade_candles_use_usdm_endpoint_and_filter_open_bar():
    s=Session([candle(1700000000000,1700000299999),candle(1700000300000,1700000599999)])
    data=fetch_usdm_trade_candles("BTCUSDT","5m",2,session=s,now_ms=1700000500000)
    assert s.url==KLINES_URL and len(data)==1
    assert data[0]["volume_unit"]=="base_asset" and data[0]["quote_volume"]==250

def test_mark_price_candles_are_separate_from_trade_volume():
    s=Session([candle(1700000000000,1700000299999)])
    data=fetch_usdm_mark_price_candles("BTCUSDT","5m",2,session=s,now_ms=1700000500000)
    assert s.url==MARK_KLINES_URL and "volume" not in data[0]

def test_funding_history_is_sorted_and_deduplicated():
    s=Session([
      {"symbol":"BTCUSDT","fundingTime":1700028800000,"fundingRate":"0.0002","markPrice":"61000"},
      {"symbol":"BTCUSDT","fundingTime":1700000000000,"fundingRate":"-0.0001","markPrice":"60000"},
      {"symbol":"BTCUSDT","fundingTime":1700000000000,"fundingRate":"-0.0001","markPrice":"60000"}])
    data=fetch_usdm_funding_history("BTCUSDT",3,session=s)
    assert s.url==FUNDING_URL and [x["funding_time_ms"] for x in data]==[1700000000000,1700028800000]
    assert data[0]["realized_rate"]==pytest.approx(-0.0001)

@pytest.mark.parametrize("symbol",["btcusdt","BTCUSD",""])
def test_invalid_symbol_rejected(symbol):
    with pytest.raises(ValueError,match="USDT-quoted"): fetch_usdm_trade_candles(symbol)

def test_out_of_order_candles_rejected():
    s=Session([candle(1700000300000,1700000599999),candle(1700000000000,1700000299999)])
    with pytest.raises(ValueError,match="chronological"):
        fetch_usdm_trade_candles("BTCUSDT",session=s,now_ms=1800000000000)

def test_non_finite_funding_rate_rejected():
    s=Session([{"symbol":"BTCUSDT","fundingTime":1700000000000,"fundingRate":"nan","markPrice":"60000"}])
    with pytest.raises(ValueError,match="invalid record"): fetch_usdm_funding_history("BTCUSDT",session=s)
