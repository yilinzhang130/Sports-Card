"""Player Radar — Card Ladder queue and player-selection surface."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from reports.app._components.auth import guard_localhost
from reports.app._components.ui import job_badge
from sportscards.ingest.cardladder_queue import next_searches
from sportscards.reports import queries

st.set_page_config(page_title="Player Radar", page_icon="🎯", layout="wide")
guard_localhost()
job_badge()

st.title("🎯 Player Radar")
st.caption("Card Ladder-driven NBA watchlist, coverage queue, and early player-selection scores.")


def _coverage_dict(radar: pd.DataFrame) -> dict[str, int]:
    if radar.empty:
        return {}
    return {str(row.search_query): int(row.rows) for row in radar.itertuples(index=False)}


try:
    radar = queries.cardladder_player_radar()
except queries.TableMissing as e:
    st.info(f"Player Radar unavailable yet — {e.phase} migration needed.")
    st.stop()
except Exception as e:  # pragma: no cover - defensive UI fallback
    st.error(f"Player Radar unavailable: {e}")
    st.stop()

if radar.empty:
    st.info("No Card Ladder queue configured yet.")
    st.stop()

metric_cols = st.columns(4)
metric_cols[0].metric("tracked searches", len(radar))
metric_cols[1].metric("Card Ladder rows", int(radar["rows"].sum()))
metric_cols[2].metric("needs ingest", int((radar["next_action"] == "ingest_more").sum()))
metric_cols[3].metric("parse failures", queries.data_health_summary().get("parse_failures", 0))

coverage = _coverage_dict(radar)
next_rows = next_searches(coverage, limit=12)
if next_rows:
    st.markdown("### Next Card Ladder searches")
    st.dataframe(
        pd.DataFrame([row.__dict__ for row in next_rows]),
        use_container_width=True,
        hide_index=True,
    )

st.markdown("### Radar leaderboard")
tier_filter = st.multiselect(
    "Tier",
    sorted(radar["tier"].dropna().unique().tolist()),
    default=sorted(radar["tier"].dropna().unique().tolist()),
)
action_filter = st.multiselect(
    "Action",
    sorted(radar["next_action"].dropna().unique().tolist()),
    default=sorted(radar["next_action"].dropna().unique().tolist()),
)
filtered = radar[
    radar["tier"].isin(tier_filter) & radar["next_action"].isin(action_filter)
].copy()

display = filtered[
    [
        "tier",
        "search_query",
        "radar_score",
        "rows",
        "target_rows",
        "coverage_pct",
        "median_price",
        "high_sale",
        "premium_sale_pct",
        "price_volatility",
        "next_action",
    ]
]
st.dataframe(
    display,
    use_container_width=True,
    hide_index=True,
    column_config={
        "coverage_pct": st.column_config.ProgressColumn(
            "coverage",
            min_value=0,
            max_value=100,
            format="%.0f%%",
        ),
        "median_price": st.column_config.NumberColumn("median", format="$%.0f"),
        "high_sale": st.column_config.NumberColumn("high", format="$%.0f"),
        "premium_sale_pct": st.column_config.NumberColumn("premium sales", format="%.0%%"),
        "price_volatility": st.column_config.NumberColumn("volatility", format="%.2f"),
    },
)
