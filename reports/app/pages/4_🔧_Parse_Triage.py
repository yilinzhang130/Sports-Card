"""Parse Triage — review parse_failures and apply manual corrections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd
import streamlit as st

from reports.app._components import actions
from reports.app._components.auth import guard_localhost
from reports.app._components.jobs import get_status, submit_job
from reports.app._components.ui import confirm_toggle, job_badge
from reports.app.pages._parse_triage_helpers import apply_manual_correction
from sportscards.db.models import ParseFailure, TxRaw
from sportscards.db.session import session_scope

st.set_page_config(page_title="Parse Triage", page_icon="🔧", layout="wide")
guard_localhost()
job_badge()
st.title("🔧 Parse Triage")
st.caption("Review unparsed transactions and apply manual corrections.")


# --- Load failures -----------------------------------------------------------
try:
    with session_scope() as s:
        rows = (
            s.query(ParseFailure, TxRaw)
            .join(TxRaw, TxRaw.raw_id == ParseFailure.raw_id)
            .order_by(ParseFailure.attempted_at.desc())
            .limit(200)
            .all()
        )
        failure_data = [
            {
                "raw_id": pf.raw_id,
                "reason": pf.reason,
                "source": tx.source,
                "raw_title": tx.raw_title,
                "raw_price": float(tx.raw_price) if tx.raw_price is not None else None,
                "sold_at": tx.sold_at,
            }
            for pf, tx in rows
        ]
except Exception as e:  # pragma: no cover — defensive
    failure_data = []
    st.warning(f"could not load failures: {e}")

if not failure_data:
    st.info("No parse failures to triage.")
else:
    st.caption(f"{len(failure_data)} failures (most recent 200)")
    st.dataframe(pd.DataFrame(failure_data), use_container_width=True)


# --- Manual correction -------------------------------------------------------
st.markdown("### Apply manual correction")
with st.form("manual_form"):
    raw_id = st.number_input("raw_id", min_value=1, step=1)
    card_id = st.number_input("card_id", min_value=1, step=1)
    slab_grader = st.text_input("Slab grader (e.g. PSA)", value="PSA")
    slab_grade = st.number_input("Slab grade", 0.0, 10.0, 10.0, 0.5)
    price_usd = st.number_input("Price USD", 0.0, step=1.0)
    sold_at = st.date_input("Sold at", value=datetime.utcnow().date())
    ok = confirm_toggle("confirm_manual")
    submitted = st.form_submit_button("Apply correction", disabled=not ok)
    if submitted:
        try:
            apply_manual_correction(
                int(raw_id),
                card_id=int(card_id),
                slab_grader=slab_grader or None,
                slab_grade=Decimal(str(slab_grade)),
                price_usd=Decimal(str(price_usd)),
                sold_at=datetime.combine(sold_at, datetime.min.time()),
            )
            st.success(f"correction applied for raw_id={raw_id}")
            st.rerun()
        except KeyError as e:
            st.error(str(e))


# --- Re-run LLM on batch -----------------------------------------------------
st.markdown("### Re-run parser with LLM")
with st.form("llm_form"):
    batch = st.number_input("Batch size", 10, 5000, 1000, 10)
    ok = confirm_toggle("confirm_llm")
    submitted = st.form_submit_button("Re-run LLM", disabled=not ok)
    if submitted:
        run_id = submit_job(
            "parse_llm_rerun",
            actions.parse_pending,
            params={"batch_size": int(batch), "allow_llm": True},
            kwargs={"batch_size": int(batch), "allow_llm": True},
        )
        st.session_state["job_llm"] = run_id
        st.rerun()

run_id = st.session_state.get("job_llm")
if run_id is not None:
    s = get_status(run_id)
    badge = {"running": "🟡", "succeeded": "🟢", "failed": "🔴"}.get(s["status"], "⚪")
    st.markdown(f"{badge} run_id={s['run_id']} · {s['status']}")
    if s["status"] == "succeeded":
        st.json(s["summary_json"])
    elif s["status"] == "failed":
        st.error(s["error"])
