"""Trader Console — landing page.

Localhost-only operator UI. The sportscards CLI remains the canonical surface;
this UI is a convenience. Use the sidebar to navigate.
"""

from __future__ import annotations

import streamlit as st

from reports.app._components.auth import guard_localhost
from reports.app._components.ui import job_badge
from sportscards.reports import queries

st.set_page_config(page_title="Trader Console", page_icon="🃏", layout="wide")
guard_localhost()
job_badge()

st.title("🃏 Trader Console")
st.caption("Localhost-only operator UI. The `sportscards` CLI remains canonical.")

st.markdown("### Data health")
try:
    summary = queries.data_health_summary()
    cols = st.columns(max(1, len(summary)))
    labels = {
        "raw_transactions": "raw transactions",
        "clean_transactions": "clean transactions",
        "cardladder_rows": "Card Ladder rows",
        "parse_failures": "parse failures",
    }
    for col, (name, value) in zip(cols, summary.items(), strict=False):
        col.metric(labels.get(name, name), value)
except queries.TableMissing as e:
    st.info(f"Data health unavailable yet — {e.phase} migration needed.")
except Exception as e:  # pragma: no cover — defensive
    st.info(f"Data health unavailable: {e}")

try:
    coverage = queries.cardladder_coverage_summary()
    if not coverage.empty:
        st.markdown("### Card Ladder coverage")
        st.dataframe(coverage, use_container_width=True, hide_index=True)
except queries.TableMissing:
    pass
except Exception as e:  # pragma: no cover — defensive
    st.info(f"Card Ladder coverage unavailable: {e}")

st.markdown("---")
st.markdown(
    "**Use the sidebar to navigate.** The CLI is the canonical operator surface — "
    "this UI is a convenience that exposes the same workflows behind forms."
)
