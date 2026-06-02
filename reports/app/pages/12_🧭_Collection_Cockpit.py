"""Collection Cockpit — Card Ladder capture operating queue."""

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


def _display_targets(df: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "tier",
        "next_action",
        "search_query",
        "rows",
        "target_rows",
        "remaining_rows",
        "coverage_pct",
        "needs_review_rows",
        "cadence",
        "latest_ingested_at",
        "cardladder_url",
    ]
    return df[[col for col in columns if col in df.columns]].copy()


st.set_page_config(page_title="Collection Cockpit", page_icon="🧭", layout="wide")
guard_localhost()
job_badge()

st.title("🧭 Collection Cockpit")
st.caption("Card Ladder target queue, coverage gaps, and identity-review pressure.")

try:
    targets = queries.collection_cockpit_targets(limit=200)
    identity_summary = queries.card_identity_review_summary()
except queries.TableMissing as e:
    st.info(f"Collection Cockpit unavailable yet — {e.phase} migration needed.")
    st.stop()
except Exception as e:  # pragma: no cover - defensive UI fallback
    st.error(f"Collection Cockpit unavailable: {e}")
    st.stop()

if targets.empty:
    st.info("No Card Ladder targets configured.")
    st.stop()

metric_cols = st.columns(5)
metric_cols[0].metric("targets", len(targets))
metric_cols[1].metric("rows", int(targets["rows"].sum()))
metric_cols[2].metric("remaining", int(targets["remaining_rows"].sum()))
metric_cols[3].metric("identity review", int(identity_summary["needs_review"]))
metric_cols[4].metric("rejected", int(identity_summary["rejected"]))

st.markdown("### Next capture batch")
top_n = st.slider("Batch size", min_value=3, max_value=25, value=10, step=1)
action_options = sorted(targets["next_action"].dropna().unique().tolist())
default_actions = [
    action for action in ["review_identity", "ingest_more"] if action in action_options
]
action_filter = st.multiselect(
    "Action",
    action_options,
    default=default_actions or action_options,
)
tier_filter = st.multiselect(
    "Tier",
    sorted(targets["tier"].dropna().unique().tolist()),
    default=sorted(targets["tier"].dropna().unique().tolist()),
)

filtered = targets[
    targets["next_action"].isin(action_filter) & targets["tier"].isin(tier_filter)
].copy()
batch = filtered.head(int(top_n))

if batch.empty:
    st.info("No targets match the selected filters.")
else:
    st.dataframe(
        _display_targets(batch),
        use_container_width=True,
        hide_index=True,
        column_config={
            "coverage_pct": st.column_config.ProgressColumn(
                "coverage",
                min_value=0,
                max_value=100,
                format="%.0f%%",
            ),
            "cardladder_url": st.column_config.LinkColumn("Card Ladder URL"),
        },
    )
    st.text_area(
        "Batch URLs",
        value="\n".join(batch["cardladder_url"].astype(str).tolist()),
        height=180,
    )
    st.text_area(
        "Search queries",
        value="\n".join(batch["search_query"].astype(str).tolist()),
        height=120,
    )

st.markdown("### Full target board")
st.dataframe(
    _display_targets(targets),
    use_container_width=True,
    hide_index=True,
    column_config={
        "coverage_pct": st.column_config.ProgressColumn(
            "coverage",
            min_value=0,
            max_value=100,
            format="%.0f%%",
        ),
        "cardladder_url": st.column_config.LinkColumn("Card Ladder URL"),
    },
)
