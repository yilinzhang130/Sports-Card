"""NBA Draft Combine ingestion for the PRISM scouting model.

Pulls anthropometric and athletic-testing measurements from the NBA's
pre-draft combine, persisting one Parquet per draft year to
``data/scouting_cache/combine/``.

Source: NBA's ``stats.nba.com/stats/draftcombinestats`` endpoint via the
``nba_api`` library. (Basketball-Reference does *not* host combine data;
nba.com is the only systematic free source.) The library encapsulates the
specific User-Agent / Referer / x-nba-stats-* headers and connection
retries that the raw endpoint requires.

Coverage caveat: combine attendance is *voluntary*. Roughly 60-70% of any
draft class participates, and pre-2014 anthropometric coverage in particular
is uneven. Downstream consumers MUST treat the ``has_combine_data`` flag as
load-bearing — a missing row is the norm, not an error.

Tests inject ``FakeCombineClient``; the live scraper is only exercised when
``sportscards scouting ingest-combine`` is run from the CLI.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol, cast

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/scouting_cache/combine")
NBA_API_MIN_REQ_INTERVAL_S = 1.0  # stats.nba.com is generous; 1s is plenty

# Map nba_api column names → our schema. Anything not in this map is dropped
# (we don't need the 30+ shooting-drill columns for the model).
_NBA_API_COLUMN_MAP: dict[str, str] = {
    "PLAYER_NAME": "name",
    "HEIGHT_WO_SHOES": "height_no_shoes",
    "HEIGHT_W_SHOES": "height_with_shoes",
    "WEIGHT": "weight",
    "WINGSPAN": "wingspan",
    "STANDING_REACH": "standing_reach",
    "BODY_FAT_PCT": "body_fat_pct",
    "HAND_LENGTH": "hand_length",
    "HAND_WIDTH": "hand_width",
    "STANDING_VERTICAL_LEAP": "standing_vertical",
    "MAX_VERTICAL_LEAP": "max_vertical",
    "LANE_AGILITY_TIME": "lane_agility_time",
    "THREE_QUARTER_SPRINT": "three_quarter_sprint",
    "BENCH_PRESS": "bench_press",
}

COMBINE_COLUMNS = [
    "br_slug",
    "name",
    "draft_year",
    "height_no_shoes",
    "height_with_shoes",
    "weight",
    "wingspan",
    "standing_reach",
    "body_fat_pct",
    "hand_length",
    "hand_width",
    "standing_vertical",
    "max_vertical",
    "lane_agility_time",
    "three_quarter_sprint",
    "bench_press",
]

class CombineClient(Protocol):
    """Narrow protocol — only the call the ingester needs."""

    def get_combine(self, year: int) -> pd.DataFrame:
        """Return one row per combine participant for ``year``.

        Required columns: ``br_slug``, ``name``. Any other physical /
        athletic-testing column may be present or NaN; the normalizer fills
        the rest with NaN.
        """
        ...


@dataclass
class LiveCombineClient:
    """Production client that pulls one year of combine data via ``nba_api``.

    ``stats.nba.com`` indexes the combine by ``season_all_time``, which is
    the season *after* the draft year (e.g. the 2023 draft class lives under
    ``"2023-24"``). Coverage starts at 2000 but is sparse anthropometrically
    before 2014. The library handles required headers + retries; we add a
    small inter-request sleep on top to be a good citizen.

    ``br_slug`` is *synthesized* as the lowercase, hyphen-joined player name
    because the NBA endpoint exposes no BR slug. This matches the fallback
    in ``ingest_bref._normalize_prospects`` and is a best-effort join key.
    """

    sleep_s: float = NBA_API_MIN_REQ_INTERVAL_S
    timeout: int = 60

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_combine(self, year: int) -> pd.DataFrame:  # pragma: no cover - network
        try:
            from nba_api.stats.endpoints import DraftCombineStats
        except ImportError as e:
            raise RuntimeError(
                "nba_api not installed — `pip install 'sportscards-quant[scouting-live]'`"
            ) from e

        time.sleep(self.sleep_s)
        season = f"{year}-{str(year + 1)[-2:]}"  # 2023 → "2023-24"
        raw = DraftCombineStats(season_all_time=season, timeout=self.timeout).get_data_frames()[0]
        if raw.empty:
            raise RuntimeError(f"DraftCombineStats returned no rows for {season}")

        df = raw.rename(columns=_NBA_API_COLUMN_MAP)
        df["draft_year"] = year
        df["br_slug"] = (
            df.get("name", pd.Series(dtype=str))
            .astype(str)
            .str.lower()
            .str.replace(r"\s+", "-", regex=True)
            .str.replace(r"[^a-z0-9-]", "", regex=True)
        )
        return cast(pd.DataFrame, df)


def ingest_year(
    year: int,
    client: CombineClient,
    cache_dir: Path = CACHE_DIR,
) -> Path:
    """Pull one combine class and write a Parquet file. Returns the path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = client.get_combine(year)
    df = _normalize(raw, year)
    out = cache_dir / f"{year}.parquet"
    df.to_parquet(out, index=False)
    logger.info("wrote %d combine rows → %s", len(df), out)
    return out


def load_combine_cohort(
    years: Iterable[int], cache_dir: Path = CACHE_DIR
) -> pd.DataFrame:
    """Read previously-cached combine Parquet artifacts for the given years.

    Years with no cache file are silently skipped — combine attendance and
    BR coverage are both spotty, so a missing year is expected, not fatal.
    """
    frames: list[pd.DataFrame] = []
    for y in years:
        path = cache_dir / f"{y}.parquet"
        if not path.exists():
            logger.info("no combine cache for %d — skipping", y)
            continue
        frames.append(pd.read_parquet(path))
    if not frames:
        return pd.DataFrame(columns=COMBINE_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def _normalize(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    df = raw.copy()
    df["draft_year"] = year
    if "br_slug" not in df.columns:
        df["br_slug"] = df.get("name", pd.Series(dtype=str)).astype(str)
    for col in COMBINE_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    # Numeric coercion — BR sometimes ships heights as "6-7" strings; convert
    # those to inches. Everything else is already numeric on the page.
    for col in (
        "height_no_shoes",
        "height_with_shoes",
        "wingspan",
        "standing_reach",
        "hand_length",
    ):
        df[col] = df[col].apply(_parse_height_inches)
    for col in (
        "weight",
        "body_fat_pct",
        "hand_width",
        "standing_vertical",
        "max_vertical",
        "lane_agility_time",
        "three_quarter_sprint",
        "bench_press",
    ):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[COMBINE_COLUMNS].copy()


def _parse_height_inches(val: object) -> float:
    """Accept either numeric inches (already parsed) or a "6-7.25" string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return float("nan")
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "--"}:
        return float("nan")
    if "-" in s:
        try:
            feet, inches = s.split("-", 1)
            return float(feet) * 12.0 + float(inches)
        except ValueError:
            return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")
