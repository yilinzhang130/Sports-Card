"""Job-runnable action wrappers — thin shims around existing CLI handlers.

Each function MUST:
  * import heavy modules lazily,
  * accept only keyword args,
  * return a JSON-serializable dict summary,
  * raise on failure (the job runner records the traceback).
"""
from __future__ import annotations

from typing import Any


def fit_hedonic(*, train_end: str | None = None, synthetic: bool = False) -> dict[str, Any]:
    """Mirror the orchestration from ``model fit-hedonic`` in cli/__main__.py.

    1. (if synthetic) seed synthetic data
    2. build_features
    3. fit
    4. save_model
    5. predict + persist_residuals
    """
    from datetime import date as _date

    from sportscards.db.session import session_scope
    from sportscards.factors.features import build_features
    from sportscards.factors.hedonic import fit, persist_residuals, predict, save_model
    from sportscards.factors.synthetic_data import generate_synthetic_transactions

    synthetic_n: int | None = None
    with session_scope() as s:
        if synthetic:
            synthetic_n = generate_synthetic_transactions(s)
        df = build_features(s)
        if df.empty:
            return {"error": "no feature rows — aborting", "n_rows": 0}
        te = _date.fromisoformat(train_end) if train_end else None
        model_obj, encoder, metrics = fit(df, train_end=te)
        save_model(model_obj, encoder, metrics)
        pred = predict(model_obj, encoder, df)
        written = persist_residuals(s, df, pred)

    summary: dict[str, Any] = {
        "n_features": len(df),
        "residuals_written": written,
        **{k: float(v) if isinstance(v, float) else v for k, v in metrics.items()},
    }
    if synthetic_n is not None:
        summary["synthetic_tx_generated"] = synthetic_n
    return summary


def build_index(
    *,
    bucket: str = "weekly",
    grade_tiers: list[str] | None = None,
    eras: list[str] | None = None,
    sport: str = "NBA",
    replace: bool = False,
) -> dict[str, Any]:
    """Mirror the orchestration from ``index build`` in cli/__main__.py."""
    from sportscards.factors.index_build import build_and_persist

    effective_tiers = grade_tiers or ["PSA10", "PSA9", "PSA8", "lower"]
    effective_eras = eras or ["modern", "vintage"]
    stats = build_and_persist(
        sport=sport,
        bucket=bucket,
        grade_tiers=effective_tiers,
        eras=effective_eras,
        replace=replace,
    )
    return {k: int(v) for k, v in stats.items()}


def compute_factor_panel(*, as_of: str | None = None) -> dict[str, Any]:
    """Mirror the orchestration from ``factor compute-panel`` in cli/__main__.py."""
    from datetime import UTC
    from datetime import date as _date
    from datetime import datetime as _datetime

    from sportscards.db.session import session_scope
    from sportscards.factors.factor_panel import persist_panel

    if as_of is None:
        as_of_dt: _datetime = _datetime.now(tz=UTC)
    else:
        as_of_dt = _datetime.combine(_date.fromisoformat(as_of), _datetime.min.time())

    with session_scope() as s:
        n = persist_panel(s, as_of_dt)

    return {"rows_persisted": n, "as_of": as_of_dt.strftime("%Y-%m-%d")}


def scouting_fit(*, start: int = 2010, end: int = 2024) -> dict[str, Any]:
    """Mirror the orchestration from ``scouting fit`` in cli/__main__.py."""
    from sportscards.scouting.nba.features import build_feature_matrix
    from sportscards.scouting.nba.ingest_combine import load_combine_cohort
    from sportscards.scouting.nba.ingest_prospects import build_unified_cohort
    from sportscards.scouting.nba.prism import (
        concordance,
        predict_scores,
        save_model,
        train_pairwise_model,
    )

    years = range(start, end + 1)
    prospects, outcomes = build_unified_cohort(years)
    combine = load_combine_cohort(years)
    X, y, groups, _ = build_feature_matrix(prospects, outcomes, combine=combine)
    model = train_pairwise_model(X, y, groups)
    save_model(model)
    c = concordance(predict_scores(model, X), y.to_numpy(), groups.to_numpy())
    return {
        "trained": True,
        "start_year": start,
        "end_year": end,
        "in_sample_concordance": float(c),
        "combine_rows": len(combine),
    }


def scouting_score_class(
    *,
    draft_year: int,
    season: str,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Mirror the orchestration from ``scouting score-class`` in cli/__main__.py."""
    from datetime import date as _date

    from sportscards.db.session import session_scope
    from sportscards.scouting.nba.score_undrafted import score_current_class

    as_of_date = _date.fromisoformat(as_of) if as_of else None
    with session_scope() as s:
        df = score_current_class(
            draft_year=draft_year,
            season=season,
            as_of=as_of_date,
            session=s,
        )

    return {
        "scored": True,
        "draft_year": draft_year,
        "season": season,
        "n_prospects": len(df),
        "n_with_mock_consensus": int(df["premium"].notna().sum()) if "premium" in df.columns else 0,
    }
