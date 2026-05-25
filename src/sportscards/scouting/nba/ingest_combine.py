"""NBA Draft Combine ingestion for the PRISM scouting model.

Pulls anthropometric and athletic-testing measurements from the NBA's
pre-draft combine, persisting one Parquet per draft year to
``data/scouting_cache/combine/``.

Source preference:
    1. ``basketball-reference.com/draft/NBA_<year>_combine.html`` —
       server-rendered, parseable with pandas + BeautifulSoup. Primary source.
    2. ``nba.com/draft/combine`` — JS-heavy SPA, used only if BR is missing
       a year. (Not implemented here; pre-2014 coverage is sparse upstream.)

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
BREF_MIN_REQ_INTERVAL_S = 3.5  # match ingest_bref pacing — same upstream host

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

# Header strings used by basketball-reference.com on the combine HTML page.
# Keep this map close to the parser so a BR rename only requires one edit.
_BREF_HEADER_MAP: dict[str, str] = {
    "player": "name",
    "height (no shoes)": "height_no_shoes",
    "height (with shoes)": "height_with_shoes",
    "weight (lbs)": "weight",
    "wingspan": "wingspan",
    "standing reach": "standing_reach",
    "body fat %": "body_fat_pct",
    "hand (length)": "hand_length",
    "hand (width)": "hand_width",
    "no step vert": "standing_vertical",
    "max vert": "max_vertical",
    "lane agility": "lane_agility_time",
    "shuttle run": "three_quarter_sprint",  # BR sometimes labels this column "shuttle run"
    "3/4 court sprint": "three_quarter_sprint",
    "bench": "bench_press",
}


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
    """Production client that pulls the BR combine table for one year.

    Kept thin and isolated so it can be swapped or stubbed wholesale. BR's
    HTML occasionally changes column headers; the client raises and lets the
    caller decide whether to skip the year.
    """

    sleep_s: float = BREF_MIN_REQ_INTERVAL_S

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=30))
    def get_combine(self, year: int) -> pd.DataFrame:  # pragma: no cover - network
        import requests

        url = f"https://www.basketball-reference.com/draft/NBA_{year}_combine.html"
        time.sleep(self.sleep_s)
        resp = requests.get(url, timeout=30, headers={"User-Agent": "sportscards-quant/0.1"})
        resp.raise_for_status()

        # pandas.read_html handles the combine table directly.
        tables = pd.read_html(resp.text)
        if not tables:
            raise RuntimeError(f"no tables found at {url}")
        df = tables[0]

        # BR uses MultiIndex headers on combine pages — flatten to the inner
        # level (e.g. ("Anthro", "Wingspan") → "Wingspan").
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [str(c[-1]) for c in df.columns]

        df.columns = [str(c).strip().lower() for c in df.columns]
        renamed = {col: _BREF_HEADER_MAP[col] for col in df.columns if col in _BREF_HEADER_MAP}
        df = df.rename(columns=renamed)

        # Drop interleaved header rows (BR repeats the header every ~20 rows).
        if "name" in df.columns:
            df = df[df["name"].astype(str).str.lower() != "player"].copy()

        # Slug derivation — BR doesn't put the slug in the combine HTML, so
        # we synthesize the lowercase-hyphenated name as a best-effort key.
        # This matches what the ingest_bref normalizer falls back to for
        # prospects whose draft row also lacks a slug.
        df["br_slug"] = (
            df.get("name", pd.Series(dtype=str)).astype(str).str.lower().str.replace(r"\s+", "-", regex=True)
        )
        df["draft_year"] = year
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
