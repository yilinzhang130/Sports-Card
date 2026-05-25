"""International (G-League + EuroLeague) scouting tests.

All tests run fully offline via FakeGLeagueClient / FakeEuroClient. The
critical assertions:

1. ``ingest_season`` and ``ingest_league_season`` write Parquet artifacts
   shaped like the cached schema other modules expect.
2. ``build_unified_cohort`` merges NCAA + G-League + Euro frames, tags each
   row with a ``prospect_origin``, and applies MLE multipliers to non-NCAA
   percent stats.
3. With Wembanyama / Doncic-shaped synthetic Euro rows, the pairwise model
   ranks them at or near the top of their draft classes.
4. The combined cohort still hits concordance ≥ 0.80.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd
import pytest

from sportscards.scouting.nba import features as feat
from sportscards.scouting.nba import (
    ingest_bref,
    ingest_euro,
    ingest_gleague,
    ingest_prospects,
    prism,
)
from sportscards.scouting.nba.mle import load_mle_table

# Re-use the canonical NCAA synthetic class. Inlined (rather than imported
# from tests.test_scouting) because CI doesn't expose `tests` as a package.
SYNTHETIC = [
    # (slug, name, draft_pick, position, age, usg, ts, trb, ast, stl, blk, sos,
    #  recruit_pct, true_bpm)
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


class FakeBRefClient:
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
# Fake clients
# ---------------------------------------------------------------------------


class _DictGLeague:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames = frames

    def get_season_stats(self, season: str) -> pd.DataFrame:
        return self._frames.get(season, pd.DataFrame()).copy()


class _DictEuro:
    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    def get_league_season(self, league: str, season: str) -> pd.DataFrame:
        return self._frames.get((league, season), pd.DataFrame()).copy()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _ncaa_2018_frame() -> tuple[pd.DataFrame, pd.DataFrame]:
    # SYNTHETIC re-used from test_scouting.py — same 12 prospects, but we
    # drop the 'luka' row so we can test Doncic coming in from EuroLeague
    # instead of NCAA.
    rows = [r for r in SYNTHETIC if r[0] != "luka"]
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
            for r in rows
        ]
    )
    outcomes = pd.DataFrame(
        [
            {
                "br_slug": r[0],
                "career_bpm_5y": r[13],
                "career_ws_5y": r[13] * 2,
                "career_vorp_5y": r[13] / 3,
            }
            for r in rows
        ]
        # Doncic's 5-yr BPM comes from his original SYNTHETIC entry (30.0),
        # joined on slug below regardless of where the prospect row came from.
        + [
            {
                "br_slug": "luka",
                "career_bpm_5y": 30.0,
                "career_ws_5y": 60.0,
                "career_vorp_5y": 10.0,
            }
        ]
    )
    return prospects, outcomes


def _doncic_euro_row() -> dict[str, object]:
    """EuroLeague 2017-18 MVP-shaped row. Pre-MLE percent stats — the unified
    cohort builder will multiply by EuroLeague MLE (1.15) so the model sees
    NCAA-equivalent magnitudes."""
    return {
        "br_slug": "luka",
        "name": "Luka Doncic",
        "league": "euroleague",
        "season": "2017-18",
        "age": 19.0,
        "position": "PG",
        "team": "Real Madrid",
        "gp": 33,
        "mpg": 25.0,
        "pts_per40": 23.0,
        "reb_per40": 8.5,
        "ast_per40": 7.5,
        "stl_per40": 1.8,
        "blk_per40": 0.4,
        "ts_pct": 0.59,
        "usg_pct": 28.0,
        "trb_pct": 11.5,
        "ast_pct": 27.0,
        "stl_pct": 2.5,
        "blk_pct": 1.2,
    }


def _wemby_euro_row() -> dict[str, object]:
    """Metropolitans 92 2022-23 — extreme USG / BLK / TS for a 19-year-old."""
    return {
        "br_slug": "wemby",
        "name": "Victor Wembanyama",
        "league": "euroleague",  # use top tier for MLE 1.15
        "season": "2022-23",
        "age": 19.0,
        "position": "C",
        "team": "Metropolitans 92",
        "gp": 34,
        "mpg": 32.0,
        "pts_per40": 27.0,
        "reb_per40": 12.0,
        "ast_per40": 3.0,
        "stl_per40": 2.0,
        "blk_per40": 4.5,
        "ts_pct": 0.61,
        "usg_pct": 32.0,
        "trb_pct": 18.0,
        "ast_pct": 9.0,
        "stl_pct": 2.4,
        "blk_pct": 11.5,
    }


def _wemby_2023_class() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Tiny 5-prospect 2023 class so the pairwise model has something to
    rank. Synthetic — only Wembanyama's row comes from EuroLeague.
    """
    # (slug, name, pick, pos, age, usg, ts, trb, ast, stl, blk, sos, recruit, true_bpm)
    rows = [
        ("scoot", "Scoot H.", 3, "PG", 19.2, 30.0, 0.55, 6.0, 28.0, 1.5, 0.5, 7.5, 0.95, 12.0),
        ("bmiller", "Brandon M.", 2, "SF", 20.1, 27.0, 0.58, 8.0, 6.0, 1.4, 1.5, 8.0, 0.90, 10.0),
        ("amen", "Amen T.", 4, "PG", 20.2, 26.0, 0.57, 7.5, 22.0, 1.8, 0.8, 6.5, 0.88, 11.0),
        ("ausar", "Ausar T.", 5, "SF", 20.2, 24.0, 0.55, 8.5, 15.0, 1.9, 1.0, 6.5, 0.86, 9.0),
        ("jarace", "Jarace W.", 8, "PF", 19.6, 22.0, 0.56, 10.5, 5.5, 1.5, 3.5, 7.0, 0.92, 7.5),
    ]
    prospects = pd.DataFrame(
        [
            {
                "br_slug": r[0],
                "name": r[1],
                "draft_year": 2023,
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
                "wingspan_in": 82.0,
                "max_vert_in": 36.0,
            }
            for r in rows
        ]
    )
    outcomes = pd.DataFrame(
        [
            {
                "br_slug": r[0],
                "career_bpm_5y": r[13],
                "career_ws_5y": r[13] * 2,
                "career_vorp_5y": r[13] / 3,
            }
            for r in rows
        ]
        + [
            {
                "br_slug": "wemby",
                "career_bpm_5y": 28.0,  # ground-truth BPM — top of class
                "career_ws_5y": 56.0,
                "career_vorp_5y": 9.3,
            }
        ]
    )
    return prospects, outcomes


# ---------------------------------------------------------------------------
# Unit tests — ingestors
# ---------------------------------------------------------------------------
def test_gleague_ingest_writes_parquet(tmp_path: Path) -> None:
    fake_row = pd.DataFrame(
        [
            {
                "name": "Test Player",
                "age": 21.0,
                "position": "G",
                "team": "Westchester",
                "gp": 30,
                "mpg": 32.0,
                "ts_pct": 0.58,
                "usg_pct": 26.0,
                "trb_pct": 6.0,
                "ast_pct": 22.0,
                "stl_pct": 2.0,
                "blk_pct": 0.5,
            }
        ]
    )
    client = _DictGLeague({"2021-22": fake_row})
    path = ingest_gleague.ingest_season("2021-22", client=client, cache_dir=tmp_path)
    assert path.exists()
    out = pd.read_parquet(path)
    assert set(ingest_gleague.GLEAGUE_COLUMNS).issubset(out.columns)
    assert out["season"].iloc[0] == "2021-22"


def test_euro_ingest_writes_parquet(tmp_path: Path) -> None:
    fake_row = pd.DataFrame([_doncic_euro_row()])
    client = _DictEuro({("euroleague", "2017-18"): fake_row})
    path = ingest_euro.ingest_league_season(
        "euroleague", "2017-18", client=client, cache_dir=tmp_path
    )
    assert path.exists()
    out = pd.read_parquet(path)
    assert set(ingest_euro.EURO_COLUMNS).issubset(out.columns)
    assert out["league"].iloc[0] == "euroleague"


def test_mle_table_loads() -> None:
    table = load_mle_table()
    assert table["NCAA"]["mle"] == pytest.approx(1.00)
    assert table["EUROLEAGUE"]["mle"] > 1.0
    assert table["EUROLEAGUE"]["strength_rank"] >= table["NCAA"]["strength_rank"]


# ---------------------------------------------------------------------------
# Integration — unified cohort
# ---------------------------------------------------------------------------
def _populate_caches(
    tmp_path: Path,
    *,
    ncaa: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    gleague: dict[str, pd.DataFrame] | None = None,
    euro: dict[tuple[str, str], pd.DataFrame] | None = None,
) -> dict[str, Path]:
    ncaa_dir = tmp_path / "ncaa"
    gleague_dir = tmp_path / "gleague"
    euro_dir = tmp_path / "euro"
    ncaa_dir.mkdir()
    gleague_dir.mkdir()
    euro_dir.mkdir()

    if ncaa is not None:
        prospects, outcomes = ncaa
        client = FakeBRefClient(prospects, outcomes)
        for y in sorted(prospects["draft_year"].unique().tolist()):
            ingest_bref.ingest_year(int(y), client=client, cache_dir=ncaa_dir)
            # ingest_year only writes outcomes for slugs in the NCAA prospect
            # frame; international prospects (Doncic, Wembanyama) come in via
            # the euro cache but their NBA outcomes still need to be in the
            # outcomes parquet so the unified cohort can join them. Overwrite
            # the per-year outcomes file with the full synthetic outcomes
            # filtered to that draft class.
            year_outcomes = outcomes.merge(
                prospects.assign(_kept=True)[["br_slug", "draft_year", "_kept"]],
                on="br_slug",
                how="left",
            )
            # any slug NOT in the NCAA prospect frame is an international —
            # keep them too, attributed to the draft year being ingested.
            extra = outcomes[~outcomes["br_slug"].isin(prospects["br_slug"])]
            kept = year_outcomes.drop(columns=["_kept", "draft_year"], errors="ignore")
            final = pd.concat([kept, extra], ignore_index=True).drop_duplicates(
                subset="br_slug", keep="first"
            )
            final.to_parquet(ncaa_dir / f"nba_outcomes_{int(y)}.parquet", index=False)

    if gleague:
        client_g = _DictGLeague(gleague)
        for season in gleague:
            ingest_gleague.ingest_season(season, client=client_g, cache_dir=gleague_dir)

    if euro:
        client_e = _DictEuro(euro)
        for lg, season in euro:
            ingest_euro.ingest_league_season(lg, season, client=client_e, cache_dir=euro_dir)

    return {"ncaa": ncaa_dir, "gleague": gleague_dir, "euro": euro_dir}


def test_unified_cohort_merges_origins(tmp_path: Path) -> None:
    ncaa = _ncaa_2018_frame()
    euro_frame = pd.DataFrame([_doncic_euro_row()])
    dirs = _populate_caches(
        tmp_path,
        ncaa=ncaa,
        euro={("euroleague", "2017-18"): euro_frame},
    )

    prospects, outcomes = ingest_prospects.build_unified_cohort(
        range(2018, 2019),
        ncaa_cache_dir=dirs["ncaa"],
        gleague_cache_dir=dirs["gleague"],
        euro_cache_dir=dirs["euro"],
    )

    # 11 NCAA rows (minus 'luka') + 1 EuroLeague row for Luka.
    assert len(prospects) == 12
    assert "luka" in set(prospects["br_slug"])
    luka_row = prospects[prospects["br_slug"] == "luka"].iloc[0]
    assert luka_row["prospect_origin"] == "EUROLEAGUE"
    # USG% must have been scaled by EuroLeague MLE (1.15).
    assert luka_row["usg_pct"] == pytest.approx(28.0 * 1.15, rel=1e-3)
    # TS% NOT scaled.
    assert luka_row["ts_pct"] == pytest.approx(0.59, rel=1e-3)
    # outcomes still join cleanly.
    assert "luka" in set(outcomes["br_slug"])


def test_doncic_ranks_top_three_in_2018(tmp_path: Path) -> None:
    ncaa = _ncaa_2018_frame()
    euro_frame = pd.DataFrame([_doncic_euro_row()])
    dirs = _populate_caches(
        tmp_path,
        ncaa=ncaa,
        euro={("euroleague", "2017-18"): euro_frame},
    )

    prospects, outcomes = ingest_prospects.build_unified_cohort(
        range(2018, 2019),
        ncaa_cache_dir=dirs["ncaa"],
        gleague_cache_dir=dirs["gleague"],
        euro_cache_dir=dirs["euro"],
    )
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=400)
    scores = prism.predict_scores(model, X)

    ranked = (
        pd.DataFrame({"slug": cast(pd.Series, slugs).values, "score": scores})
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )
    top3 = list(ranked["slug"].head(3))
    assert "luka" in top3, f"Doncic should be top-3; ranked: {ranked.head(5).to_dict('records')}"


def test_wembanyama_ranks_top_one_in_2023(tmp_path: Path) -> None:
    ncaa = _wemby_2023_class()
    euro_frame = pd.DataFrame([_wemby_euro_row()])
    dirs = _populate_caches(
        tmp_path,
        ncaa=ncaa,
        euro={("euroleague", "2022-23"): euro_frame},
    )

    prospects, outcomes = ingest_prospects.build_unified_cohort(
        range(2023, 2024),
        ncaa_cache_dir=dirs["ncaa"],
        gleague_cache_dir=dirs["gleague"],
        euro_cache_dir=dirs["euro"],
    )
    X, y, groups, slugs = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=400)
    scores = prism.predict_scores(model, X)

    ranked = (
        pd.DataFrame({"slug": cast(pd.Series, slugs).values, "score": scores})
        .sort_values("score", ascending=False)
        .reset_index(drop=True)
    )
    assert ranked.iloc[0]["slug"] == "wemby", (
        f"Wembanyama should be #1; ranked: {ranked.head(5).to_dict('records')}"
    )


def test_combined_cohort_concordance(tmp_path: Path) -> None:
    ncaa_2018 = _ncaa_2018_frame()
    ncaa_2023 = _wemby_2023_class()
    ncaa_prospects = pd.concat([ncaa_2018[0], ncaa_2023[0]], ignore_index=True)
    ncaa_outcomes = pd.concat([ncaa_2018[1], ncaa_2023[1]], ignore_index=True)

    euro_frames = {
        ("euroleague", "2017-18"): pd.DataFrame([_doncic_euro_row()]),
        ("euroleague", "2022-23"): pd.DataFrame([_wemby_euro_row()]),
    }
    dirs = _populate_caches(
        tmp_path,
        ncaa=(ncaa_prospects, ncaa_outcomes),
        euro=euro_frames,
    )

    prospects, outcomes = ingest_prospects.build_unified_cohort(
        range(2018, 2024),
        ncaa_cache_dir=dirs["ncaa"],
        gleague_cache_dir=dirs["gleague"],
        euro_cache_dir=dirs["euro"],
    )
    X, y, groups, _ = feat.build_feature_matrix(prospects, outcomes)
    model = prism.train_pairwise_model(X, y, groups, iterations=400)
    scores = prism.predict_scores(model, X)
    c = prism.concordance(scores, y.to_numpy(), groups.to_numpy())
    assert c >= 0.80, f"combined-cohort concordance too low: {c:.3f}"
