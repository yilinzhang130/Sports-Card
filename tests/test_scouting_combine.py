"""Tests for the NBA combine ingestor + combine-augmented PRISM features.

All tests run offline via ``FakeCombineClient``. Synthetic prospects with
extreme wingspans must rank in the top decile after re-training; missing
combine rows must be cohort-median-imputed and flagged.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sportscards.scouting.nba import features as feat
from sportscards.scouting.nba import ingest_combine, prism

# ---------------------------------------------------------------------------
# Synthetic combine cohort
# ---------------------------------------------------------------------------
# 12 prospects: 5 "freaks" with extreme wingspan + vertical and high true_bpm,
# 4 average bodies with average BPM, 3 with NO combine data at all (test
# imputation). Slugs are stable so we can target rows directly.
FREAKS = [
    # slug, name, height_ws, wingspan, weight, reach, max_vert, lane_agility,
    # 3/4_sprint, true_bpm
    ("freak1", "Freak One",   84.0, 92.0, 250, 112.0, 40.0, 10.5, 3.10, 30.0),
    ("freak2", "Freak Two",   82.0, 90.0, 230, 110.0, 41.0, 10.3, 3.05, 28.0),
    ("freak3", "Freak Three", 81.0, 89.0, 220, 109.0, 42.0, 10.4, 3.00, 26.0),
    ("freak4", "Freak Four",  80.0, 88.0, 215, 107.0, 39.0, 10.6, 3.15, 25.0),
    ("freak5", "Freak Five",  83.0, 91.0, 240, 111.0, 38.0, 10.7, 3.20, 24.0),
]
AVERAGES = [
    ("avg1", "Avg One",   78.0, 79.0, 210, 104.0, 33.0, 11.5, 3.30, 8.0),
    ("avg2", "Avg Two",   77.0, 78.0, 200, 103.0, 32.0, 11.4, 3.28, 7.0),
    ("avg3", "Avg Three", 79.0, 80.0, 215, 105.0, 34.0, 11.3, 3.27, 9.0),
    ("avg4", "Avg Four",  76.0, 77.0, 195, 102.0, 31.0, 11.6, 3.32, 6.0),
]
NO_COMBINE = [
    # only slug/name/true_bpm; no combine row at all
    ("nocomb1", "No Combine One",   5.0),
    ("nocomb2", "No Combine Two",   4.0),
    ("nocomb3", "No Combine Three", 3.0),
]


def _prospect_row(slug: str, name: str, draft_pick: int, true_bpm: float) -> dict:
    # NCAA features are deliberately *uninformative* (all identical) so the
    # combine block carries the signal in the recovery test.
    return {
        "br_slug": slug,
        "name": name,
        "draft_year": 2018,
        "draft_pick": draft_pick,
        "position": "SF",
        "age_at_draft": 20.0,
        "usg_pct": 22.0,
        "ts_pct": 0.55,
        "trb_pct": 10.0,
        "ast_pct": 10.0,
        "stl_pct": 1.0,
        "blk_pct": 1.0,
        "sos": 6.0,
        "recruit_rank_pct": 0.80,
        "wingspan_in": 0.0,  # zero out the legacy column so signal flows through combine only
        "max_vert_in": 0.0,
    }


@pytest.fixture
def synthetic_with_combine() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    prospects_rows = []
    outcomes_rows = []
    combine_rows = []

    pick = 1
    for r in FREAKS + AVERAGES:
        slug, name = r[0], r[1]
        true_bpm = r[-1]
        prospects_rows.append(_prospect_row(slug, name, pick, true_bpm))
        outcomes_rows.append(
            {"br_slug": slug, "career_bpm_5y": true_bpm, "career_ws_5y": 0.0, "career_vorp_5y": 0.0}
        )
        combine_rows.append(
            {
                "br_slug": slug,
                "name": name,
                "draft_year": 2018,
                "height_no_shoes": r[2] - 1.0,
                "height_with_shoes": r[2],
                "weight": r[4],
                "wingspan": r[3],
                "standing_reach": r[5],
                "body_fat_pct": 6.0,
                "hand_length": 9.0,
                "hand_width": 10.0,
                "standing_vertical": r[6] - 3.0,
                "max_vertical": r[6],
                "lane_agility_time": r[7],
                "three_quarter_sprint": r[8],
                "bench_press": 12,
            }
        )
        pick += 1
    for slug, name, true_bpm in NO_COMBINE:
        prospects_rows.append(_prospect_row(slug, name, pick, true_bpm))
        outcomes_rows.append(
            {"br_slug": slug, "career_bpm_5y": true_bpm, "career_ws_5y": 0.0, "career_vorp_5y": 0.0}
        )
        pick += 1

    return (
        pd.DataFrame(prospects_rows),
        pd.DataFrame(outcomes_rows),
        pd.DataFrame(combine_rows),
    )


class FakeCombineClient:
    """Drop-in replacement for ``CombineClient`` — guarantees no network."""

    def __init__(self, combine: pd.DataFrame) -> None:
        self._combine = combine

    def get_combine(self, year: int) -> pd.DataFrame:
        return self._combine[self._combine["draft_year"] == year].copy()


# ---------------------------------------------------------------------------
# Ingestor tests
# ---------------------------------------------------------------------------
def test_ingest_combine_writes_parquet(tmp_path: Path, synthetic_with_combine) -> None:
    _, _, combine = synthetic_with_combine
    client = FakeCombineClient(combine)
    out = ingest_combine.ingest_year(2018, client=client, cache_dir=tmp_path)
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(ingest_combine.COMBINE_COLUMNS).issubset(df.columns)
    assert len(df) == len(combine)


def test_load_combine_cohort_skips_missing_years(tmp_path: Path, synthetic_with_combine) -> None:
    _, _, combine = synthetic_with_combine
    client = FakeCombineClient(combine)
    ingest_combine.ingest_year(2018, client=client, cache_dir=tmp_path)
    df = ingest_combine.load_combine_cohort([2017, 2018, 2019], cache_dir=tmp_path)
    assert len(df) == len(combine)
    # 2017 and 2019 are silently skipped — no exception.


def test_parse_height_inches_handles_strings_and_numbers() -> None:
    assert ingest_combine._parse_height_inches("6-7") == pytest.approx(79.0)
    assert ingest_combine._parse_height_inches("6-7.25") == pytest.approx(79.25)
    assert ingest_combine._parse_height_inches(79.0) == pytest.approx(79.0)
    assert np.isnan(ingest_combine._parse_height_inches("--"))
    assert np.isnan(ingest_combine._parse_height_inches(None))


# ---------------------------------------------------------------------------
# merge_combine tests
# ---------------------------------------------------------------------------
def test_merge_combine_imputes_missing_and_flags(synthetic_with_combine) -> None:
    prospects, _, combine = synthetic_with_combine
    merged = feat.merge_combine(prospects, combine)

    # Every prospect row preserved.
    assert len(merged) == len(prospects)

    # Combine-present rows have flag=1, no-combine rows have flag=0.
    present = merged.set_index("br_slug")["has_combine_data"]
    assert present.loc["freak1"] == 1
    assert present.loc["avg1"] == 1
    assert present.loc["nocomb1"] == 0

    # Per-feature imputation flags are set for the no-combine prospects.
    for feature in feat.COMBINE_FEATURES:
        flag_col = f"combine_{feature}_imputed"
        assert merged.set_index("br_slug").loc["nocomb1", flag_col] == 1
        assert merged.set_index("br_slug").loc["freak1", flag_col] == 0

    # Imputed values are finite (not NaN) — booster needs a defined matrix.
    assert merged[feat.COMBINE_FEATURES].notna().all().all()

    # The imputed value is the cohort median of observed rows. Confirm one
    # feature explicitly to guard against accidental zero-fill. Note the
    # ape-index formula uses height_no_shoes (matches features.merge_combine).
    observed_wingspan_minus = (
        combine["wingspan"].astype(float) - combine["height_no_shoes"].astype(float)
    )
    expected_median = float(observed_wingspan_minus.median())
    nocomb_value = float(
        merged.set_index("br_slug").loc["nocomb1", "wingspan_minus_height"]
    )
    assert nocomb_value == pytest.approx(expected_median)


def test_merge_combine_with_empty_frame_safe(synthetic_with_combine) -> None:
    prospects, _, _ = synthetic_with_combine
    merged = feat.merge_combine(prospects, pd.DataFrame())
    assert (merged["has_combine_data"] == 0).all()
    for feature in feat.COMBINE_FEATURES:
        assert (merged[f"combine_{feature}_imputed"] == 1).all()


# ---------------------------------------------------------------------------
# End-to-end: extreme-wingspan freaks must rank in the top decile
# ---------------------------------------------------------------------------
def test_combine_freaks_rank_top_after_training(synthetic_with_combine) -> None:
    prospects, outcomes, combine = synthetic_with_combine
    X, y, groups, slugs = feat.build_feature_matrix(
        prospects, outcomes, combine=combine, use_combine=True
    )
    # Sanity: combine features should be present in the matrix.
    for feature in feat.COMBINE_FEATURES:
        assert feature in X.columns
    assert "has_combine_data" in X.columns

    model = prism.train_pairwise_model(X, y, groups, iterations=400)
    scores = prism.predict_scores(model, X)

    ranked = pd.DataFrame({"br_slug": slugs.to_numpy(), "score": scores}).sort_values(
        "score", ascending=False
    )
    # Top 5 by predicted score should be dominated by the 5 freaks (allow 1
    # slack slot for stochastic boosting noise — at least 4 of 5 freaks in
    # the top 5).
    top5 = set(ranked.head(5)["br_slug"])
    freak_slugs = {r[0] for r in FREAKS}
    overlap = len(top5 & freak_slugs)
    assert overlap >= 4, f"only {overlap}/5 freaks in top 5: {top5}"


def test_build_feature_matrix_without_combine_unchanged(synthetic_with_combine) -> None:
    """The legacy NCAA-only path must still work — used by all existing tests."""
    prospects, outcomes, _ = synthetic_with_combine
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    assert "wingspan_minus_height" not in X.columns
    assert "has_combine_data" not in X.columns
    assert len(X) == len(prospects)
