"""Descriptive market-quality analytics for verified Binance USD-M archives."""
from __future__ import annotations
import math
import statistics
from typing import Any

def _percentile(values: list[float], percentile: float) -> float | None:
    if not values: return None
    ordered=sorted(values); position=(len(ordered)-1)*percentile
    lower=int(math.floor(position)); upper=int(math.ceil(position))
    if lower==upper: return ordered[lower]
    weight=position-lower
    return ordered[lower]*(1-weight)+ordered[upper]*weight

def analyze_archive_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Summarize returns, mark/trade basis, funding and coverage; never score a strategy."""
    if not isinstance(dataset,dict): raise ValueError("dataset must be an object")
    symbol=dataset.get("symbol"); month=dataset.get("month")
    trade=dataset.get("trade_candles"); marks=dataset.get("mark_price_candles"); funding=dataset.get("funding_history")
    if not isinstance(symbol,str) or not isinstance(month,str): raise ValueError("dataset symbol and month are required")
    if not isinstance(trade,list) or len(trade)<2 or not isinstance(marks,list) or len(marks)!=len(trade):
        raise ValueError("trade and mark candle series must have matching rows")
    if not isinstance(funding,list) or not funding: raise ValueError("realized funding history is required")
    returns=[]; basis=[]; quote_volume=0.0
    previous_timestamp=None; previous_close=None; equity=1.0; peak=1.0; max_drawdown=0.0
    for index,(t,m) in enumerate(zip(trade,marks)):
        if t.get("timestamp")!=m.get("timestamp"): raise ValueError(f"trade/mark timestamp mismatch at row {index}")
        stamp=t.get("timestamp")
        if previous_timestamp is not None and stamp<=previous_timestamp: raise ValueError("timestamps must be strictly increasing")
        previous_timestamp=stamp
        close=float(t["close"]); mark_close=float(m["close"])
        if not math.isfinite(close) or close<=0 or not math.isfinite(mark_close) or mark_close<=0:
            raise ValueError("trade and mark close prices must be finite and positive")
        if previous_close is not None:
            value=close/previous_close-1.0
            returns.append(value); equity*=1.0+value; peak=max(peak,equity)
            max_drawdown=max(max_drawdown,(peak-equity)/peak)
        previous_close=close
        basis.append((mark_close/close-1.0)*10_000.0)
        volume=float(t.get("quote_volume",0.0))
        if not math.isfinite(volume) or volume<0: raise ValueError("quote volume must be finite and non-negative")
        quote_volume+=volume
    rates=[]
    prior=None
    for row in funding:
        if row.get("is_realized") is not True: raise ValueError("funding history must contain realized records only")
        rate=float(row.get("realized_rate"))
        if not math.isfinite(rate): raise ValueError("funding rate must be finite")
        stamp=row.get("funding_time")
        if not isinstance(stamp,str) or (prior is not None and stamp<=prior): raise ValueError("funding timestamps must be strictly chronological")
        prior=stamp; rates.append(rate)
    abs_basis=[abs(x) for x in basis]
    abs_returns=[abs(x) for x in returns]
    return {
      "schema_version":1,"mode":"historical_market_descriptive_analysis_only","symbol":symbol,"month":month,
      "candle_count":len(trade),"return_observation_count":len(returns),"funding_observation_count":len(rates),
      "trade_vs_mark_basis_bps":{"mean":statistics.fmean(basis),"median":statistics.median(basis),"max_abs":max(abs_basis),"p95_abs":_percentile(abs_basis,0.95)},
      "hourly_trade_returns":{"mean_pct":statistics.fmean(returns)*100.0,"median_pct":statistics.median(returns)*100.0,"volatility_pct":statistics.pstdev(returns)*100.0,"annualized_volatility_pct":statistics.pstdev(returns)*math.sqrt(24*365)*100.0,"p95_abs_pct":_percentile(abs_returns,0.95)*100.0,"max_abs_pct":max(abs_returns)*100.0,"buy_and_hold_max_drawdown_pct":max_drawdown*100.0},
      "realized_funding_rate":{"mean":statistics.fmean(rates),"median":statistics.median(rates),"min":min(rates),"max":max(rates),"positive_count":sum(x>0 for x in rates),"negative_count":sum(x<0 for x in rates),"zero_count":sum(x==0 for x in rates),"simple_sum_pct":sum(rates)*100.0},
      "quote_volume_usdt_sum":quote_volume,
      "limitations":["Descriptive historical market statistics only; no strategy or execution performance is evaluated.","Annualized volatility extrapolates a single month and is not a forecast.","Funding-rate sum is not a position-specific realized P&L calculation.","Funding archive rows lack event mark prices; the paper ledger must disclose its mark-candle-open proxy."],
      "live_trading_enabled":False
    }
