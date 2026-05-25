"""Feature engineering for the PRISM scouting model.

Joins prospects with their 5-year NBA outcomes and produces the design matrix
used by ``prism.train_pairwise_model``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

POSITIONS = ["PG", "SG", "SF", "PF", "C"]
# BR uses single-letter shorthands ('G', 'F') for older draft classes. Map them
# to the dominant two-letter bucket so feature signal isn't silently lost.
POSITION_ALIASES: dict[str, str] = {"G": "SG", "F": "SF"}
UNDRAFTED_SENTINEL = 61.0  # one past the last NBA pick — bust prior

# Categorical values that ``prospect_origin`` can take. New rows from
# G-League / Euro ingestors get one of these tags; rows from the legacy
# NCAA-only cohort default to NCAA. Stored as one-hot columns so CatBoost
# treats them identically to position one-hots.
ORIGIN_CATEGORIES = [
    "NCAA",
    "GLEAGUE",
    "EUROLEAGUE",
    "LIGA_ACB",
    "LNB",
    "LBA",
    "NBL",
    "OTHER_INTL",
]
DEFAULT_ORIGIN = "NCAA"

NUMERIC_FEATURES = [
    "trb_pct",
    "ast_pct",
    "stl_pct",
    "blk_pct",
    "usg_pct",
    "ts_pct",
    "sos",
    "recruit_rank_pct",
    "age_at_draft",
    "draft_pick",
    "log_draft_pick",
    "mock_rank",
    "wingspan_in",
    "max_vert_in",
    "years_in_prior_league",
    "prior_league_mle_score",
    "prior_league_strength_rank",
]


def build_target(outcomes: pd.DataFrame) -> pd.Series:
    """5-year cumulative BPM, with negatives clipped to 0 per PRISM convention."""
    bpm = pd.to_numeric(outcomes["career_bpm_5y"], errors="coerce").fillna(0.0)
    return bpm.clip(lower=0.0).rename("target")


def build_feature_matrix(
    prospects: pd.DataFrame, outcomes: pd.DataFrame
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Return (X, y, draft_year_group, br_slug) aligned row-by-row.

    Prospects missing from ``outcomes`` are treated as zero-BPM career (DNP or
    bust) rather than dropped, so the cohort size matches the draft class.
    """
    # Outcomes is allowed to be missing a slug → treat as bust. Multiple rows
    # for the same slug would explode the cohort; collapse defensively.
    outcomes_unique = outcomes.drop_duplicates(subset="br_slug", keep="first")
    merged = prospects.merge(outcomes_unique, on="br_slug", how="left").reset_index(drop=True)
    merged["career_bpm_5y"] = pd.to_numeric(merged["career_bpm_5y"], errors="coerce").fillna(0.0)

    # Compute draft_pick *once*, with UNDRAFTED_SENTINEL imputation, and use
    # that single value to derive log_draft_pick and mock_rank. The earlier
    # version of this block overwrote draft_pick with 0.0 in the NUMERIC_
    # FEATURES loop AFTER deriving log/mock, producing internally
    # contradictory features.
    merged["draft_pick"] = pd.to_numeric(merged["draft_pick"], errors="coerce").fillna(
        UNDRAFTED_SENTINEL
    )
    merged["log_draft_pick"] = np.log1p(merged["draft_pick"])
    merged["mock_rank"] = merged["draft_pick"]

    # Backward-compat defaults for columns added by the unified cohort
    # builder. Old NCAA-only parquets won't have these — treat such rows as
    # NCAA prospects observed for one season with MLE = 1.0 (no scaling).
    if "prior_league_mle_score" not in merged.columns:
        merged["prior_league_mle_score"] = 1.0
    if "prior_league_strength_rank" not in merged.columns:
        merged["prior_league_strength_rank"] = 3
    if "years_in_prior_league" not in merged.columns:
        merged["years_in_prior_league"] = 1
    if "prospect_origin" not in merged.columns:
        merged["prospect_origin"] = DEFAULT_ORIGIN

    for col in NUMERIC_FEATURES:
        if col in {"draft_pick", "log_draft_pick", "mock_rank"}:
            continue  # already imputed above; do not re-overwrite
        if col not in merged.columns:
            merged[col] = 0.0
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)

    pos_raw = merged["position"].fillna("").astype(str).str.upper().str.strip()
    pos = pos_raw.replace(POSITION_ALIASES).str[:2]
    for p in POSITIONS:
        merged[f"pos_{p}"] = (pos == p).astype(int)

    origin = merged["prospect_origin"].fillna(DEFAULT_ORIGIN).astype(str).str.upper()
    for o in ORIGIN_CATEGORIES:
        merged[f"origin_{o}"] = (origin == o).astype(int)

    feature_cols = (
        NUMERIC_FEATURES
        + [f"pos_{p}" for p in POSITIONS]
        + [f"origin_{o}" for o in ORIGIN_CATEGORIES]
    )
    X = merged[feature_cols].astype(float)
    y = build_target(merged)
    return X, y, merged["draft_year"].astype(int), merged["br_slug"]
