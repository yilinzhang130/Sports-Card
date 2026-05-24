"""Feature engineering for the PRISM scouting model.

Joins prospects with their 5-year NBA outcomes and produces the design matrix
used by ``prism.train_pairwise_model``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

POSITIONS = ["PG", "SG", "SF", "PF", "C"]

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
    merged = prospects.merge(outcomes, on="br_slug", how="left")
    merged["career_bpm_5y"] = pd.to_numeric(
        merged["career_bpm_5y"], errors="coerce"
    ).fillna(0.0)
    merged["draft_pick"] = pd.to_numeric(merged["draft_pick"], errors="coerce")
    merged["log_draft_pick"] = np.log1p(merged["draft_pick"].fillna(60.0))
    merged["mock_rank"] = merged["draft_pick"].fillna(60.0)

    for col in NUMERIC_FEATURES:
        if col not in merged.columns:
            merged[col] = 0.0
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0.0)

    pos = merged["position"].fillna("").astype(str).str.upper().str[:2]
    for p in POSITIONS:
        merged[f"pos_{p}"] = (pos == p).astype(int)

    feature_cols = NUMERIC_FEATURES + [f"pos_{p}" for p in POSITIONS]
    X = merged[feature_cols].astype(float)
    y = build_target(merged)
    return X, y, merged["draft_year"].astype(int), merged["br_slug"]
