import pytest
from binance_usdm_archive_analysis import analyze_archive_dataset

def sample():
    trade=[]; marks=[]
    for i,close in enumerate([100,110,99,108]):
        stamp=f"2026-09-01T{i:02d}:00:00+00:00"
        trade.append({"timestamp":stamp,"close":close,"quote_volume":1000})
        marks.append({"timestamp":stamp,"close":close*1.001})
    funding=[{"funding_time":f"2026-09-01T{i*8:02d}:00:00.005+00:00","realized_rate":rate,"is_realized":True} for i,rate in enumerate([0.001,-0.0005,0.0])]
    return {"symbol":"BTCUSDT","month":"2026-09","trade_candles":trade,"mark_price_candles":marks,"funding_history":funding}

def test_archive_analysis_reports_market_descriptives_not_strategy_performance():
    result=analyze_archive_dataset(sample())
    assert result["candle_count"]==4 and result["return_observation_count"]==3
    assert result["funding_observation_count"]==3
    assert result["trade_vs_mark_basis_bps"]["median"]==pytest.approx(10)
    assert result["realized_funding_rate"]["positive_count"]==1
    assert result["realized_funding_rate"]["negative_count"]==1
    assert result["realized_funding_rate"]["zero_count"]==1
    assert result["hourly_trade_returns"]["buy_and_hold_max_drawdown_pct"]>0
    assert result["live_trading_enabled"] is False
    assert any("no strategy" in x.lower() for x in result["limitations"])

def test_analysis_rejects_misaligned_trade_and_mark_rows():
    data=sample(); data["mark_price_candles"][1]["timestamp"]="2026-09-01T08:00:00+00:00"
    with pytest.raises(ValueError,match="timestamp mismatch"): analyze_archive_dataset(data)

def test_analysis_rejects_unrealized_funding():
    data=sample(); data["funding_history"][0]["is_realized"]=False
    with pytest.raises(ValueError,match="realized"): analyze_archive_dataset(data)
