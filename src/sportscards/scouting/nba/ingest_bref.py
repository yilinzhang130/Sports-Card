"""Basketball-Reference ingestion for the PRISM scouting model.

Pulls draft classes (college advanced stats + draft slot) and 5-year NBA
outcomes (BPM, WS, VORP) for prospects 2010 onward, persisting to local
Parquet under ``data/scouting_cache/``.

Real network calls go through ``BRefClient`` and are gated behind the
``sportscards scouting ingest-nba`` CLI command. Tests inject a fake client
so the unit suite never touches the network.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/scouting_cache")
BREF_MIN_REQ_INTERVAL_S = 3.5  # ~17 req/min, under BR's 20/min cap

PROSPECT_COLUMNS = [
    "br_slug",
    "name",
    "draft_year",
    "draft_pick",
    "position",
    "age_at_draft",
    "trb_pct",
    "ast_pct",
    "stl_pct",
    "blk_pct",
    "usg_pct",
    "ts_pct",
    "sos",
    "recruit_rank_pct",
    "wingspan_in",
    "max_vert_in",
]

OUTCOME_COLUMNS = ["br_slug", "career_bpm_5y", "career_ws_5y", "career_vorp_5y"]

CURRENT_NCAA_COLUMNS = [
    *PROSPECT_COLUMNS,
    "class_year",          # "FR" | "SO" | "JR" | "SR" (or "" if unknown)
    "n_games_played",
    "prior_league",        # always "NCAA" for this ingestor
]

# Years until the player is *expected* to declare for the draft, per the
# scouting spec: FR=2, SO=1, JR=0/1, SR=0. Used to mark forward optionality;
# this is metadata only and is NOT fed to the model.
CLASS_TO_YEARS_UNTIL_DRAFT: dict[str, int] = {
    "FR": 2,
    "SO": 1,
    "JR": 0,
    "SR": 0,
    "": 0,
}
UNDERCLASSMEN: set[str] = {"FR", "SO"}


def expected_draft_year(season: str, class_year: str) -> int:
    """Given a season label like "2025-26" and a class year, return the
    draft year the player is expected to enter.

    A true freshman in 2025-26 (FR) would, under our convention, declare
    after 2 more seasons → 2028 draft. A senior declares this year → 2026.
    """
    end_year = _season_end_year(season)
    return end_year + CLASS_TO_YEARS_UNTIL_DRAFT.get(class_year.upper(), 0)


def _season_end_year(season: str) -> int:
    """'2025-26' -> 2026. '2099-00' wraps centuries correctly."""
    start_str, end_str = season.split("-")
    start = int(start_str)
    end_suffix = int(end_str)
    century = (start // 100) * 100
    end = century + end_suffix
    if end < start:
        end += 100
    return end


class BRefClient(Protocol):
    """Narrow protocol — only what the ingester needs.

    Implementations may wrap ``basketball_reference_scraper`` or any other
    source; the rest of the module never imports the library directly.
    """

    def get_draft_class(self, year: int) -> pd.DataFrame:
        """Return one row per drafted player with at least the columns:
        ``br_slug``, ``name``, ``draft_pick``, ``position``, ``age_at_draft``,
        ``trb_pct``, ``ast_pct``, ``stl_pct``, ``blk_pct``, ``usg_pct``,
        ``ts_pct``, ``sos``, ``recruit_rank_pct``. Missing columns may be
        filled with NaN.
        """
        ...

    def get_player_career_advanced(self, name: str, max_seasons: int = 5) -> pd.DataFrame:
        """Return up-to-``max_seasons`` rows of advanced NBA stats for one
        player (looked up by player NAME — Basketball-Reference's scraper API
        does not accept BR slugs), with at least ``BPM``, ``WS``, ``VORP``
        columns.
        """
        ...

    def get_current_ncaa_season(self, season: str) -> pd.DataFrame:
        """Return one row per current-season D-I player with the prospect
        columns (``br_slug``, ``name``, ``position``, ``age_at_draft``,
        per-100 advanced rates, ``sos``, ``recruit_rank_pct``) plus
        ``class_year`` ∈ {FR, SO, JR, SR} and ``n_games_played``.

        Forward-looking: ``draft_pick`` will not be present and is added by
        the ingester. ``season`` is in BR's "2025-26" form.
        """
        ...


@dataclass
class LiveBRefClient:
    """Production client that wraps ``basketball_reference_scraper``.

    Kept thin and isolated so it can be swapped or stubbed wholesale. The
    upstream library is known to break when BR's HTML changes; callers should
    expect ``RuntimeError`` and degrade gracefully.
    """

    sleep_s: float = BREF_MIN_REQ_INTERVAL_S

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_draft_class(self, year: int) -> pd.DataFrame:
        try:
            from basketball_reference_scraper.drafts import get_draft_class
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("basketball_reference_scraper not installed") from e
        time.sleep(self.sleep_s)
        df = get_draft_class(year)
        df = df.rename(columns=str.lower)
        if "player" in df.columns and "name" not in df.columns:
            df["name"] = df["player"]
        if "pk" in df.columns and "draft_pick" not in df.columns:
            df["draft_pick"] = pd.to_numeric(df["pk"], errors="coerce")
        df["draft_year"] = year
        return cast(pd.DataFrame, df)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_current_ncaa_season(self, season: str) -> pd.DataFrame:
        """Production current-season ingest is not wired against any concrete
        upstream source yet — the `basketball_reference_scraper` library does
        not expose D-I per-100 stats. Live usage requires a custom HTML
        scraper or sports-reference paid API; for now the live path raises
        so tests must inject a fake client.
        """
        raise RuntimeError(
            "LiveBRefClient.get_current_ncaa_season is not implemented; inject a "
            "concrete client for the desired upstream source (sports-reference, "
            "Bart Torvik, KenPom) or pre-stage a parquet under data/scouting_cache/."
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_player_career_advanced(self, name: str, max_seasons: int = 5) -> pd.DataFrame:
        """`basketball_reference_scraper.players.get_stats` expects a *player
        name* (e.g. "Luka Doncic"), not a BR slug. The ingester is responsible
        for passing the name column, not the slug.
        """
        try:
            from basketball_reference_scraper.players import get_stats
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("basketball_reference_scraper not installed") from e
        time.sleep(self.sleep_s)
        df = get_stats(name, stat_type="ADVANCED", playoffs=False, career=False)
        df = df.rename(columns=str.upper).head(max_seasons)
        return cast(pd.DataFrame, df)


def ingest_year(
    year: int,
    client: BRefClient,
    cache_dir: Path = CACHE_DIR,
) -> tuple[Path, Path]:
    """Pull one draft class + 5-yr NBA outcomes and write two Parquet files.

    Returns (prospects_path, outcomes_path).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw = client.get_draft_class(year)
    prospects = _normalize_prospects(raw, year)
    prospects_path = cache_dir / f"prospects_{year}.parquet"
    prospects.to_parquet(prospects_path, index=False)
    logger.info("wrote %d prospects → %s", len(prospects), prospects_path)

    outcomes: list[dict[str, float | str]] = []
    seen_slugs: set[str] = set()
    for row in prospects[["br_slug", "name"]].dropna(subset=["br_slug"]).itertuples(index=False):
        slug = str(row.br_slug)
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        try:
            adv = client.get_player_career_advanced(str(row.name), max_seasons=5)
        except Exception as e:  # pragma: no cover - network errors degrade
            logger.warning("career fetch failed for %s (%s): %s", row.name, slug, e)
            continue
        outcomes.append(_aggregate_outcome(slug, adv))

    outcomes_df = (
        pd.DataFrame(outcomes, columns=OUTCOME_COLUMNS)
        if outcomes
        else pd.DataFrame(columns=OUTCOME_COLUMNS)
    )
    outcomes_path = cache_dir / f"nba_outcomes_{year}.parquet"
    outcomes_df.to_parquet(outcomes_path, index=False)
    logger.info("wrote %d outcomes → %s", len(outcomes_df), outcomes_path)

    return prospects_path, outcomes_path


def ingest_current_ncaa_season(
    season: str,
    client: BRefClient,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    """Pull every D-I player's current-season stats and write one parquet.

    Forward-looking analogue of ``ingest_year``. Unlike the historical path,
    ``draft_pick`` does not yet exist for these players — the column is
    populated as NA and ``score_undrafted.score_current_class`` injects
    ``UNDRAFTED_SENTINEL`` at feature-build time.

    Output columns: ``CURRENT_NCAA_COLUMNS`` (the 16 prospect fields plus
    ``class_year``, ``n_games_played``, ``prior_league``). Each row's
    ``draft_year`` is the player's *expected* declaration year derived from
    ``class_year`` per ``CLASS_TO_YEARS_UNTIL_DRAFT``.

    Cache: ``data/scouting_cache/ncaa_current_{season}.parquet``.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = client.get_current_ncaa_season(season)
    df = _normalize_current_ncaa(raw, season)
    out = cache_dir / f"ncaa_current_{season}.parquet"
    df.to_parquet(out, index=False)
    logger.info("wrote %d NCAA current-season rows → %s", len(df), out)
    return out


def load_current_ncaa(season: str, cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """Read the parquet cached by ``ingest_current_ncaa_season``."""
    return pd.read_parquet(cache_dir / f"ncaa_current_{season}.parquet")


def _normalize_current_ncaa(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    df = raw.copy()
    if "br_slug" not in df.columns:
        df["br_slug"] = df.get("slug", df.get("name", pd.Series(dtype=str)))
    if "class_year" in df.columns:
        df["class_year"] = (
            df["class_year"].fillna("").astype(str).str.upper().str.strip()
        )
    else:
        df["class_year"] = ""
    # Per-row draft_year derives from each player's class — NOT a constant.
    df["draft_year"] = df["class_year"].apply(lambda c: expected_draft_year(season, c))
    if "prior_league" not in df.columns:
        df["prior_league"] = "NCAA"
    if "n_games_played" not in df.columns:
        df["n_games_played"] = pd.NA
    # draft_pick is unknown for current-season prospects.
    if "draft_pick" not in df.columns:
        df["draft_pick"] = pd.NA
    for col in CURRENT_NCAA_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[CURRENT_NCAA_COLUMNS].copy()


def load_cohort(years: range, cache_dir: Path = CACHE_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read previously-cached Parquet artifacts for the given draft years."""
    prospects = pd.concat(
        [pd.read_parquet(cache_dir / f"prospects_{y}.parquet") for y in years],
        ignore_index=True,
    )
    outcomes = pd.concat(
        [pd.read_parquet(cache_dir / f"nba_outcomes_{y}.parquet") for y in years],
        ignore_index=True,
    )
    return prospects, outcomes


def _normalize_prospects(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    df = raw.copy()
    if "br_slug" not in df.columns:
        df["br_slug"] = df.get("slug", df.get("name", pd.Series(dtype=str)))
    df["draft_year"] = year
    for col in PROSPECT_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[PROSPECT_COLUMNS].copy()


def _aggregate_outcome(slug: str, adv: pd.DataFrame) -> dict[str, float | str]:
    def _sum(col: str) -> float:
        if col not in adv.columns:
            return 0.0
        return float(pd.to_numeric(adv[col], errors="coerce").fillna(0.0).sum())

    return {
        "br_slug": slug,
        "career_bpm_5y": _sum("BPM"),
        "career_ws_5y": _sum("WS"),
        "career_vorp_5y": _sum("VORP"),
    }
