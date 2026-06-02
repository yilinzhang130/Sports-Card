"""Card Identity — review canonical card candidates."""

# ruff: noqa: E402

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reports.app._components.auth import guard_localhost
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

metric_cols = st.columns(4)
metric_cols[0].metric("candidates", summary["candidates"])
metric_cols[1].metric("distinct identities", summary["distinct_identities"])
metric_cols[2].metric("needs review", summary["needs_review"])
metric_cols[3].metric("high confidence", summary["high_confidence"])

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
