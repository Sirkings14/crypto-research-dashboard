from __future__ import annotations

import json
from hashlib import sha256
from io import StringIO
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from engine import digest_bytes, evaluate_holdout, infer_interval, load_experiments, load_ohlcv, record_experiment, run_sweep
from market_data import fetch_ohlcv


APP_DIR = Path(__file__).resolve().parent
DATABASE_PATH = APP_DIR / "data" / "experiments.sqlite3"


def settings_fingerprint(settings: dict) -> str:
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()

st.set_page_config(page_title="Crypto Research Lab", page_icon="📈", layout="wide")
st.title("Crypto Research Lab")
st.caption("Accelerated historical research · exchange-independent · no live orders")

with st.sidebar:
    st.header("Research baseline")
    st.write("**BTC/USDT · 5-minute bars · UTC**")
    st.caption("Public market candles are used for research only. No exchange account or API key is connected.")
    source_mode = st.radio("Candle source", ["Fetch public Binance Spot data", "Upload OHLCV CSV"], index=0)
    if source_mode == "Fetch public Binance Spot data":
        market_symbol = st.text_input("Spot market symbol", value="BTCUSDT", help="For example BTCUSDT. This is a public market-data request only.")
        interval = st.selectbox("Bar interval", ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"], index=2)
        lookback = st.select_slider("Historical lookback", options=[30, 60, 90, 180, 365], value=90, format_func=lambda x: f"{x} days")
        uploaded = None
        data_source = "Binance public Spot market data"
    else:
        market_symbol = st.text_input("Dataset market label", value="BTC/USDT")
        data_source = st.text_input("Dataset source label", value="User-provided CSV")
        interval, lookback = "5m", 90
        uploaded = st.file_uploader("Upload OHLCV CSV", type=["csv"])
    st.divider()
    st.subheader("Execution assumptions")
    initial = st.number_input("Starting equity (USDT)", min_value=10.0, max_value=10_000_000.0, value=1_000.0, step=100.0)
    allocation = st.slider("Maximum spot allocation", min_value=0.05, max_value=0.95, value=0.50, step=0.05)
    fee_bps = st.number_input("Fee per side (basis points)", min_value=0.0, max_value=500.0, value=10.0, step=1.0)
    slippage_bps = st.number_input("Slippage per side (basis points)", min_value=0.0, max_value=500.0, value=5.0, step=1.0)
    max_drawdown = st.slider("Maximum validation drawdown", min_value=5.0, max_value=80.0, value=25.0, step=1.0)
    st.divider()
    st.subheader("SMA parameter grid")
    fast_min = st.number_input("Fast window minimum", min_value=2, max_value=100, value=5, step=1)
    fast_max = st.number_input("Fast window maximum", min_value=2, max_value=200, value=30, step=1)
    fast_step = st.number_input("Fast window step", min_value=1, max_value=25, value=5, step=1)
    slow_min = st.number_input("Slow window minimum", min_value=5, max_value=300, value=20, step=5)
    slow_max = st.number_input("Slow window maximum", min_value=10, max_value=500, value=120, step=5)
    slow_step = st.number_input("Slow window step", min_value=1, max_value=50, value=10, step=1)

settings = {
    "market_symbol": market_symbol.strip(), "data_source": data_source.strip(),
    "initial": float(initial), "allocation": float(allocation), "fee_bps": float(fee_bps),
    "slippage_bps": float(slippage_bps), "max_drawdown": float(max_drawdown),
    "fast_min": int(fast_min), "fast_max": int(fast_max), "fast_step": int(fast_step),
    "slow_min": int(slow_min), "slow_max": int(slow_max), "slow_step": int(slow_step),
    "train_fraction": 0.60, "validation_fraction": 0.20,
}
settings_hash = settings_fingerprint(settings)

st.subheader("Persistent paper run · BTC/USDT · 5-minute bars")
st.caption("A scheduled GitHub Actions worker advances this fixed SMA 10/40 paper account from completed public candles and saves each result in the repository. No real orders or exchange credentials are used.")

@st.cache_data(ttl=60, show_spinner=False)
def load_paper_ledger() -> tuple[dict, pd.DataFrame]:
    root = "https://raw.githubusercontent.com/Sirkings14/crypto-research-dashboard/main/data"
    state_response = requests.get(f"{root}/paper_state.json", timeout=20)
    state_response.raise_for_status()
    history_response = requests.get(f"{root}/paper_history.csv", timeout=30)
    history_response.raise_for_status()
    history = pd.read_csv(StringIO(history_response.text))
    return state_response.json(), history


@st.fragment(run_every="60s")
def persistent_paper_panel() -> None:
    try:
        state, history = load_paper_ledger()
        equity = float(state["equity_usdt"])
        starting = float(state["initial_equity_usdt"])
        total_return = (equity / starting - 1.0) * 100.0
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Paper equity", f"{equity:,.2f} USDT", f"{equity - starting:+,.2f} USDT · {total_return:+.2f}%")
        p2.metric("Maximum drawdown", f"{float(state['max_drawdown_pct']):.2f}%")
        p3.metric("Current position", "LONG" if float(state["position"]) > 0 else "CASH", f"{float(state['position'])*100:.0f}% allocated")
        p4.metric("Completed paper bars", f"{int(state['processed_bars']):,}")
        st.caption(f"Started {state['started_utc']} · latest completed bar {state['last_bar_open_utc']} UTC · last ledger update {state['last_updated_utc']}. Strategy: {state['strategy']}; estimated costs include {state['fee_bps_per_side']:.1f} bps fee and {state['slippage_bps_per_side']:.1f} bps slippage per side.")
        updated = pd.Timestamp(state["last_updated_utc"])
        if updated.tzinfo is None:
            updated = updated.tz_localize("UTC")
        if (pd.Timestamp.now(tz="UTC") - updated).total_seconds() > 15 * 60:
            st.warning("The scheduled paper worker has not updated recently. Check the repository Actions tab for the latest run.")
        if history.empty:
            st.info("The ledger is initialized and waiting for the next completed candle.")
            return
        view = history.tail(3000).copy()
        view["bar_open_utc"] = pd.to_datetime(view["bar_open_utc"], utc=True)
        chart = go.Figure(go.Scatter(x=view["bar_open_utc"], y=view["equity_usdt"], name="Paper equity", line={"width": 2}))
        chart.update_layout(height=340, xaxis_title="UTC", yaxis_title="Simulated equity (USDT)", margin={"t": 10})
        st.plotly_chart(chart, use_container_width=True)
        trades = history[history["action"].isin(["ENTER_LONG", "EXIT_TO_CASH"])].tail(20).iloc[::-1]
        st.write("Recent simulated position changes")
        st.dataframe(trades[["bar_close_utc", "action", "close", "equity_usdt", "drawdown_pct", "estimated_cost_usdt"]], use_container_width=True, hide_index=True)
    except Exception as exc:
        st.info(f"The scheduled paper ledger will appear here after its first successful run. ({exc})")


persistent_paper_panel()

if source_mode == "Fetch public Binance Spot data":
    @st.cache_data(ttl=60, show_spinner=False)
    def cached_public_data(symbol: str, bar_interval: str, days: int) -> pd.DataFrame:
        return fetch_ohlcv(symbol, bar_interval, days)
    try:
        with st.spinner(f"Loading {lookback} days of completed {interval} candles for {market_symbol.upper()}…"):
            data = cached_public_data(market_symbol, interval, int(lookback))
        raw = data.to_csv(index=False).encode("utf-8")
        st.download_button("Download loaded candles as CSV", raw, file_name=f"{market_symbol.upper()}_{interval}_{lookback}d.csv", mime="text/csv")
    except Exception as exc:
        st.error(f"Public candle download failed: {exc}")
        st.info("Switch to “Upload OHLCV CSV” to continue with a local candle file.")
        st.stop()
else:
    if not uploaded:
        st.info("Upload historical OHLCV CSV to begin. No sample P&L is shown, so synthetic or illustrative returns cannot be mistaken for trading results.")
        st.markdown("Required columns: `timestamp, open, high, low, close`; `volume` is optional. Use 5-minute BTC/USDT candles in UTC for the selected baseline.")
        st.markdown("[Read the project setup and research rules](README.md)")
        st.stop()
    raw = uploaded.getvalue()
    try:
        data = load_ohlcv(raw)
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
source_digest = digest_bytes(raw)

interval_label, interval_seconds = infer_interval(data)
gaps = int((data["timestamp"].diff().dt.total_seconds() > interval_seconds * 1.5).sum()) if interval_seconds > 0 else 0
c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows", f"{len(data):,}")
c2.metric("Observed interval", interval_label)
c3.metric("UTC coverage", f"{data['timestamp'].iloc[0]:%Y-%m-%d} → {data['timestamp'].iloc[-1]:%Y-%m-%d}")
c4.metric("Missing-bar gaps", f"{gaps:,}")
if interval_seconds != 300:
    st.warning(f"The selected baseline is 5-minute bars; this file appears to use {interval_label}. The engine will run the uploaded interval as-is.")
if gaps:
    st.warning("The data contains time gaps. Backtest returns across those gaps may not represent the intended bar-by-bar execution.")

st.subheader("Run a parameter sweep")
st.write("Signals use only completed candles. A new target position is applied at the next candle open; the prior position earns the close-to-open gap and the new one earns the open-to-close move.")
st.write("The chronological split is 60% training, 20% validation, 20% holdout. Candidates are ranked on validation return after costs, subject to your drawdown ceiling. Holdout metrics stay hidden until you explicitly evaluate one frozen candidate.")

if st.button("Run accelerated sweep", type="primary"):
    if fast_max < fast_min or slow_max < slow_min:
        st.error("Each parameter maximum must be at least its minimum.")
    else:
        fast_windows = list(range(int(fast_min), int(fast_max) + 1, int(fast_step)))
        slow_windows = list(range(int(slow_min), int(slow_max) + 1, int(slow_step)))
        candidates = [(f, s) for f in fast_windows for s in slow_windows if f < s]
        if len(candidates) > 600:
            st.error(f"This grid has {len(candidates)} valid pairs; reduce it to 600 or fewer.")
        elif not market_symbol.strip() or not data_source.strip():
            st.error("Add a market label and data source label so this run can be identified later.")
        elif not candidates:
            st.error("The selected ranges produced no fast < slow parameter pairs.")
        else:
            try:
                with st.spinner(f"Evaluating {len(candidates):,} candidates across {len(data):,} bars…"):
                    result, split = run_sweep(
                        data, fast_windows, slow_windows, float(initial), float(allocation),
                        float(fee_bps), float(slippage_bps), float(max_drawdown),
                    )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["sweep"] = result
                st.session_state["sweep_hash"] = source_digest
                st.session_state["sweep_settings_hash"] = settings_hash
                st.session_state["sweep_split"] = split
                st.session_state.pop("holdout", None)

sweep = st.session_state.get("sweep")
if sweep is not None and st.session_state.get("sweep_hash") != source_digest:
    st.warning("The uploaded data changed. Run the sweep again before evaluating a candidate.")
    sweep = None
if sweep is not None and st.session_state.get("sweep_settings_hash") != settings_hash:
    st.warning("A market label, cost, risk limit, or parameter-grid setting changed. Run the sweep again before evaluating a candidate.")
    sweep = None

if sweep is not None:
    visible = sweep[sweep["passes_drawdown_limit"]].copy()
    if visible.empty:
        st.warning("No candidate stayed within the validation drawdown limit. Consider revising the strategy hypothesis or risk limit; don’t raise risk just to force a winner.")
    else:
        st.success(f"{len(visible):,} of {len(sweep):,} candidates passed the validation drawdown ceiling.")
        show = visible.head(25)[[
            "fast", "slow", "train_return_pct", "train_max_drawdown_pct", "validation_return_pct",
            "validation_max_drawdown_pct", "validation_position_changes",
        ]].rename(columns={
            "fast": "Fast SMA", "slow": "Slow SMA", "train_return_pct": "Train net %",
            "train_max_drawdown_pct": "Train max DD %", "validation_return_pct": "Validation net %",
            "validation_max_drawdown_pct": "Validation max DD %", "validation_position_changes": "Validation position changes",
        })
        st.dataframe(show.style.format({c: "{:.2f}" for c in show.columns if " %" in c}), use_container_width=True, hide_index=True)
        choices = [(int(r.fast), int(r.slow)) for r in visible.head(25).itertuples()]
        selected = st.selectbox("Freeze one candidate for the holdout", choices, format_func=lambda p: f"Fast {p[0]} / Slow {p[1]}")
        st.warning("The holdout is for one final check. If you use its result to change the strategy, it is no longer an untouched holdout. Every evaluation is logged locally.")
        if st.button("Evaluate frozen candidate on holdout"):
            split = st.session_state["sweep_split"]
            metrics, chart = evaluate_holdout(
                data, selected[0], selected[1], split, float(initial), float(allocation), float(fee_bps), float(slippage_bps),
            )
            assumptions = {
                "market_symbol_label": market_symbol.strip(), "data_source_label": data_source.strip(),
                "bar_baseline": "5m", "observed_bar_interval": interval_label, "timestamp_timezone": "UTC",
                "strategy_version": "sma-crossover-v1",
                "initial_equity": float(initial), "allocation": float(allocation),
                "fee_bps_per_side": float(fee_bps), "slippage_bps_per_side": float(slippage_bps),
                "training_fraction": 0.60, "validation_fraction": 0.20,
            }
            record_experiment(str(DATABASE_PATH), {
                "data_sha256": source_digest, "fast": selected[0], "slow": selected[1],
                "assumptions": assumptions, "results": metrics,
            })
            st.session_state["holdout"] = {
                "metrics": metrics, "chart": chart, "candidate": selected,
                "hash": source_digest, "settings_hash": settings_hash,
            }

holdout = st.session_state.get("holdout")
if holdout and holdout["hash"] == source_digest and holdout["settings_hash"] == settings_hash:
    st.subheader(f"Frozen holdout: fast {holdout['candidate'][0]} / slow {holdout['candidate'][1]}")
    metrics = holdout["metrics"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Holdout net return", f"{metrics['return_pct']:.2f}%")
    k2.metric("Holdout max drawdown", f"{metrics['max_drawdown_pct']:.2f}%")
    k3.metric("Position changes", f"{metrics['position_changes']}")
    k4.metric("Buy/hold net return", f"{metrics['buy_hold_return_pct']:.2f}%")
    st.caption(f"Final simulated equity from a fresh {initial:,.2f} USDT holdout account: {metrics['final_equity']:,.2f} USDT. Fees and slippage are estimates, not actual fills.")
    chart = holdout["chart"]
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=chart["timestamp"], y=chart["strategy_equity"], name="Strategy (net)", line={"width": 2}))
    figure.add_trace(go.Scatter(x=chart["timestamp"], y=chart["buy_hold_equity"], name="Buy and hold", line={"dash": "dot"}))
    figure.update_layout(height=420, xaxis_title="UTC", yaxis_title="Equity (USDT)", hovermode="x unified", legend_title="Holdout")
    st.plotly_chart(figure, use_container_width=True)

st.subheader("Experiment audit log")
history = load_experiments(str(DATABASE_PATH))
if history.empty:
    st.caption("No holdout evaluations recorded yet.")
else:
    history["results"] = history["results_json"].map(json.loads)
    history["holdout_net_pct"] = history["results"].map(lambda x: x.get("return_pct"))
    history["holdout_max_dd_pct"] = history["results"].map(lambda x: x.get("max_drawdown_pct"))
    st.dataframe(history[["id", "created_utc", "fast", "slow", "holdout_net_pct", "holdout_max_dd_pct", "data_sha256"]], use_container_width=True, hide_index=True)

st.caption("Research software only. Historical and simulated performance does not predict future results. This app has no exchange connection and cannot trade real money.")
