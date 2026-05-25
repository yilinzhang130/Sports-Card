"""Forward-looking PRISM scoring for prospects who have not yet been drafted.

Reuses the trained ``prism_v1`` CatBoost ranker — no retrain — and replaces
``draft_pick`` (which doesn't yet exist for these prospects) with a mock-draft
**consensus rank** as the baseline market prior.

Asymmetry vs. ``score.py``
--------------------------
``score.py`` (historical, post-draft) uses the real ``draft_pick`` for BOTH
the model feature input AND the consensus baseline. We can do that because
the draft already happened.

``score_undrafted.py`` (forward, pre-draft) uses:

* ``UNDRAFTED_SENTINEL`` (= 61) as the ``draft_pick`` model input — there
  is no real pick yet, and we deliberately do not let mock-draft signal
  leak into the model side of the premium decomposition.
* Mock-draft consensus rank as the baseline (subtracted from the pairwise
  percentile).

This keeps the premium decomposition clean (consensus enters once, on the
baseline side only) at the cost of one feature slot to the model — a price
we pay to avoid the circularity of subtracting mock-consensus from a model
score that already saw mock-consensus.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sportscards.db.models import ProspectForecast
from sportscards.scouting.nba.features import UNDRAFTED_SENTINEL, build_feature_matrix
from sportscards.scouting.nba.ingest_bref import (
    CACHE_DIR,
    UNDERCLASSMEN,
    load_current_ncaa,
)
from sportscards.scouting.nba.mock_draft import (
    MOCK_DRAFT_DIR,
    aggregate_consensus_rank,
)
from sportscards.scouting.nba.prism import MODEL_PATH, MODEL_VERSION, load_model, predict_scores

logger = logging.getLogger(__name__)


def score_current_class(
    draft_year: int,
    season: str,
    as_of: date | None = None,
    *,
    cache_dir: Path = CACHE_DIR,
    mock_draft_dir: Path = MOCK_DRAFT_DIR,
    model_path: Path = MODEL_PATH,
    model: Any | None = None,
    session: Session | None = None,
) -> pd.DataFrame:
    """End-to-end forward-looking scoring pipeline.

    1. Load the current-season NCAA parquet for ``season``.
    2. Filter to prospects whose computed ``draft_year`` matches the argument.
    3. Inject ``draft_pick = UNDRAFTED_SENTINEL`` (see module docstring).
    4. Build the feature matrix (synthetic NaN outcomes for inference).
    5. Score with ``prism_v1`` (or the model passed in).
    6. Pull ``aggregate_consensus_rank`` for ``(draft_year, as_of)``.
    7. Outer-join on ``br_slug``; players with < 3 mock sources get NaN
       consensus → NaN premium (we don't pretend to have a view).
    8. premium = pairwise_pct - (1 - consensus_pct), both percentiles
       computed within the (draft_year, as_of) cohort.
    9. Persist to ``prospect_forecast`` (upsert on the composite PK) if a
       session is provided.

    Returns the scored DataFrame regardless of persistence.
    """
    as_of = as_of or date.today()

    raw = load_current_ncaa(season, cache_dir=cache_dir)
    cohort = raw[raw["draft_year"] == draft_year].reset_index(drop=True)
    if cohort.empty:
        logger.warning(
            "score_current_class: no prospects in cohort (draft_year=%d, season=%s)",
            draft_year,
            season,
        )
        return _empty_scored_frame()

    cohort = cohort.copy()
    cohort["draft_pick"] = UNDRAFTED_SENTINEL  # see asymmetry note in module docstring

    # build_feature_matrix needs an outcomes frame. Inference doesn't use y,
    # so synthesise one with NaN BPM — build_target fills NaN → 0 → clipped.
    fake_outcomes = pd.DataFrame({"br_slug": cohort["br_slug"], "career_bpm_5y": np.nan})

    X, _, _, slugs = build_feature_matrix(cohort, fake_outcomes)
    loaded_model = model if model is not None else load_model(path=model_path)
    pairwise = predict_scores(loaded_model, X)

    consensus = aggregate_consensus_rank(draft_year, as_of, cache_dir=mock_draft_dir)

    scored = pd.DataFrame(
        {
            "br_slug": slugs.values,
            "player_name": cohort["name"].values,
            "draft_year": draft_year,
            "pairwise_score": pairwise,
            "class_year": cohort["class_year"].values,
            "n_games_played": cohort["n_games_played"].values,
            "prior_league": cohort["prior_league"].values,
        }
    )
    scored = scored.merge(
        consensus[["br_slug", "consensus_rank", "sources_count"]],
        on="br_slug",
        how="left",
    )

    scored["is_underclassman"] = scored["class_year"].isin(UNDERCLASSMEN)
    scored["years_until_draft"] = scored["class_year"].apply(_years_until_draft)
    scored["pairwise_pct"] = scored["pairwise_score"].rank(pct=True, method="average")
    consensus_pct = scored["consensus_rank"].rank(pct=True, method="average")
    # Where consensus_rank is NaN → consensus_pct NaN → premium NaN. Intentional.
    scored["consensus_pct"] = consensus_pct
    scored["premium"] = scored["pairwise_pct"] - (1.0 - scored["consensus_pct"])
    scored["as_of_date"] = as_of

    if session is not None:
        persist_prospect_forecast(session, scored, model_version=MODEL_VERSION)

    return scored


def persist_prospect_forecast(
    session: Session,
    scored: pd.DataFrame,
    model_version: str = MODEL_VERSION,
) -> int:
    """Upsert the scored frame into ``prospect_forecast``.

    Upsert is implemented as delete-then-insert on the composite PK so the
    same logic works across SQLite (tests) and Postgres (prod). Snapshots
    with different ``as_of_date`` coexist by design.
    """
    if scored.empty:
        return 0
    now = datetime.now(UTC)

    keys = scored[["br_slug", "draft_year", "as_of_date"]].drop_duplicates()
    # Delete any prior row matching the composite PK for these (slug, year,
    # as_of_date, model_version). Loop to keep the SQL portable.
    for _, row in keys.iterrows():
        session.execute(
            delete(ProspectForecast).where(
                ProspectForecast.player_slug == str(row["br_slug"]),
                ProspectForecast.draft_year == int(row["draft_year"]),
                ProspectForecast.model_version == model_version,
                ProspectForecast.as_of_date == row["as_of_date"],
            )
        )

    n = 0
    for r in scored.itertuples(index=False):
        session.add(
            ProspectForecast(
                player_slug=str(r.br_slug),
                draft_year=int(float(str(r.draft_year))),
                model_version=model_version,
                as_of_date=r.as_of_date,
                name=str(r.player_name),
                premium=_maybe_decimal(r.premium, "0.0001"),
                pairwise_score=_maybe_decimal(r.pairwise_score, "0.0001"),
                consensus_rank=_maybe_decimal(r.consensus_rank, "0.01"),
                sources_count=_maybe_int(r.sources_count),
                is_underclassman=bool(r.is_underclassman),
                years_until_draft=int(float(str(r.years_until_draft))),
                prior_league=str(r.prior_league or "NCAA"),
                n_games_played=_maybe_int(r.n_games_played),
                fit_at=now,
            )
        )
        n += 1
    return n


def _empty_scored_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "br_slug",
            "player_name",
            "draft_year",
            "pairwise_score",
            "class_year",
            "n_games_played",
            "prior_league",
            "consensus_rank",
            "sources_count",
            "is_underclassman",
            "years_until_draft",
            "pairwise_pct",
            "consensus_pct",
            "premium",
            "as_of_date",
        ]
    )


def _maybe_decimal(value: Any, quant: str) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    return Decimal(str(float(value))).quantize(Decimal(quant))


def _maybe_int(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _years_until_draft(class_year: str) -> int:
    # Local import to avoid a circular header; mirrors the ingest mapping.
    from sportscards.scouting.nba.ingest_bref import CLASS_TO_YEARS_UNTIL_DRAFT

    return CLASS_TO_YEARS_UNTIL_DRAFT.get(str(class_year).upper(), 0)
