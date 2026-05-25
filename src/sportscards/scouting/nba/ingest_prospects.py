"""Unified prospect cohort across NCAA + G-League + European leagues.

The pairwise scouting model only needs one consolidated frame keyed by
``(br_slug, draft_year)``. This module merges the three source cohorts,
tags each row with a ``prospect_origin``, applies first-order Major League
Equivalency multipliers so per-stat magnitudes are comparable, and returns a
``(prospects, outcomes)`` pair shaped exactly like ``ingest_bref.load_cohort``
so downstream code (features + prism) doesn't have to change.

Pre-draft players with multiple origins (e.g., one NCAA year + one Euro
year) collapse to the **most recent pre-draft season** of stats; the column
``years_in_prior_league`` records how many seasons we observed in that
league.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from sportscards.scouting.nba.ingest_bref import (
    CACHE_DIR as NCAA_CACHE_DIR,
)
from sportscards.scouting.nba.ingest_bref import (
    PROSPECT_COLUMNS,
)
from sportscards.scouting.nba.ingest_euro import (
    CACHE_DIR as EURO_CACHE_DIR,
)
from sportscards.scouting.nba.ingest_euro import (
    LEAGUE_ORIGIN,
    load_euro_cohort,
)
from sportscards.scouting.nba.ingest_gleague import (
    CACHE_DIR as GLEAGUE_CACHE_DIR,
)
from sportscards.scouting.nba.ingest_gleague import (
    load_gleague_cohort,
)
from sportscards.scouting.nba.mle import SCALED_STATS, mle_for

logger = logging.getLogger(__name__)

# Columns added on top of the NCAA prospect schema.
EXTRA_COLUMNS = [
    "prospect_origin",
    "years_in_prior_league",
    "prior_league_mle_score",
    "prior_league_strength_rank",
]
UNIFIED_PROSPECT_COLUMNS = list(PROSPECT_COLUMNS) + EXTRA_COLUMNS

ORIGIN_NCAA = "NCAA"
ORIGIN_GLEAGUE = "GLEAGUE"


def build_unified_cohort(
    years: range,
    ncaa_cache_dir: Path = NCAA_CACHE_DIR,
    gleague_cache_dir: Path = GLEAGUE_CACHE_DIR,
    euro_cache_dir: Path = EURO_CACHE_DIR,
    euro_leagues: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Concatenate NCAA + G-League + Euro cohorts into one prospect frame.

    Returns (prospects, outcomes) with prospects matching
    ``UNIFIED_PROSPECT_COLUMNS``. Outcomes are pulled from the NCAA cache —
    that's the only path that currently records 5-year NBA outcomes; G-League
    and Euro prospects who later played in the NBA can have outcomes added by
    running ``scouting ingest-nba`` for their draft year, which writes the
    same ``nba_outcomes_<year>.parquet`` and is joined here on ``br_slug``.
    """
    ncaa_p, outcomes = _load_ncaa_safe(years, ncaa_cache_dir)
    seasons = _seasons_for_years(years)

    gleague_raw = load_gleague_cohort(seasons, cache_dir=gleague_cache_dir)
    gleague_p = _gleague_to_prospects(gleague_raw, years)

    leagues = euro_leagues or list(LEAGUE_ORIGIN.keys())
    euro_raw = load_euro_cohort(leagues, seasons, cache_dir=euro_cache_dir)
    euro_p = _euro_to_prospects(euro_raw, years)

    frames = [df for df in (ncaa_p, gleague_p, euro_p) if not df.empty]
    if not frames:
        prospects = pd.DataFrame(columns=UNIFIED_PROSPECT_COLUMNS)
    else:
        prospects = pd.concat(frames, ignore_index=True)

    prospects = _collapse_multi_origin(prospects)
    prospects = _apply_mle(prospects)
    prospects = _ensure_columns(prospects)
    return prospects, outcomes


# ---------------------------------------------------------------------------
# loaders / converters
# ---------------------------------------------------------------------------
def _load_ncaa_safe(years: range, cache_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-year tolerant loader. Missing year parquets are skipped with a
    warning so a partial cache (e.g. only 2018 + 2023 in tests) doesn't
    short-circuit the whole cohort.
    """
    prospect_frames: list[pd.DataFrame] = []
    outcome_frames: list[pd.DataFrame] = []
    for y in years:
        p_path = cache_dir / f"prospects_{y}.parquet"
        o_path = cache_dir / f"nba_outcomes_{y}.parquet"
        if not p_path.exists():
            continue
        prospect_frames.append(pd.read_parquet(p_path))
        if o_path.exists():
            outcome_frames.append(pd.read_parquet(o_path))
    if prospect_frames:
        prospects = pd.concat(prospect_frames, ignore_index=True).copy()
        prospects["prospect_origin"] = ORIGIN_NCAA
        prospects["years_in_prior_league"] = 1
    else:
        logger.warning("no NCAA cache for years %s — using empty frame", years)
        prospects = pd.DataFrame(
            columns=list(PROSPECT_COLUMNS) + ["prospect_origin", "years_in_prior_league"]
        )
    if outcome_frames:
        outcomes = pd.concat(outcome_frames, ignore_index=True)
    else:
        outcomes = pd.DataFrame(
            columns=["br_slug", "career_bpm_5y", "career_ws_5y", "career_vorp_5y"]
        )
    return prospects, outcomes


def _seasons_for_years(years: range) -> list[str]:
    """For each draft year Y we look at seasons Y-2 / Y-1 (e.g. 2018 →
    ['2016-17', '2017-18']). This catches the most recent pre-draft year of
    international play while keeping the cohort tight.
    """
    out: set[str] = set()
    for y in years:
        for end in (y - 1, y):  # season that ENDS in y-1 or y
            start = end - 1
            out.add(f"{start}-{str(end)[-2:]}")
    return sorted(out)


def _gleague_to_prospects(raw: pd.DataFrame, years: range) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=UNIFIED_PROSPECT_COLUMNS)
    df = raw.copy()
    df["draft_year"] = df["season"].map(_season_to_draft_year)
    df = df[df["draft_year"].isin(list(years))]
    df["prospect_origin"] = ORIGIN_GLEAGUE
    df["years_in_prior_league"] = 1
    df["age_at_draft"] = pd.to_numeric(df["age"], errors="coerce") if "age" in df.columns else pd.NA
    # G-League volume columns aren't directly used; map percent stats over.
    return _to_unified(df)


def _euro_to_prospects(raw: pd.DataFrame, years: range) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame(columns=UNIFIED_PROSPECT_COLUMNS)
    df = raw.copy()
    df["draft_year"] = df["season"].map(_season_to_draft_year)
    df = df[df["draft_year"].isin(list(years))]
    df["prospect_origin"] = df["league"].map(LEAGUE_ORIGIN).fillna("OTHER_INTL").astype(str)
    df["years_in_prior_league"] = 1
    df["age_at_draft"] = pd.to_numeric(df["age"], errors="coerce") if "age" in df.columns else pd.NA
    return _to_unified(df)


def _to_unified(df: pd.DataFrame) -> pd.DataFrame:
    """Project a source frame onto the unified prospect schema.

    Missing columns (draft_pick, position, sos, recruit_rank_pct, wingspan,
    vertical) are filled with NaN — the feature builder will impute. Existing
    percent stats (ts_pct, usg_pct, trb_pct, ast_pct, stl_pct, blk_pct) are
    passed through and later scaled by the league's MLE.
    """
    out = pd.DataFrame()
    for col in UNIFIED_PROSPECT_COLUMNS:
        out[col] = df[col] if col in df.columns else pd.NA
    return out


def _collapse_multi_origin(prospects: pd.DataFrame) -> pd.DataFrame:
    """When a (br_slug, draft_year) appears in multiple sources, keep the
    most recent pre-draft season and record how many seasons we observed.
    NCAA wins ties because its schema is richer (sos, recruit, biometrics).
    """
    if prospects.empty:
        return prospects
    df = prospects.copy()
    # Priority: NCAA > GLEAGUE > EUROLEAGUE > everything else.
    priority = {ORIGIN_NCAA: 0, ORIGIN_GLEAGUE: 1, "EUROLEAGUE": 2}
    df["_prio"] = df["prospect_origin"].map(priority).fillna(3)
    df = df.sort_values(["br_slug", "draft_year", "_prio"]).reset_index(drop=True)
    counts = df.groupby(["br_slug", "draft_year"])["prospect_origin"].count().rename("_n")
    df = df.drop_duplicates(subset=["br_slug", "draft_year"], keep="first")
    df = df.merge(counts, on=["br_slug", "draft_year"], how="left")
    df["years_in_prior_league"] = df["_n"].fillna(1).astype(int)
    return df.drop(columns=["_prio", "_n"])


def _apply_mle(prospects: pd.DataFrame) -> pd.DataFrame:
    if prospects.empty:
        return prospects
    df = prospects.copy()
    origins = df["prospect_origin"].fillna(ORIGIN_NCAA).astype(str)
    mles = origins.map(lambda o: mle_for(o)["mle"])
    ranks = origins.map(lambda o: mle_for(o)["strength_rank"])
    df["prior_league_mle_score"] = pd.to_numeric(mles, errors="coerce").astype(float)
    df["prior_league_strength_rank"] = pd.to_numeric(ranks, errors="coerce").astype(int)

    # Scale volume-ish percent stats by the MLE multiplier so an EuroLeague
    # USG% of 25 isn't read by the model the same way as an NCAA 25. TS% is
    # NOT scaled — shooting efficiency travels league-to-league cleanly.
    for col in SCALED_STATS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce") * df["prior_league_mle_score"]
    return df


def _ensure_columns(prospects: pd.DataFrame) -> pd.DataFrame:
    df = prospects.copy()
    for col in UNIFIED_PROSPECT_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    df["prospect_origin"] = df["prospect_origin"].fillna(ORIGIN_NCAA).astype(str)
    df["years_in_prior_league"] = (
        pd.to_numeric(df["years_in_prior_league"], errors="coerce").fillna(1).astype(int)
    )
    df["prior_league_mle_score"] = pd.to_numeric(
        df["prior_league_mle_score"], errors="coerce"
    ).fillna(1.0)
    df["prior_league_strength_rank"] = (
        pd.to_numeric(df["prior_league_strength_rank"], errors="coerce").fillna(3).astype(int)
    )
    return df[UNIFIED_PROSPECT_COLUMNS].copy()


def _season_to_draft_year(season: object) -> int | float:
    """'2017-18' → 2018 (the draft happens in June after the season ends)."""
    if not isinstance(season, str) or "-" not in season:
        try:
            return int(season)  # type: ignore[call-overload, no-any-return]
        except (TypeError, ValueError):
            return float("nan")
    start, end = season.split("-", 1)
    century = int(start[:2]) * 100
    end_year = century + int(end)
    if end_year < int(start):
        end_year += 100
    return end_year
