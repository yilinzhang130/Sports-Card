"""Models — trigger fits and scoring from the UI; persist via model_run_log."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from reports.app._components import actions
from reports.app._components.auth import guard_localhost
from reports.app._components.jobs import get_status, submit_job
from reports.app._components.ui import confirm_toggle, job_badge
from sportscards.db.models import ModelRunLog
from sportscards.db.session import session_scope

st.set_page_config(page_title="Models", page_icon="🧪", layout="wide")
guard_localhost()
job_badge()
st.title("🧪 Models")
st.caption("Trigger fits and scoring jobs. Each run persists to model_run_log.")


def _render_status(slot_key: str) -> None:
    run_id = st.session_state.get(slot_key)
    if run_id is None:
        return
    s = get_status(run_id)
    badge = {"running": "🟡", "succeeded": "🟢", "failed": "🔴"}.get(s["status"], "⚪")
    st.markdown(f"{badge} **run_id={s['run_id']}** · {s['status']} · started {s['started_at']}")
    if s["status"] == "succeeded":
        st.json(s["summary_json"])
    elif s["status"] == "failed":
        st.error(s["error"])


# --- Fit hedonic -------------------------------------------------------------
with st.expander("Fit hedonic", expanded=True):
    with st.form("form_fit_hedonic"):
        train_end = st.date_input("Train end", value=date.today())
        synthetic = st.checkbox("Use synthetic data", value=False)
        ok = confirm_toggle("confirm_fit_hedonic")
        submitted = st.form_submit_button("Submit", disabled=not ok)
        if submitted:
            run_id = submit_job(
                "fit_hedonic",
                actions.fit_hedonic,
                params={"train_end": train_end.isoformat(), "synthetic": synthetic},
                kwargs={"train_end": train_end.isoformat(), "synthetic": synthetic},
            )
            st.session_state["job_fit_hedonic"] = run_id
            st.rerun()
    _render_status("job_fit_hedonic")


# --- Build repeat-sales index ------------------------------------------------
with st.expander("Build repeat-sales index"):
    with st.form("form_build_index"):
        bucket = st.selectbox("Bucket", ["weekly", "monthly"])
        grade_tiers_sel = st.multiselect(
            "Grade tiers",
            ["PSA10", "PSA9", "PSA8", "lower"],
            default=["PSA10", "PSA9", "PSA8", "lower"],
        )
        eras_sel = st.multiselect(
            "Eras",
            ["modern", "vintage"],
            default=["modern", "vintage"],
        )
        replace = st.checkbox("Replace existing rows", value=False)
        ok = confirm_toggle("confirm_build_index")
        submitted = st.form_submit_button("Submit", disabled=not ok)
        if submitted:
            run_id = submit_job(
                "build_index",
                actions.build_index,
                params={
                    "bucket": bucket,
                    "grade_tiers": grade_tiers_sel or None,
                    "eras": eras_sel or None,
                    "replace": replace,
                },
                kwargs={
                    "bucket": bucket,
                    "grade_tiers": grade_tiers_sel or None,
                    "eras": eras_sel or None,
                    "replace": replace,
                },
            )
            st.session_state["job_build_index"] = run_id
            st.rerun()
    _render_status("job_build_index")


# --- Compute factor panel ----------------------------------------------------
with st.expander("Compute factor panel"):
    with st.form("form_factor_panel"):
        as_of = st.date_input("As of", value=date.today(), key="factor_asof")
        ok = confirm_toggle("confirm_factor_panel")
        submitted = st.form_submit_button("Submit", disabled=not ok)
        if submitted:
            run_id = submit_job(
                "compute_factor_panel",
                actions.compute_factor_panel,
                params={"as_of": as_of.isoformat()},
                kwargs={"as_of": as_of.isoformat()},
            )
            st.session_state["job_factor_panel"] = run_id
            st.rerun()
    _render_status("job_factor_panel")


# --- Scouting fit ------------------------------------------------------------
with st.expander("Fit scouting (PRISM) model"):
    with st.form("form_scouting_fit"):
        fit_start = st.number_input("Start year", 2010, 2030, value=2010, key="scouting_fit_start")
        fit_end = st.number_input("End year", 2010, 2030, value=2024, key="scouting_fit_end")
        ok = confirm_toggle("confirm_scouting_fit")
        submitted = st.form_submit_button("Submit", disabled=not ok)
        if submitted:
            run_id = submit_job(
                "scouting_fit",
                actions.scouting_fit,
                params={"start": int(fit_start), "end": int(fit_end)},
                kwargs={"start": int(fit_start), "end": int(fit_end)},
            )
            st.session_state["job_scouting_fit"] = run_id
            st.rerun()
    _render_status("job_scouting_fit")


# --- Scouting score-class ----------------------------------------------------
with st.expander("Score scouting class"):
    with st.form("form_scouting_score"):
        draft_year = st.number_input("Draft year", 2020, 2030, value=date.today().year + 1)
        # Season label must match the NCAA parquet written by ingest-current-ncaa
        _dy = int(draft_year)
        _default_season = f"{_dy - 1}-{str(_dy)[-2:]}"
        season = st.text_input("Season label (e.g. 2025-26)", value=_default_season)
        as_of_score = st.date_input("As of (optional)", value=date.today(), key="score_asof")
        ok = confirm_toggle("confirm_scouting_score")
        submitted = st.form_submit_button("Submit", disabled=not ok)
        if submitted:
            run_id = submit_job(
                "scouting_score_class",
                actions.scouting_score_class,
                params={
                    "draft_year": int(draft_year),
                    "season": season,
                    "as_of": as_of_score.isoformat(),
                },
                kwargs={
                    "draft_year": int(draft_year),
                    "season": season,
                    "as_of": as_of_score.isoformat(),
                },
            )
            st.session_state["job_scouting_score"] = run_id
            st.rerun()
    _render_status("job_scouting_score")


# --- History -----------------------------------------------------------------
st.markdown("---")
st.subheader("Recent runs")
try:
    with session_scope() as s:
        rows = s.query(ModelRunLog).order_by(ModelRunLog.started_at.desc()).limit(50).all()
        if not rows:
            st.info("No runs yet.")
        else:
            df = pd.DataFrame(
                [
                    {
                        "run_id": r.run_id,
                        "action": r.action,
                        "status": r.status,
                        "started_at": r.started_at,
                        "finished_at": r.finished_at,
                        "error": (r.error or "")[:80],
                    }
                    for r in rows
                ]
            )
            st.dataframe(df, use_container_width=True)
except Exception as e:  # pragma: no cover — defensive
    st.warning(f"history unavailable: {e}")
