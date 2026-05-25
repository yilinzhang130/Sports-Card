"""G-League ingestion for the PRISM scouting model.

Pulls per-game + advanced stats from basketball-reference.com/gleague/
season pages. Same cache / Protocol pattern as ``ingest_bref`` so the rest of
the pipeline can swap data sources without learning new shapes.

Network calls only happen behind ``LiveGLeagueClient``; tests inject
``FakeGLeagueClient``.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/scouting_cache/gleague")
GLEAGUE_MIN_REQ_INTERVAL_S = 3.5  # mirror BR rate-limit policy

# Columns persisted per season. Mirrors the NCAA prospect schema where it
# applies (br_slug, name, age, position, percent-based advanced stats) and
# adds G-League-specific volume markers we may later use to refit MLE.
GLEAGUE_COLUMNS = [
    "br_slug",
    "name",
    "season",
    "age",
    "position",
    "team",
    "gp",
    "mpg",
    "pts_per36",
    "reb_per36",
    "ast_per36",
    "stl_per36",
    "blk_per36",
    "ts_pct",
    "usg_pct",
    "trb_pct",
    "ast_pct",
    "stl_pct",
    "blk_pct",
]


class GLeagueClient(Protocol):
    """Narrow protocol — only what the ingester needs."""

    def get_season_stats(self, season: str) -> pd.DataFrame:
        """Return one row per G-League player with the columns in
        ``GLEAGUE_COLUMNS`` (missing columns may be NaN).
        """
        ...


@dataclass
class LiveGLeagueClient:
    """Production client. Pulls server-rendered HTML and parses with
    ``pandas.read_html``. The basketball_reference_scraper library does NOT
    cover the G-League, so this client uses ``requests`` directly.

    BR's HTML occasionally drifts; expect ``RuntimeError`` and degrade.
    """

    sleep_s: float = GLEAGUE_MIN_REQ_INTERVAL_S
    user_agent: str = "sportscards-quant/1.0 (research; contact via repo)"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_season_stats(self, season: str) -> pd.DataFrame:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("requests not installed") from e

        end_year = _season_end_year(season)
        per_game_url = (
            f"https://www.basketball-reference.com/gleague/years/NBA_G_{end_year}_per_game.html"
        )
        adv_url = (
            f"https://www.basketball-reference.com/gleague/years/NBA_G_{end_year}_advanced.html"
        )
        headers = {"User-Agent": self.user_agent}

        time.sleep(self.sleep_s)
        per_game_html = requests.get(per_game_url, headers=headers, timeout=30).text
        time.sleep(self.sleep_s)
        adv_html = requests.get(adv_url, headers=headers, timeout=30).text

        per_game = _first_stats_table(per_game_html)
        adv = _first_stats_table(adv_html)
        merged = _merge_per_game_and_advanced(per_game, adv)
        merged["season"] = season
        return cast(pd.DataFrame, merged)


class FakeGLeagueClient:
    """Drop-in test replacement — never touches the network."""

    def __init__(self, by_season: dict[str, pd.DataFrame]) -> None:
        self._by_season = by_season

    def get_season_stats(self, season: str) -> pd.DataFrame:
        return self._by_season.get(season, pd.DataFrame(columns=GLEAGUE_COLUMNS)).copy()


def ingest_season(
    season: str,
    client: GLeagueClient,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    """Pull one G-League season and persist to Parquet."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = client.get_season_stats(season)
    normalized = _normalize(raw, season)
    out = cache_dir / f"{season}.parquet"
    normalized.to_parquet(out, index=False)
    logger.info("wrote %d G-League rows → %s", len(normalized), out)
    return out


def load_gleague_cohort(seasons: list[str], cache_dir: Path = CACHE_DIR) -> pd.DataFrame:
    """Read previously-cached G-League parquets and concatenate."""
    frames: list[pd.DataFrame] = []
    for s in seasons:
        path = cache_dir / f"{s}.parquet"
        if not path.exists():
            logger.warning("no G-League cache for %s — skipping", s)
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=GLEAGUE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _season_end_year(season: str) -> int:
    """'2017-18' -> 2018. Accepts 'YYYY-YY' or 'YYYY'."""
    if "-" in season:
        start, end = season.split("-", 1)
        # 2017-18 → 2018; 1999-00 → 2000
        century = int(start[:2]) * 100
        end_year = century + int(end)
        if end_year < int(start):
            end_year += 100
        return end_year
    return int(season)


def _first_stats_table(html: str) -> pd.DataFrame:
    """BR pages bury the stats table in HTML comments. Try plain read_html
    first; if that finds nothing, strip the comment markers and retry.
    """
    try:
        tables = pd.read_html(io.StringIO(html))
        if tables:
            return tables[0]
    except ValueError:
        pass
    stripped = html.replace("<!--", "").replace("-->", "")
    tables = pd.read_html(io.StringIO(stripped))
    if not tables:
        raise RuntimeError("could not parse stats table from G-League page")
    return tables[0]


def _merge_per_game_and_advanced(per_game: pd.DataFrame, adv: pd.DataFrame) -> pd.DataFrame:
    """Best-effort join on player name. BR's slug isn't in the rendered HTML
    so we use name + team as the join key; collisions are extremely rare at
    G-League volume.
    """
    per_game = per_game.rename(columns=str.lower)
    adv = adv.rename(columns=str.lower)
    if "player" in per_game.columns:
        per_game = per_game.rename(columns={"player": "name"})
    if "player" in adv.columns:
        adv = adv.rename(columns={"player": "name"})
    join_cols = [c for c in ("name", "team", "tm") if c in per_game.columns and c in adv.columns]
    if not join_cols:
        return per_game
    return per_game.merge(adv, on=join_cols, how="left", suffixes=("", "_adv"))


def _normalize(raw: pd.DataFrame, season: str) -> pd.DataFrame:
    df = raw.copy()
    if "br_slug" not in df.columns:
        df["br_slug"] = df.get("slug", df.get("name", pd.Series(dtype=str)))
    df["season"] = season
    for col in GLEAGUE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[GLEAGUE_COLUMNS].copy()
