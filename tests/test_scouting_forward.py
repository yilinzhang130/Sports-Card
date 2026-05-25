"""Forward-looking PRISM scoring tests.

Offline-only. Uses FakeBRefClient + FakeMockDraftClient and an in-memory
SQLite. The CatBoost model is trained on a synthetic 2018 draft class (so
the .cbm checkpoint isn't required to run these tests) and passed in
directly via ``score_current_class(model=...)``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sportscards.db.models import Base, ProspectForecast
from sportscards.scouting.nba import features as feat
from sportscards.scouting.nba import ingest_bref, mock_draft, prism
from sportscards.scouting.nba.score_undrafted import (
    score_current_class,
)

# ---------------------------------------------------------------------------
# Synthetic training cohort (2018) — same shape as tests/test_scouting.py
# ---------------------------------------------------------------------------
TRAIN_SET = [
    ("luka", "Luka Doncic", 3, "PG", 19.2, 32.0, 0.61, 13.0, 28.0, 1.8, 0.7, 8.0, 0.99, 30.0),
    ("trae", "Trae Young", 5, "PG", 19.8, 36.0, 0.59, 6.0, 35.0, 1.5, 0.4, 6.0, 0.95, 20.0),
    ("ayton", "Deandre Ayton", 1, "C", 19.9, 28.0, 0.62, 20.0, 4.0, 0.5, 4.0, 7.0, 0.92, 10.0),
    ("bagley", "Marvin Bagley", 2, "PF", 19.1, 27.0, 0.61, 18.0, 3.0, 0.6, 2.5, 6.5, 0.96, 3.0),
    ("jjj", "Jaren Jackson", 4, "PF", 18.8, 22.0, 0.60, 14.0, 3.5, 1.0, 5.0, 7.5, 0.91, 18.0),
    ("mpj", "Michael Porter", 14, "SF", 20.1, 30.0, 0.55, 16.0, 4.0, 0.6, 1.5, 7.0, 0.97, 5.0),
    ("sga", "SGA", 11, "SG", 20.2, 24.0, 0.60, 7.0, 9.0, 1.6, 0.6, 7.0, 0.88, 25.0),
    ("knox", "Kevin Knox", 9, "SF", 18.9, 26.0, 0.55, 9.0, 2.5, 0.8, 0.7, 6.0, 0.85, 1.0),
    ("bridges", "Mikal Bridges", 10, "SF", 21.7, 18.0, 0.61, 7.0, 6.0, 1.5, 1.0, 7.5, 0.80, 14.0),
    ("walker", "Lonnie Walker", 18, "SG", 19.6, 24.0, 0.54, 6.0, 6.0, 1.1, 0.5, 6.5, 0.82, 2.0),
    ("smith", "Zhaire Smith", 16, "SG", 19.0, 19.0, 0.60, 8.0, 6.0, 1.7, 1.4, 6.0, 0.78, 0.5),
    ("robinson", "Mitchell Rob.", 36, "C", 20.0, 25.0, 0.65, 19.0, 2.0, 0.8, 4.5, 5.0, 0.55, 12.0),
]


def _train_prism() -> object:
    prospects = pd.DataFrame(
        [
            {
                "br_slug": r[0], "name": r[1], "draft_year": 2018, "draft_pick": r[2],
                "position": r[3], "age_at_draft": r[4], "usg_pct": r[5], "ts_pct": r[6],
                "trb_pct": r[7], "ast_pct": r[8], "stl_pct": r[9], "blk_pct": r[10],
                "sos": r[11], "recruit_rank_pct": r[12], "wingspan_in": 80.0,
                "max_vert_in": 36.0,
            }
            for r in TRAIN_SET
        ]
    )
    outcomes = pd.DataFrame([{"br_slug": r[0], "career_bpm_5y": r[13]} for r in TRAIN_SET])
    X, y, groups, _ = feat.build_feature_matrix(prospects, outcomes)
    return prism.train_pairwise_model(X, y, groups, iterations=200)


@pytest.fixture(scope="module")
def trained_model() -> object:
    return _train_prism()


# ---------------------------------------------------------------------------
# Fake current-season NCAA frame for the 2026 draft
# ---------------------------------------------------------------------------
# A mix of true freshmen (will be 2028 draft) and seniors (2026 draft), so
# we exercise the draft_year derivation path.
NCAA_ROWS = [
    # (slug, name, class_year, pos, age, usg, ts, trb, ast, stl, blk, sos, recruit_pct, ngp)
    ("elite_sr",  "Elite SR",  "SR", "PG", 22.0, 30.0, 0.62,  9.0, 25.0, 1.7, 0.6, 8.0, 0.85, 32),
    ("solid_sr",  "Solid SR",  "SR", "SF", 22.0, 24.0, 0.58, 10.0,  5.0, 1.3, 0.9, 7.0, 0.70, 30),
    ("hype_sr",   "Hyped SR",  "SR", "PF", 21.5, 28.0, 0.55, 12.0,  4.0, 0.9, 1.4, 7.5, 0.95, 28),
    ("weak_sr",   "Weak SR",   "SR", "SG", 22.3, 22.0, 0.52,  5.0,  4.0, 1.0, 0.4, 5.0, 0.40, 31),
    ("steady_sr", "Steady SR", "SR", "C",  22.1, 24.0, 0.61, 18.0,  2.0, 0.7, 3.8, 6.5, 0.60, 29),
    ("freshman",  "True FR",   "FR", "PG", 18.5, 26.0, 0.58,  7.0,  6.0, 1.5, 0.5, 8.0, 0.90, 18),
]


@pytest.fixture
def ncaa_season_parquet(tmp_path: Path) -> Path:
    """Write a synthetic ncaa_current_2025-26.parquet via the real ingest path."""
    rows = []
    for r in NCAA_ROWS:
        rows.append({
            "br_slug": r[0], "name": r[1], "class_year": r[2], "position": r[3],
            "age_at_draft": r[4], "usg_pct": r[5], "ts_pct": r[6], "trb_pct": r[7],
            "ast_pct": r[8], "stl_pct": r[9], "blk_pct": r[10], "sos": r[11],
            "recruit_rank_pct": r[12], "n_games_played": r[13],
            "wingspan_in": 80.0, "max_vert_in": 36.0,
        })
    raw = pd.DataFrame(rows)

    class FakeNCAA:
        def get_current_ncaa_season(self, season: str) -> pd.DataFrame:
            return raw.copy()

        # Unused but required for the Protocol if mypy ever bites.
        def get_draft_class(self, year: int) -> pd.DataFrame:
            return pd.DataFrame()

        def get_player_career_advanced(self, name: str, max_seasons: int = 5) -> pd.DataFrame:
            return pd.DataFrame()

    return ingest_bref.ingest_current_ncaa_season(
        "2025-26", client=FakeNCAA(), cache_dir=tmp_path
    )


# ---------------------------------------------------------------------------
# Fake mock-draft snapshots
# ---------------------------------------------------------------------------
def _write_mock_snapshots(
    mock_dir: Path,
    draft_year: int,
    snap_date: date,
    rankings_by_source: dict[str, list[tuple[str, int]]],
) -> None:
    """Write one parquet per source under ``mock_dir``."""
    mock_dir.mkdir(parents=True, exist_ok=True)
    for source, rows in rankings_by_source.items():
        df = pd.DataFrame(
            [
                {
                    "source": source,
                    "draft_year": draft_year,
                    "fetched_at": pd.Timestamp.now(tz="UTC"),
                    "rank": rank,
                    "player_name": slug.replace("_", " ").title(),
                    "br_slug": slug,
                }
                for slug, rank in rows
            ]
        )
        # mirror the normalisation done by refresh_mock_drafts
        df = mock_draft._normalize_mock_frame(df, source=source, draft_year=draft_year)
        out = mock_dir / f"{source}_{draft_year}_{snap_date.isoformat()}.parquet"
        df.to_parquet(out, index=False)


@pytest.fixture
def mock_dir_2026(tmp_path: Path) -> Path:
    """Mock-draft snapshots that put a hyped low-producer at the top and the
    elite producer in the middle of the first round."""
    mock_dir = tmp_path / "mock_drafts"
    snap = date(2026, 5, 1)
    _write_mock_snapshots(
        mock_dir,
        draft_year=2026,
        snap_date=snap,
        rankings_by_source={
            "espn": [
                ("hype_sr", 1), ("ayton_recruit", 2), ("solid_sr", 8),
                ("elite_sr", 22), ("steady_sr", 15),
            ],
            "tankathon": [
                ("hype_sr", 2), ("solid_sr", 9), ("elite_sr", 25),
                ("steady_sr", 18), ("weak_sr", 45),
            ],
            "nbadraft_net": [
                ("hype_sr", 3), ("solid_sr", 7), ("elite_sr", 28),
                ("steady_sr", 12), ("weak_sr", 40),
            ],
            "the_ringer": [
                ("hype_sr", 4), ("solid_sr", 10), ("elite_sr", 24),
                ("steady_sr", 16),
                # Only-2-source player: should be dropped by the consensus aggregator.
                ("only_two_sources", 50),
            ],
        },
    )
    # A second source for the dropout player so it has 2 < 3 sources total.
    df = pd.DataFrame([{
        "source": "espn",
        "draft_year": 2026,
        "fetched_at": pd.Timestamp.now(tz="UTC"),
        "rank": 55,
        "player_name": "Only Two Sources",
        "br_slug": "only_two_sources",
    }])
    df = mock_draft._normalize_mock_frame(df, source="espn", draft_year=2026)
    df.to_parquet(mock_dir / f"espn_2026_{snap.isoformat()}_extra.parquet", index=False)
    return mock_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_iqr_and_median_math() -> None:
    """Spec: median([5,8,12]) = 8, IQR = 3.5."""
    s = pd.Series([5, 8, 12])
    assert float(s.median()) == 8.0
    assert mock_draft._iqr(s) == pytest.approx(3.5)


def test_aggregate_consensus_drops_under_min_sources(mock_dir_2026: Path) -> None:
    df = mock_draft.aggregate_consensus_rank(
        2026, date(2026, 5, 1), cache_dir=mock_dir_2026
    )
    slugs = set(df["br_slug"])
    assert "only_two_sources" not in slugs  # fewer than MIN_SOURCES_FOR_CONSENSUS
    assert "hype_sr" in slugs and "elite_sr" in slugs
    hype_rank = float(df.loc[df["br_slug"] == "hype_sr", "consensus_rank"].iloc[0])
    assert hype_rank == 2.5  # median of [1,2,3,4]


def test_pipeline_writes_forecast_rows(
    ncaa_season_parquet: Path, mock_dir_2026: Path, trained_model: object
) -> None:
    cache_dir = ncaa_season_parquet.parent
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        df = score_current_class(
            draft_year=2026,
            season="2025-26",
            as_of=date(2026, 5, 1),
            cache_dir=cache_dir,
            mock_draft_dir=mock_dir_2026,
            model=trained_model,
            session=s,
        )
        s.commit()
        rows = s.execute(select(ProspectForecast)).scalars().all()

    # All 5 SR rows enter the cohort (the freshman has draft_year=2028).
    sr_slugs = {r[0] for r in NCAA_ROWS if r[2] == "SR"}
    assert {r.player_slug for r in rows} == sr_slugs
    assert all(r.draft_year == 2026 for r in rows)
    # Each row has a class_year-derived flag; SRs are NOT underclassmen.
    assert not any(r.is_underclassman for r in rows)
    assert set(df["br_slug"]) == sr_slugs


def test_undervalued_planted_prospect_gets_positive_premium(
    ncaa_season_parquet: Path, mock_dir_2026: Path, trained_model: object
) -> None:
    """Elite producer with mid-1st mock consensus → positive premium."""
    df = score_current_class(
        draft_year=2026,
        season="2025-26",
        as_of=date(2026, 5, 1),
        cache_dir=ncaa_season_parquet.parent,
        mock_draft_dir=mock_dir_2026,
        model=trained_model,
    )
    elite = df.loc[df["br_slug"] == "elite_sr"].iloc[0]
    assert pd.notna(elite["premium"]), "elite_sr should have a consensus rank"
    assert elite["premium"] > 0, (
        f"expected positive premium for elite producer ranked ~25, "
        f"got {elite['premium']:.3f}"
    )


def test_overvalued_planted_prospect_gets_negative_premium(
    ncaa_season_parquet: Path, mock_dir_2026: Path, trained_model: object
) -> None:
    """Hyped low-producer at #1 in mocks → negative premium."""
    df = score_current_class(
        draft_year=2026,
        season="2025-26",
        as_of=date(2026, 5, 1),
        cache_dir=ncaa_season_parquet.parent,
        mock_draft_dir=mock_dir_2026,
        model=trained_model,
    )
    hyped = df.loc[df["br_slug"] == "hype_sr"].iloc[0]
    assert pd.notna(hyped["premium"])
    assert hyped["premium"] < 0, (
        f"expected negative premium for over-hyped weak producer at #1, "
        f"got {hyped['premium']:.3f}"
    )


def test_idempotent_rescore_same_as_of(
    ncaa_season_parquet: Path, mock_dir_2026: Path, trained_model: object
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        for _ in range(2):
            score_current_class(
                draft_year=2026,
                season="2025-26",
                as_of=date(2026, 5, 1),
                cache_dir=ncaa_season_parquet.parent,
                mock_draft_dir=mock_dir_2026,
                model=trained_model,
                session=s,
            )
            s.commit()
        rows = s.execute(select(ProspectForecast)).scalars().all()
    sr_slugs = {r[0] for r in NCAA_ROWS if r[2] == "SR"}
    assert len(rows) == len(sr_slugs), "second run must upsert, not duplicate"


def test_history_preserved_across_as_of_dates(
    ncaa_season_parquet: Path, mock_dir_2026: Path, trained_model: object
) -> None:
    """Two snapshots with different as_of_date must both persist."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Add a later snapshot for one source so the second run sees fresher data.
    later = date(2026, 5, 15)
    _write_mock_snapshots(
        mock_dir_2026,
        draft_year=2026,
        snap_date=later,
        rankings_by_source={
            "espn": [("hype_sr", 1), ("solid_sr", 5), ("elite_sr", 20),
                      ("steady_sr", 12), ("weak_sr", 38)],
        },
    )

    with Session(engine) as s:
        score_current_class(
            draft_year=2026, season="2025-26",
            as_of=date(2026, 5, 1),
            cache_dir=ncaa_season_parquet.parent,
            mock_draft_dir=mock_dir_2026,
            model=trained_model,
            session=s,
        )
        s.commit()
        score_current_class(
            draft_year=2026, season="2025-26",
            as_of=later,
            cache_dir=ncaa_season_parquet.parent,
            mock_draft_dir=mock_dir_2026,
            model=trained_model,
            session=s,
        )
        s.commit()
        as_of_dates = {r.as_of_date for r in s.execute(select(ProspectForecast)).scalars().all()}

    assert len(as_of_dates) == 2, f"expected two snapshots, got {as_of_dates!r}"


def test_consensus_dropout_propagates_to_nan_premium(
    ncaa_season_parquet: Path, tmp_path: Path, trained_model: object
) -> None:
    """When a player has < 3 mock sources, their premium is NaN."""
    mock_dir = tmp_path / "mock_only_two"
    _write_mock_snapshots(
        mock_dir,
        draft_year=2026,
        snap_date=date(2026, 5, 1),
        rankings_by_source={
            "espn": [("elite_sr", 5), ("hype_sr", 1), ("solid_sr", 8)],
            "tankathon": [("elite_sr", 7), ("hype_sr", 2), ("solid_sr", 9)],
            # weak_sr only here (2 sources after merging with tankathon).
            "nbadraft_net": [
                ("elite_sr", 6), ("hype_sr", 3), ("solid_sr", 10),
                ("weak_sr", 40),
            ],
            "the_ringer": [("weak_sr", 38)],  # weak_sr's 2nd source only
        },
    )
    df = score_current_class(
        draft_year=2026, season="2025-26",
        as_of=date(2026, 5, 1),
        cache_dir=ncaa_season_parquet.parent,
        mock_draft_dir=mock_dir,
        model=trained_model,
    )
    weak = df.loc[df["br_slug"] == "weak_sr"].iloc[0]
    assert pd.isna(weak["premium"]), "weak_sr has only 2 sources → premium must be NaN"
    elite = df.loc[df["br_slug"] == "elite_sr"].iloc[0]
    assert pd.notna(elite["premium"]), "elite_sr has 3 sources → premium must be finite"


def test_persist_handles_nan_premium_cleanly(
    ncaa_season_parquet: Path, tmp_path: Path, trained_model: object
) -> None:
    """Rows with NaN premium must persist (the metadata is still useful)."""
    mock_dir = tmp_path / "mock_one"
    _write_mock_snapshots(
        mock_dir,
        draft_year=2026,
        snap_date=date(2026, 5, 1),
        rankings_by_source={"espn": [("elite_sr", 1)]},  # only one source — everyone dropped
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        df = score_current_class(
            draft_year=2026, season="2025-26",
            as_of=date(2026, 5, 1),
            cache_dir=ncaa_season_parquet.parent,
            mock_draft_dir=mock_dir,
            model=trained_model,
            session=s,
        )
        s.commit()
        rows = s.execute(select(ProspectForecast)).scalars().all()
    assert len(rows) == len(df)
    assert all(r.premium is None for r in rows)


def test_expected_draft_year_derivation() -> None:
    """FR in 2025-26 → 2028 draft; SR → 2026."""
    assert ingest_bref.expected_draft_year("2025-26", "FR") == 2028
    assert ingest_bref.expected_draft_year("2025-26", "SO") == 2027
    assert ingest_bref.expected_draft_year("2025-26", "JR") == 2026
    assert ingest_bref.expected_draft_year("2025-26", "SR") == 2026


def test_ingest_current_ncaa_normalisation(tmp_path: Path) -> None:
    """ingest_current_ncaa_season fills all CURRENT_NCAA_COLUMNS even when the
    upstream frame is missing some columns."""

    class Sparse:
        def get_current_ncaa_season(self, season: str) -> pd.DataFrame:
            return pd.DataFrame(
                [{"br_slug": "x", "name": "X", "class_year": "FR"}]
            )

        def get_draft_class(self, year: int) -> pd.DataFrame: return pd.DataFrame()
        def get_player_career_advanced(self, n: str, max_seasons: int = 5) -> pd.DataFrame:
            return pd.DataFrame()

    p = ingest_bref.ingest_current_ncaa_season("2025-26", client=Sparse(), cache_dir=tmp_path)
    df = pd.read_parquet(p)
    assert set(ingest_bref.CURRENT_NCAA_COLUMNS).issubset(df.columns)
    assert df.iloc[0]["draft_year"] == 2028  # FR → 2028
    assert df.iloc[0]["prior_league"] == "NCAA"
