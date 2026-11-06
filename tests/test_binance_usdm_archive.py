from datetime import datetime,timezone
import hashlib,io,zipfile
import pytest
from binance_usdm_archive import load_monthly_symbol_archives
class Response:
    def __init__(self,content): self.content=content; self.text=content.decode("utf-8",errors="replace")
    def raise_for_status(self): pass
class Session:
    def __init__(self,responses): self.responses=list(responses); self.urls=[]
    def get(self,url,**kwargs): self.urls.append((url,kwargs)); return self.responses.pop(0)
def archive(name,text):
    out=io.BytesIO()
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z: z.writestr(name,text)
    content=out.getvalue()
    return Response(content),Response(hashlib.sha256(content).hexdigest().encode()+b"  archive.zip\n")
def sample_session(offset=5):
    start=int(datetime(2026,9,1,tzinfo=timezone.utc).timestamp()*1000); trade=[]; mark=[]
    for i in range(720):
        t=start+i*3_600_000
        trade.append(f"{t},100,102,99,101,2,{t+3599999},202,3,1,101,0")
        mark.append(f"{t},100,102,99,101,0,{t+3599999},0,0,0,0,0")
    funding=["calc_time,funding_interval_hours,last_funding_rate"]+[f"{start+i*8*3_600_000+offset},8,0.0001" for i in range(90)]
    responses=[]
    for name,rows in [("BTCUSDT-1h-2026-09.csv","\n".join(trade)+"\n"),("BTCUSDT-markPrice-1h-2026-09.csv","\n".join(mark)+"\n"),("BTCUSDT-fundingRate-2026-09.csv","\n".join(funding)+"\n")]: responses.extend(archive(name,rows))
    return Session(responses)
def test_ingests_verified_archives_and_normalizes_separate_series():
    client=sample_session(); data=load_monthly_symbol_archives("BTCUSDT","2026-09",session=client)
    assert data["validation"]["all_checksums_valid"] and data["validation"]["trade_candle_count"]==720
    assert data["validation"]["mark_price_candle_count"]==720 and data["validation"]["funding_record_count"]==90
    assert data["trade_candles"][0]["volume_unit"]=="base_asset" and data["funding_history"][0]["is_realized"]
    assert data["funding_history"][0]["mark_price"] is None and data["live_trading_enabled"] is False
    assert len(client.urls)==6
def test_checksum_mismatch_fails_closed():
    client=sample_session(); client.responses[1]=Response(b"0"*64+b" archive.zip\n")
    with pytest.raises(ValueError,match="checksum mismatch"): load_monthly_symbol_archives("BTCUSDT","2026-09",session=client)
def test_missing_hour_is_not_forward_filled():
    client=sample_session()
    with zipfile.ZipFile(io.BytesIO(client.responses[0].content)) as z: rows=z.read(z.namelist()[0]).decode().splitlines()
    rows.pop(100); client.responses[0],client.responses[1]=archive("BTCUSDT-1h-2026-09.csv","\n".join(rows)+"\n")
    with pytest.raises(ValueError,match="expected 720 hourly candles"): load_monthly_symbol_archives("BTCUSDT","2026-09",session=client)
def test_trade_mark_timestamp_mismatch_rejected():
    client=sample_session()
    with zipfile.ZipFile(io.BytesIO(client.responses[2].content)) as z: rows=z.read(z.namelist()[0]).decode().splitlines()
    row=rows[5].split(","); row[0]=str(int(row[0])+3_600_000); row[6]=str(int(row[6])+3_600_000); rows[5]=",".join(row)
    client.responses[2],client.responses[3]=archive("BTCUSDT-markPrice-1h-2026-09.csv","\n".join(rows)+"\n")
    with pytest.raises(ValueError,match="strictly increasing|timestamps differ"): load_monthly_symbol_archives("BTCUSDT","2026-09",session=client)
def test_funding_offset_over_ten_ms_rejected():
    with pytest.raises(ValueError,match="more than 10ms"): load_monthly_symbol_archives("BTCUSDT","2026-09",session=sample_session(offset=11))
def test_invalid_month_and_symbol_rejected():
    with pytest.raises(ValueError,match="valid YYYY-MM"): load_monthly_symbol_archives("BTCUSDT","2026-13",session=sample_session())
    with pytest.raises(ValueError,match="uppercase USDT"): load_monthly_symbol_archives("btcusdt","2026-09",session=sample_session())
