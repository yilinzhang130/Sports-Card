"""Card Identity — review canonical card candidates."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reports.app._components.auth import guard_localhost
from reports.app._components.card_identity_actions import (
    approve_candidate,
    reject_candidate,
    update_candidate,
)
from reports.app._components.ui import job_badge
from sportscards.reports import queries


def _display_queue(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "needs_review",
        "confidence",
        "player_name",
        "year",
        "manufacturer",
        "set_name",
        "subset",
        "card_number",
        "parallel",
        "print_run",
        "slab_grader",
        "slab_grade",
        "canonical_key",
        "search_query",
        "raw_title",
        "raw_price",
        "sold_at",
    ]
    return df[[col for col in columns if col in df.columns]].copy()


def _text_value(row: pd.Series, key: str) -> str:
    value = row.get(key)
    return "" if pd.isna(value) else str(value)


def _int_value(row: pd.Series, key: str) -> int | None:
    value = row.get(key)
    if pd.isna(value):
        return None
    return int(value)


def _bool_value(row: pd.Series, key: str) -> bool:
    return bool(row.get(key))


def _decimal_or_none(value: str) -> Decimal | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return Decimal(stripped)
    except InvalidOperation:
        st.error("Grade must be numeric, for example 10 or 9.5.")
        return None


def _review_action_panel(queue: pd.DataFrame) -> None:
    st.subheader("Selected row actions")
    raw_ids = [int(value) for value in queue["raw_id"].tolist()]
    selected_raw_id = st.selectbox("raw_id", raw_ids, key="card_identity_selected_raw_id")
    row = queue.loc[queue["raw_id"] == selected_raw_id].iloc[0]

    st.caption(_text_value(row, "raw_title"))
    cols = st.columns(4)
    player_name = cols[0].text_input("player", value=_text_value(row, "player_name"))
    manufacturer = cols[1].text_input("manufacturer", value=_text_value(row, "manufacturer"))
    year = cols[2].number_input(
        "year",
        min_value=1900,
        max_value=2100,
        value=_int_value(row, "year") or 2026,
        step=1,
    )
    set_name = cols[3].text_input("set", value=_text_value(row, "set_name"))

    cols = st.columns(4)
    subset = cols[0].text_input("subset", value=_text_value(row, "subset"))
    card_number = cols[1].text_input("card number", value=_text_value(row, "card_number"))
    parallel = cols[2].text_input("parallel", value=_text_value(row, "parallel") or "Base")
    print_run_raw = cols[3].number_input(
        "print run",
        min_value=0,
        max_value=10000,
        value=_int_value(row, "print_run") or 0,
        step=1,
    )

    cols = st.columns(5)
    is_rookie = cols[0].checkbox("rookie", value=_bool_value(row, "is_rookie"))
    has_auto = cols[1].checkbox("auto", value=_bool_value(row, "has_auto"))
    has_patch = cols[2].checkbox("patch", value=_bool_value(row, "has_patch"))
    slab_grader = cols[3].text_input("grader", value=_text_value(row, "slab_grader") or "PSA")
    slab_grade = cols[4].text_input("grade", value=_text_value(row, "slab_grade"))

    reviewer_note = st.text_input("review note", value="")
    reject_reason = st.text_input("reject reason", value="not a single-card sale")
    action_cols = st.columns(3)
    if action_cols[0].button("Approve identity", key="card_identity_approve"):
        approve_candidate(int(selected_raw_id))
        st.success(f"approved raw_id={selected_raw_id}")
        st.rerun()
    if action_cols[1].button("Save manual override", key="card_identity_save_override"):
        grade = _decimal_or_none(slab_grade)
        if slab_grade.strip() and grade is None:
            st.stop()
        update_candidate(
            int(selected_raw_id),
            {
                "player_name": player_name,
                "manufacturer": manufacturer,
                "year": int(year),
                "set_name": set_name,
                "subset": subset,
                "card_number": card_number,
                "parallel": parallel or "Base",
                "print_run": int(print_run_raw) or None,
                "is_rookie": is_rookie,
                "has_auto": has_auto,
                "has_patch": has_patch,
                "slab_grader": slab_grader,
                "slab_grade": grade,
            },
            reviewer_note=reviewer_note or None,
        )
        st.success(f"updated raw_id={selected_raw_id}")
        st.rerun()
    if action_cols[2].button("Reject row", key="card_identity_reject"):
        reject_candidate(int(selected_raw_id), reason=reject_reason)
        st.success(f"rejected raw_id={selected_raw_id}")
        st.rerun()


st.set_page_config(page_title="Card Identity", page_icon="🧬", layout="wide")
guard_localhost()
job_badge()

st.title("🧬 Card Identity")
st.caption("Review parsed card identities before promoting sales into canonical card-level models.")

try:
    summary = queries.card_identity_review_summary()
except queries.TableMissing as e:
    st.info(f"Card Identity unavailable yet — {e.phase} migration needed.")
    st.stop()
except Exception as e:  # pragma: no cover - defensive UI fallback
    st.error(f"Card Identity unavailable: {e}")
    st.stop()

metric_cols = st.columns(5)
metric_cols[0].metric("candidates", summary["candidates"])
metric_cols[1].metric("distinct identities", summary["distinct_identities"])
metric_cols[2].metric("needs review", summary["needs_review"])
metric_cols[3].metric("high confidence", summary["high_confidence"])
metric_cols[4].metric("rejected", summary["rejected"])

tab_review, tab_rollup = st.tabs(["Review queue", "Identity rollup"])

with tab_review:
    col_a, col_b = st.columns([1, 1])
    mode = col_a.segmented_control(
        "Queue",
        ["Needs review", "High confidence", "All"],
        default="Needs review",
    )
    limit = col_b.number_input("Rows", min_value=25, max_value=500, value=200, step=25)
    needs_review_filter = {"Needs review": True, "High confidence": False}.get(mode)
    queue = queries.card_identity_review_queue(
        needs_review=needs_review_filter,
        limit=int(limit),
    )
    if queue.empty:
        st.info("No card identity candidates in this queue.")
    else:
        st.dataframe(
            _display_queue(queue),
            use_container_width=True,
            hide_index=True,
            column_config={
                "confidence": st.column_config.NumberColumn("confidence", format="%.3f"),
                "raw_price": st.column_config.NumberColumn("price", format="$%.2f"),
            },
        )
        _review_action_panel(queue)

with tab_rollup:
    rollup = queries.card_identity_key_rollup(limit=300)
    if rollup.empty:
        st.info("No canonical card identities yet.")
    else:
        st.dataframe(
            rollup,
            use_container_width=True,
            hide_index=True,
            column_config={
                "min_confidence": st.column_config.NumberColumn("min conf", format="%.3f"),
                "max_confidence": st.column_config.NumberColumn("max conf", format="%.3f"),
            },
        )
