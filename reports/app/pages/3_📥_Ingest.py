"""Ingest — upload CSVs and trigger network fetches."""

from __future__ import annotations

import os
import tempfile

import streamlit as st

from reports.app._components import actions
from reports.app._components.auth import guard_localhost
from reports.app._components.jobs import get_status, submit_job
from reports.app._components.ui import confirm_toggle, job_badge

st.set_page_config(page_title="Ingest", page_icon="📥", layout="wide")
guard_localhost()
job_badge()
st.title("📥 Ingest")
st.caption("Upload sales CSVs and trigger network-bound ingest flows.")


def _render_status(slot_key: str) -> None:
    run_id = st.session_state.get(slot_key)
    if run_id is None:
        return
    s = get_status(run_id)
    badge = {"running": "🟡", "succeeded": "🟢", "failed": "🔴"}.get(s["status"], "⚪")
    st.markdown(f"{badge} run_id={s['run_id']} · {s['status']}")
    if s["status"] == "succeeded":
        st.json(s["summary_json"])
    elif s["status"] == "failed":
        st.error(s["error"])


# --- Card Ladder CSV upload --------------------------------------------------
with st.expander("Card Ladder CSV import", expanded=True):
    with st.form("form_cl"):
        uploaded = st.file_uploader("Card Ladder sales CSV", type=["csv"], key="cl_upload")
        ok = confirm_toggle("confirm_cl")
        submitted = st.form_submit_button("Import", disabled=not ok or uploaded is None)
        if submitted and uploaded is not None:
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp.write(uploaded.getvalue())
                path = tmp.name
            run_id = submit_job(
                "cardladder_import",
                actions.cardladder_import,
                params={"filename": uploaded.name},
                kwargs={"path": path},
            )
            st.session_state["job_cl"] = run_id
            st.rerun()
    _render_status("job_cl")


# --- Auction CSV upload ------------------------------------------------------
with st.expander("Auction-house CSV import"):
    with st.form("form_auction"):
        house = st.selectbox("House", ["goldin", "heritage", "fanatics_collect"])
        uploaded = st.file_uploader("Auction CSV", type=["csv"], key="auction_upload")
        ok = confirm_toggle("confirm_auction")
        submitted = st.form_submit_button("Import", disabled=not ok or uploaded is None)
        if submitted and uploaded is not None:
            with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
                tmp.write(uploaded.getvalue())
                path = tmp.name
            run_id = submit_job(
                "auction_import",
                actions.auction_import,
                params={"filename": uploaded.name, "house": house},
                kwargs={"path": path, "house": house},
            )
            st.session_state["job_auction"] = run_id
            st.rerun()
    _render_status("job_auction")


# --- eBay ingest -------------------------------------------------------------
with st.expander("eBay ingest"):
    have_creds = bool(os.getenv("EBAY_CLIENT_ID"))
    if not have_creds:
        st.warning("eBay credentials missing from .env (EBAY_CLIENT_ID) — button disabled.")
    with st.form("form_ebay"):
        keywords = st.text_input("Keywords", value="")
        max_pages = st.number_input("Max pages", 1, 50, 5)
        ok = confirm_toggle("confirm_ebay")
        submitted = st.form_submit_button(
            "Trigger ingest", disabled=not ok or not have_creds or not keywords
        )
        if submitted:
            run_id = submit_job(
                "ebay_ingest",
                actions.ebay_ingest,
                params={"keywords": keywords, "max_pages": int(max_pages)},
                kwargs={"keywords": keywords, "max_pages": int(max_pages)},
            )
            st.session_state["job_ebay"] = run_id
            st.rerun()
    _render_status("job_ebay")


# --- PSA pop snapshot --------------------------------------------------------
with st.expander("PSA pop snapshot"):
    with st.form("form_psa"):
        ok = confirm_toggle("confirm_psa")
        submitted = st.form_submit_button("Snapshot now", disabled=not ok)
        if submitted:
            run_id = submit_job("psa_pop", actions.daily_psa_pop, params={}, kwargs={})
            st.session_state["job_psa"] = run_id
            st.rerun()
    _render_status("job_psa")
