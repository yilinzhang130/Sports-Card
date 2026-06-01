"""Ingest — upload CSVs and trigger network fetches."""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pandas as pd
import streamlit as st

from reports.app._components import actions
from reports.app._components.auth import guard_localhost
from reports.app._components.jobs import get_status, submit_job
from reports.app._components.ui import confirm_toggle, job_badge
from sportscards.ingest.cardladder_manual import build_quick_sale, parse_cardladder_text

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


# --- Card Ladder manual import ----------------------------------------------
with st.expander("Card Ladder paste import"):
    search_query = st.text_input(
        "Search query",
        key="cardladder_search_query",
        placeholder="Example: Luka Doncic Prizm PSA 10",
    )
    pasted = st.text_area("Paste Card Ladder Sales History rows", height=220)
    if st.button("Parse Preview", disabled=not pasted.strip(), key="cardladder_parse_preview"):
        sales = parse_cardladder_text(pasted)
        st.session_state["cardladder_preview"] = [
            sale.with_metadata(search_query=search_query).to_dict() for sale in sales
        ]

    preview = st.session_state.get("cardladder_preview", [])
    if preview:
        st.dataframe(pd.DataFrame(preview), use_container_width=True)
        ok = confirm_toggle("confirm_cardladder_import")
        if st.button("Import confirmed rows", disabled=not ok, key="cardladder_import"):
            run_id = submit_job(
                "cardladder_manual_import",
                actions.cardladder_manual_import,
                params={"rows": len(preview), "search_query": search_query},
                kwargs={"rows": preview, "search_query": search_query},
            )
            st.session_state["job_cardladder"] = run_id
            st.rerun()
    elif pasted.strip():
        st.info("Paste rows, then click Parse Preview.")
    _render_status("job_cardladder")


with st.expander("Quick sale entry"):
    with st.form("form_cardladder_quick_sale"):
        platform = st.selectbox(
            "Platform",
            [
                "EBAY",
                "FANATICS",
                "FANATICS WEEKLY",
                "GOLDIN",
                "ALT",
                "CARD HOBBY",
                "HERITAGE",
                "MY SLABS",
            ],
        )
        title = st.text_input("Title")
        price = st.number_input("Price USD", min_value=0.01, value=100.0, step=1.0)
        sold_date = st.date_input("Sold date")
        listing_type = st.selectbox(
            "Listing type",
            ["Auction", "Best Offer", "Buy Now", "Fixed Price"],
        )
        verified = st.checkbox("Verified")
        ok = confirm_toggle("confirm_cardladder_quick")
        submitted = st.form_submit_button("Import sale", disabled=not ok or not title.strip())
        if submitted:
            sale = build_quick_sale(
                platform=platform,
                raw_title=title,
                price_usd=Decimal(str(price)),
                sold_date=sold_date,
                listing_type=listing_type,
                verified=verified,
            )
            run_id = submit_job(
                "cardladder_quick_sale",
                actions.cardladder_manual_import,
                params={"rows": 1},
                kwargs={"rows": [sale.to_dict()], "search_query": title},
            )
            st.session_state["job_cardladder_quick"] = run_id
            st.rerun()
    _render_status("job_cardladder_quick")


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
