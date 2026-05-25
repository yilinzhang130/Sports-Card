"""Feature engineering for the PRISM scouting model.

Joins prospects with their 5-year NBA outcomes and produces the design matrix
used by ``prism.train_pairwise_model``. Optionally augments the matrix with
NBA Draft Combine measurements; see ``merge_combine`` for the join semantics.
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

# Engineered combine features. Order matters only for column-stable output.
# Pairs are (feature_name, source_column_or_derivation). Imputation produces
# a sibling ``combine_<feature>_imputed`` boolean column for each.
COMBINE_FEATURES = [
    "wingspan_minus_height",  # wingspan - height_with_shoes; ape-index
    "bmi",                    # 703 * weight_lb / height_in^2
    "standing_reach",
    "max_vertical",
    "lane_agility_time",
    "three_quarter_sprint",
]


def merge_combine(prospects: pd.DataFrame, combine: pd.DataFrame) -> pd.DataFrame:
    """Left-join combine measurements onto a prospects frame on ``br_slug``.

    Adds the derived columns from ``COMBINE_FEATURES``, a ``has_combine_data``
    flag, and per-feature ``combine_<name>_imputed`` flags. Missing values
    are filled with the per-feature cohort median so the model sees a
    well-defined matrix; the imputed flag lets the booster learn to
    discount those rows.

    Roughly 30-40% of prospects historically skip the combine; designing
    around the missing-data case is not optional.
    """
    if combine is None or combine.empty:
        merged = prospects.copy()
        merged["has_combine_data"] = 0
        for feat in COMBINE_FEATURES:
            merged[feat] = 0.0
            merged[f"combine_{feat}_imputed"] = 1
        return merged

    combine = combine.drop_duplicates(subset="br_slug", keep="first").copy()

    # Derive features that don't exist as raw columns. Use barefoot height
    # for the ape-index numerator (wingspan is also measured barefoot, and
    # post-2022 NBA combines have stopped publishing height_with_shoes for
    # many players). Fall back to height_with_shoes if no-shoes is missing.
    wingspan = pd.to_numeric(combine.get("wingspan"), errors="coerce")
    height_ns = pd.to_numeric(combine.get("height_no_shoes"), errors="coerce")
    height_ws = pd.to_numeric(combine.get("height_with_shoes"), errors="coerce")
    height = height_ns.fillna(height_ws)
    weight = pd.to_numeric(combine.get("weight"), errors="coerce")
    combine["wingspan_minus_height"] = wingspan - height
    # Standard BMI formula in imperial units: 703 * lb / in^2.
    with np.errstate(divide="ignore", invalid="ignore"):
        combine["bmi"] = 703.0 * weight / (height ** 2)
    for col in ("standing_reach", "max_vertical", "lane_agility_time", "three_quarter_sprint"):
        combine[col] = pd.to_numeric(combine.get(col), errors="coerce")

    join_cols = ["br_slug"] + COMBINE_FEATURES
    merged = prospects.merge(combine[join_cols], on="br_slug", how="left")

    # has_combine_data: 1 if ANY combine column came back non-null.
    raw_present = merged[COMBINE_FEATURES].notna().any(axis=1)
    merged["has_combine_data"] = raw_present.astype(int)

    # Per-feature median imputation; cohort median is computed on observed rows.
    for feat in COMBINE_FEATURES:
        observed = pd.to_numeric(merged[feat], errors="coerce")
        median = float(observed.median()) if observed.notna().any() else 0.0
        merged[f"combine_{feat}_imputed"] = observed.isna().astype(int)
        merged[feat] = observed.fillna(median)

    return merged


def build_target(outcomes: pd.DataFrame) -> pd.Series:
    """5-year cumulative BPM, with negatives clipped to 0 per PRISM convention."""
    bpm = pd.to_numeric(outcomes["career_bpm_5y"], errors="coerce").fillna(0.0)
    return bpm.clip(lower=0.0).rename("target")


def build_feature_matrix(
    prospects: pd.DataFrame,
    outcomes: pd.DataFrame,
    combine: pd.DataFrame | None = None,
    use_combine: bool = True,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Return (X, y, draft_year_group, br_slug) aligned row-by-row.

    Prospects missing from ``outcomes`` are treated as zero-BPM career (DNP or
    bust) rather than dropped, so the cohort size matches the draft class.

    If ``use_combine`` and ``combine`` is non-empty, NBA Draft Combine
    measurements (wingspan, vertical, lane agility, etc.) are appended to
    the feature matrix via :func:`merge_combine`. The pairwise CatBoost
    ranker treats the imputation flags as ordinary features.
    """
    outcomes_unique = outcomes.drop_duplicates(subset="br_slug", keep="first")
    merged = prospects.merge(outcomes_unique, on="br_slug", how="left").reset_index(drop=True)
    merged["career_bpm_5y"] = pd.to_numeric(merged["career_bpm_5y"], errors="coerce").fillna(0.0)

    # Compute draft_pick once, with UNDRAFTED_SENTINEL imputation, and use
    # that single value to derive log_draft_pick and mock_rank.
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
            continue
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

    if use_combine and combine is not None and not combine.empty:
        merged = merge_combine(merged, combine)
        combine_cols = (
            COMBINE_FEATURES
            + [f"combine_{f}_imputed" for f in COMBINE_FEATURES]
            + ["has_combine_data"]
        )
        feature_cols = feature_cols + combine_cols

    X = merged[feature_cols].astype(float)
    y = build_target(merged)
    return X, y, merged["draft_year"].astype(int), merged["br_slug"]
