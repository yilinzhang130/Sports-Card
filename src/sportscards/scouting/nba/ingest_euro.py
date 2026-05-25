"""European-league ingestion for the PRISM scouting model.

Primary source: ``realgm.com`` international league pages (covers EuroLeague,
EuroCup, Liga ACB, LNB Pro A, Lega Basket Serie A, Australian NBL). If
realgm returns an empty result for a season, ``LiveEuroClient`` falls back to
``proballers.com``.

The Live client is intentionally minimal — production-grade scraping with
slug resolution and player-detail crawling is out of scope for v1. Tests
inject ``FakeEuroClient``.
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

CACHE_DIR = Path("data/scouting_cache/euro")
EURO_MIN_REQ_INTERVAL_S = 4.0  # realgm's robots.txt asks for a polite crawl

# Canonical league keys → realgm league IDs.
LEAGUE_IDS: dict[str, int] = {
    "euroleague": 1,
    "eurocup": 2,
    "acb": 5,
    "lnb": 12,
    "lba": 13,
    "nbl": 52,
}

# Map league key → prospect_origin value used by the unified cohort builder.
LEAGUE_ORIGIN: dict[str, str] = {
    "euroleague": "EUROLEAGUE",
    "eurocup": "OTHER_INTL",
    "acb": "LIGA_ACB",
    "lnb": "LNB",
    "lba": "LBA",
    "nbl": "NBL",
}

EURO_COLUMNS = [
    "br_slug",
    "name",
    "league",
    "season",
    "age",
    "position",
    "team",
    "gp",
    "mpg",
    "pts_per40",
    "reb_per40",
    "ast_per40",
    "stl_per40",
    "blk_per40",
    "ts_pct",
    "usg_pct",
    "trb_pct",
    "ast_pct",
    "stl_pct",
    "blk_pct",
]


class EuroClient(Protocol):
    def get_league_season(self, league: str, season: str) -> pd.DataFrame: ...


@dataclass
class LiveEuroClient:
    """realgm.com primary; proballers.com fallback."""

    sleep_s: float = EURO_MIN_REQ_INTERVAL_S
    user_agent: str = "sportscards-quant/1.0 (research; contact via repo)"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_league_season(self, league: str, season: str) -> pd.DataFrame:
        try:
            import requests
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("requests not installed") from e

        if league not in LEAGUE_IDS:
            raise ValueError(f"unknown league {league!r}; known: {sorted(LEAGUE_IDS)}")
        league_id = LEAGUE_IDS[league]

        headers = {"User-Agent": self.user_agent}
        realgm_url = (
            f"https://basketball.realgm.com/international/league/{league_id}"
            f"/{season}/stats/All/Per_40_Minutes/All/points/All/desc/1/Regular_Season"
        )
        time.sleep(self.sleep_s)
        try:
            html = requests.get(realgm_url, headers=headers, timeout=30).text
            df = _parse_realgm(html)
            if not df.empty:
                df["league"] = league
                df["season"] = season
                return cast(pd.DataFrame, df)
        except Exception as e:  # noqa: BLE001 - want to fall back on any failure
            logger.warning("realgm fetch failed for %s/%s: %s", league, season, e)

        # Fallback: proballers.com — different URL shape, looser schema.
        proballers_url = f"https://www.proballers.com/basketball/league/{league}/{season}"
        time.sleep(self.sleep_s)
        html = requests.get(proballers_url, headers=headers, timeout=30).text
        df = _parse_proballers(html)
        df["league"] = league
        df["season"] = season
        return cast(pd.DataFrame, df)


class FakeEuroClient:
    """Drop-in test replacement."""

    def __init__(self, by_key: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._by_key = by_key

    def get_league_season(self, league: str, season: str) -> pd.DataFrame:
        return self._by_key.get((league, season), pd.DataFrame(columns=EURO_COLUMNS)).copy()


def ingest_league_season(
    league: str,
    season: str,
    client: EuroClient,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = client.get_league_season(league, season)
    normalized = _normalize(raw, league, season)
    out = cache_dir / f"{league}_{season}.parquet"
    normalized.to_parquet(out, index=False)
    logger.info("wrote %d %s rows → %s", len(normalized), league, out)
    return out


def load_euro_cohort(
    leagues: list[str], seasons: list[str], cache_dir: Path = CACHE_DIR
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for lg in leagues:
        for s in seasons:
            path = cache_dir / f"{lg}_{s}.parquet"
            if not path.exists():
                logger.warning("no Euro cache for %s/%s — skipping", lg, s)
                continue
            frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=EURO_COLUMNS)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------
def _parse_realgm(html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    df = tables[0].rename(columns=str.lower)
    if "player" in df.columns:
        df = df.rename(columns={"player": "name"})
    return df


def _parse_proballers(html: str) -> pd.DataFrame:
    try:
        tables = pd.read_html(io.StringIO(html))
    except ValueError:
        return pd.DataFrame()
    if not tables:
        return pd.DataFrame()
    df = tables[0].rename(columns=str.lower)
    if "player" in df.columns:
        df = df.rename(columns={"player": "name"})
    return df


def _normalize(raw: pd.DataFrame, league: str, season: str) -> pd.DataFrame:
    df = raw.copy()
    if "br_slug" not in df.columns:
        df["br_slug"] = df.get("slug", df.get("name", pd.Series(dtype=str)))
    df["league"] = league
    df["season"] = season
    for col in EURO_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[EURO_COLUMNS].copy()
