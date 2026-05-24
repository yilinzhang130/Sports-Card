"""Stardom-premium scoring + persistence.

The stardom premium is the value-add of the scouting module:

    premium = model_percentile_rank − draft_slot_implied_prior

Both terms are within-draft-class percentile-rank in [0, 1]. A *positive*
premium means the model is higher on the prospect than the market (the draft
slot) is; *negative* means it is lower. Zero means the model and the consensus
agree. Interpretation is intentionally market-relative so downstream hedonic
modelling can read it as "edge over the priced-in prior."
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerStardomScore
from sportscards.scouting.nba.prism import MODEL_VERSION


def compute_stardom_premium(
    br_slugs: pd.Series,
    draft_years: pd.Series,
    draft_picks: pd.Series,
    model_scores: np.ndarray,
) -> pd.DataFrame:
    """Within each draft class, percentile-rank the model score and subtract
    the draft-slot prior (lower pick ⇒ higher prior).
    """
    df = pd.DataFrame(
        {
            "br_slug": br_slugs.values,
            "draft_year": pd.to_numeric(draft_years, errors="coerce").astype("Int64"),
            "draft_pick": pd.to_numeric(draft_picks, errors="coerce"),
            "model_score": model_scores,
        }
    )
    df["draft_pick"] = df["draft_pick"].fillna(df["draft_pick"].max() or 60)

    df["percentile_rank"] = df.groupby("draft_year")["model_score"].rank(
        pct=True, method="average"
    )
    # Lower pick ⇒ higher prior. Use 1 - pct-rank of draft_pick.
    df["slot_prior"] = 1.0 - df.groupby("draft_year")["draft_pick"].rank(
        pct=True, method="average"
    )
    df["premium"] = df["percentile_rank"] - df["slot_prior"]
    return df[["br_slug", "draft_year", "premium", "percentile_rank"]]


def persist_scores(
    session: Session,
    scores: pd.DataFrame,
    model_version: str = MODEL_VERSION,
) -> int:
    """Upsert rows into ``player_stardom_score`` keyed by player_id.

    Maps ``br_slug`` → ``player_id`` via ``player_master``. Rows without a
    matching player are skipped (logged caller-side if desired).
    """
    slug_to_id = dict(
        session.query(Player.br_slug, Player.player_id).filter(
            Player.br_slug.in_(scores["br_slug"].tolist())
        )
    )
    now = datetime.now(timezone.utc)
    matched_player_ids = [
        slug_to_id[s] for s in scores["br_slug"] if s in slug_to_id
    ]
    if matched_player_ids:
        session.execute(
            delete(PlayerStardomScore).where(
                PlayerStardomScore.player_id.in_(matched_player_ids),
                PlayerStardomScore.model_version == model_version,
            )
        )

    n = 0
    for row in scores.itertuples(index=False):
        pid = slug_to_id.get(row.br_slug)
        if pid is None:
            continue
        session.add(
            PlayerStardomScore(
                player_id=pid,
                draft_year=int(row.draft_year),
                model_version=model_version,
                premium=Decimal(f"{row.premium:.4f}"),
                percentile_rank=Decimal(f"{row.percentile_rank:.4f}"),
                fit_at=now,
            )
        )
        n += 1
    return n
