"""Shared UI widgets for the Trader Console."""

from __future__ import annotations

import streamlit as st

from reports.app._components.jobs import has_running_jobs


def confirm_toggle(key: str, label: str = "I understand this writes to the DB") -> bool:
    """Render a confirmation checkbox. Submit buttons should gate on this."""
    return st.checkbox(label, key=key, value=False)


def job_badge() -> None:
    """Render a sidebar badge if any background job is currently running."""
    try:
        if has_running_jobs():
            st.sidebar.warning("🟡 1 job running", icon="🟡")
    except Exception:
        # If the DB isn't reachable, don't crash the page header
        pass
