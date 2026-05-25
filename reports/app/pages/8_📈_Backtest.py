"""Backtest — launch walk-forward runs and inspect history."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from reports.app._components import actions
from reports.app._components.auth import guard_localhost
from reports.app._components.jobs import get_status, submit_job
from reports.app._components.ui import confirm_toggle, job_badge
from sportscards.db.models import BacktestRun
from sportscards.db.session import session_scope

st.set_page_config(page_title="Backtest", page_icon="📈", layout="wide")
guard_localhost()
job_badge()
st.title("📈 Backtest")


def _render_nav_chart(summary: dict | None) -> None:
    if not summary:
        st.info("No summary in this run.")
        return
    # Try to render a NAV series if present
    nav = summary.get("nav") or summary.get("nav_series")
    if nav:
        try:
            df = pd.DataFrame(nav)
            if "as_of" in df.columns and "nav" in df.columns:
                st.line_chart(df.set_index("as_of")["nav"])
            else:
                st.dataframe(df, use_container_width=True)
        except Exception:
            st.json(nav)
    # Metrics
    metrics = {
        k: summary.get(k)
        for k in (
            "total_return",
            "max_drawdown",
            "ir",
            "fee_drag_pct",
            "total_fees_usd",
            "trade_count",
            "final_nav",
        )
        if summary.get(k) is not None
    }
    if metrics:
        cols = st.columns(len(metrics))
        for col, (k, v) in zip(cols, metrics.items(), strict=False):
            col.metric(k, f"{v:.4f}" if isinstance(v, (int, float)) else str(v))
    # Fallthrough: full JSON
    with st.expander("Full summary_json"):
        st.json(summary)


# --- Launch form -------------------------------------------------------------
st.subheader("Run new backtest")
with st.form("bt_form"):
    today = date.today()
    start = st.date_input("Start", value=today - timedelta(days=365))
    end = st.date_input("End", value=today)
    aum = st.number_input("AUM (USD)", 1000.0, 1e9, 1_000_000.0)
    rebalance_days = st.number_input("Rebalance frequency (days)", 1, 365, 90)
    ok = confirm_toggle("confirm_bt")
    submitted = st.form_submit_button("Run", disabled=not ok)
    if submitted:
        run_id = submit_job(
            "backtest_run",
            actions.backtest_run,
            params={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "aum": aum,
                "rebalance_days": int(rebalance_days),
            },
            kwargs={
                "start": start.isoformat(),
                "end": end.isoformat(),
                "aum": aum,
                "rebalance_days": int(rebalance_days),
            },
        )
        st.session_state["job_bt"] = run_id
        st.rerun()

run_id = st.session_state.get("job_bt")
if run_id is not None:
    s = get_status(run_id)
    badge = {"running": "🟡", "succeeded": "🟢", "failed": "🔴"}.get(s["status"], "⚪")
    st.markdown(f"{badge} run_id={s['run_id']} · {s['status']}")
    if s["status"] == "succeeded":
        _render_nav_chart(s["summary_json"])
    elif s["status"] == "failed":
        st.error(s["error"])


# --- History -----------------------------------------------------------------
st.markdown("---")
st.subheader("Run history")
try:
    with session_scope() as ss:
        history = ss.query(BacktestRun).order_by(BacktestRun.run_id.desc()).limit(50).all()
        history_data = [
            {
                "run_id": h.run_id,
                "started_at": h.started_at,
                "finished_at": h.finished_at,
                "params": str(h.params_json)[:80],
            }
            for h in history
        ]
        summaries_by_id = {h.run_id: h.summary_json for h in history}
except Exception as e:  # pragma: no cover — defensive
    history_data = []
    summaries_by_id = {}
    st.warning(f"history unavailable: {e}")

if history_data:
    st.dataframe(pd.DataFrame(history_data), use_container_width=True)
    selected = st.selectbox(
        "Select run to inspect", [h["run_id"] for h in history_data], key="bt_history_pick"
    )
    if selected:
        _render_nav_chart(summaries_by_id.get(selected))
else:
    st.info("No backtest runs yet.")
