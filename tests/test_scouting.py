"""PRISM scouting-model tests.

All tests run fully offline. The Basketball-Reference scraper is replaced
with a ``FakeBRefClient`` so no network call is made. Synthetic prospects
have a known true ordering by BPM, and the test asserts the pairwise model
recovers it with concordance > 0.85 and that the score table is populated
for every prospect in the cohort.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sportscards.scouting.nba import features as feat
from sportscards.scouting.nba import ingest_bref, prism, score


# ---------------------------------------------------------------------------
# Synthetic cohort
# ---------------------------------------------------------------------------
SYNTHETIC = [
    # (slug, name, draft_pick, position, age, usg, ts, trb, ast, stl, blk, sos, recruit_pct, true_bpm)
    ("luka",     "Luka Doncic",   3,  "PG", 19.2, 32.0, 0.61, 13.0, 28.0, 1.8, 0.7, 8.0, 0.99, 30.0),
    ("trae",     "Trae Young",    5,  "PG", 19.8, 36.0, 0.59,  6.0, 35.0, 1.5, 0.4, 6.0, 0.95, 20.0),
    ("ayton",    "Deandre Ayton", 1,  "C",  19.9, 28.0, 0.62, 20.0,  4.0, 0.5, 4.0, 7.0, 0.92, 10.0),
    ("bagley",   "Marvin Bagley", 2,  "PF", 19.1, 27.0, 0.61, 18.0,  3.0, 0.6, 2.5, 6.5, 0.96,  3.0),
    ("jjj",      "Jaren Jackson", 4,  "PF", 18.8, 22.0, 0.60, 14.0,  3.5, 1.0, 5.0, 7.5, 0.91, 18.0),
    ("mpj",      "Michael Porter", 14, "SF", 20.1, 30.0, 0.55, 16.0,  4.0, 0.6, 1.5, 7.0, 0.97,  5.0),
    ("sga",      "SGA",           11, "SG", 20.2, 24.0, 0.60,  7.0,  9.0, 1.6, 0.6, 7.0, 0.88, 25.0),
    ("knox",     "Kevin Knox",     9, "SF", 18.9, 26.0, 0.55,  9.0,  2.5, 0.8, 0.7, 6.0, 0.85,  1.0),
    ("bridges",  "Mikal Bridges", 10, "SF", 21.7, 18.0, 0.61,  7.0,  6.0, 1.5, 1.0, 7.5, 0.80, 14.0),
    ("walker",   "Lonnie Walker", 18, "SG", 19.6, 24.0, 0.54,  6.0,  6.0, 1.1, 0.5, 6.5, 0.82,  2.0),
    ("smith",    "Zhaire Smith",  16, "SG", 19.0, 19.0, 0.60,  8.0,  6.0, 1.7, 1.4, 6.0, 0.78,  0.5),
    ("robinson", "Mitchell Rob.", 36, "C",  20.0, 25.0, 0.65, 19.0,  2.0, 0.8, 4.5, 5.0, 0.55, 12.0),
]


@pytest.fixture
def synthetic_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    prospects = pd.DataFrame(
        [
            {
                "br_slug": r[0],
                "name": r[1],
                "draft_year": 2018,
                "draft_pick": r[2],
                "position": r[3],
                "age_at_draft": r[4],
                "usg_pct": r[5],
                "ts_pct": r[6],
                "trb_pct": r[7],
                "ast_pct": r[8],
                "stl_pct": r[9],
                "blk_pct": r[10],
                "sos": r[11],
                "recruit_rank_pct": r[12],
                "wingspan_in": 80.0,
                "max_vert_in": 36.0,
            }
            for r in SYNTHETIC
        ]
    )
    outcomes = pd.DataFrame(
        [{"br_slug": r[0], "career_bpm_5y": r[13], "career_ws_5y": r[13] * 2, "career_vorp_5y": r[13] / 3}
         for r in SYNTHETIC]
    )
    return prospects, outcomes


class FakeBRefClient:
    """Drop-in replacement for ``BRefClient`` — guarantees no network."""

    def __init__(self, prospects: pd.DataFrame, outcomes: pd.DataFrame) -> None:
        self._prospects = prospects
        self._outcomes = outcomes

    def get_draft_class(self, year: int) -> pd.DataFrame:
        return self._prospects[self._prospects["draft_year"] == year].copy()

    def get_player_career_advanced(self, name: str, max_seasons: int = 5) -> pd.DataFrame:
        slug_for_name = self._prospects.loc[self._prospects["name"] == name, "br_slug"]
        if slug_for_name.empty:
            raise KeyError(name)
        slug = slug_for_name.iloc[0]
        bpm = float(self._outcomes.loc[self._outcomes["br_slug"] == slug, "career_bpm_5y"].iloc[0])
        return pd.DataFrame({"BPM": [bpm], "WS": [bpm * 2], "VORP": [bpm / 3]})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_ingest_writes_parquet(tmp_path: Path, synthetic_data) -> None:
    prospects, outcomes = synthetic_data
    client = FakeBRefClient(prospects, outcomes)
    p_path, o_path = ingest_bref.ingest_year(2018, client=client, cache_dir=tmp_path)

    assert p_path.exists()
    assert o_path.exists()
    p = pd.read_parquet(p_path)
    o = pd.read_parquet(o_path)
    assert len(p) == len(SYNTHETIC)
    assert len(o) == len(SYNTHETIC)
    assert set(ingest_bref.PROSPECT_COLUMNS).issubset(p.columns)


def test_features_alignment(synthetic_data) -> None:
    prospects, outcomes = synthetic_data
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    assert len(X) == len(prospects)
    assert (y >= 0).all()  # negatives clipped per PRISM spec
    assert set(slugs) == set(prospects["br_slug"])
    # one-hot present
    for p in feat.POSITIONS:
        assert f"pos_{p}" in X.columns


def test_pairwise_model_recovers_ordering(synthetic_data) -> None:
    prospects, outcomes = synthetic_data
    X, y, groups, _ = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=400)
    scores = prism.predict_scores(model, X)
    c = prism.concordance(scores, y.to_numpy(), groups.to_numpy())
    assert c > 0.85, f"pairwise concordance too low: {c:.3f}"


def test_stardom_premium_shape(synthetic_data) -> None:
    prospects, outcomes = synthetic_data
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=300)
    scores = prism.predict_scores(model, X)
    df = score.compute_stardom_premium(slugs, groups, prospects["draft_pick"], scores)

    assert len(df) == len(prospects)
    assert df["percentile_rank"].between(0, 1).all()
    assert df["premium"].between(-1, 1).all()
    # Luka was true-BPM #1 but drafted #3 — model should rate him higher than slot.
    luka_premium = float(df.loc[df["br_slug"] == "luka", "premium"].iloc[0])
    assert luka_premium > 0, f"expected positive premium for Luka, got {luka_premium:.3f}"


def test_persist_scores_writes_table(synthetic_data) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sportscards.db.models import Base, Player, PlayerStardomScore

    prospects, outcomes = synthetic_data
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=200)
    scores = prism.predict_scores(model, X)
    df = score.compute_stardom_premium(slugs, groups, prospects["draft_pick"], scores)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for _, row in prospects.iterrows():
            s.add(Player(name=row["name"], draft_year=int(row["draft_year"]),
                         draft_pick=int(row["draft_pick"]), br_slug=row["br_slug"]))
        s.commit()
        n = score.persist_scores(s, df, model_version="prism_test")
        s.commit()
        stored = s.query(PlayerStardomScore).all()

    assert n == len(prospects)
    assert len(stored) == len(prospects)


def test_persist_scores_multi_version_coexistence(synthetic_data) -> None:
    """Two model_versions for the same player must coexist — the PK is
    (player_id, model_version), not player_id alone."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sportscards.db.models import Base, Player, PlayerStardomScore

    prospects, outcomes = synthetic_data
    X, _, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, _, groups, iterations=100)
    scores = prism.predict_scores(model, X)
    df = score.compute_stardom_premium(slugs, groups, prospects["draft_pick"], scores)

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for _, row in prospects.iterrows():
            s.add(Player(name=row["name"], draft_year=int(row["draft_year"]),
                         draft_pick=int(row["draft_pick"]), br_slug=row["br_slug"]))
        s.commit()
        score.persist_scores(s, df, model_version="prism_v1")
        s.commit()
        score.persist_scores(s, df, model_version="prism_v2")
        s.commit()
        rows = s.query(PlayerStardomScore).all()

    assert len(rows) == 2 * len(prospects)
    versions = {r.model_version for r in rows}
    assert versions == {"prism_v1", "prism_v2"}
