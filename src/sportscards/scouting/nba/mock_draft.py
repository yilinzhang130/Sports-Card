"""Mock-draft scraping + cross-source consensus aggregation.

Replaces ``draft_pick`` as the consensus baseline for **undrafted** prospects:
score_undrafted.py subtracts the mock-consensus percentile from the model
pairwise percentile to compute stardom premium.

Network calls go through ``MockDraftClient`` (a Protocol) so unit tests can
inject fakes; the ``LiveMockDraftClient`` is intentionally stubbed and not
required at test time. Parquet snapshots are dated so historical scoring can
be replayed for backtests ("would we have flagged Wemby in late 2022?").

Aggregation rules
-----------------
* `consensus_rank` = **median** rank across sources (robust to outliers).
* A player must appear in ≥ 3 sources to be aggregated; otherwise dropped.
* Players outside a source's published top-N are treated as MISSING, not
  pushed to a sentinel rank — sentinels would drag the median.
* `rank_dispersion` = interquartile range of ranks across the sources that
  ranked the player.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from sportscards.scouting.nba.ingest_bref import CACHE_DIR

logger = logging.getLogger(__name__)

MOCK_DRAFT_DIR = CACHE_DIR / "mock_drafts"
SOURCES: tuple[str, ...] = ("espn", "tankathon", "nbadraft_net", "the_ringer")
MIN_SOURCES_FOR_CONSENSUS = 3

MOCK_COLUMNS = ["source", "draft_year", "fetched_at", "rank", "player_name", "br_slug"]


class MockDraftClient(Protocol):
    """Narrow protocol; only what the aggregator needs."""

    def fetch(self, source: str, draft_year: int) -> pd.DataFrame:
        """Return one row per ranked player for ``source`` and ``draft_year``.

        Required columns: ``source, draft_year, fetched_at, rank,
        player_name, br_slug``. ``br_slug`` may be empty/NA — the
        aggregator falls back to a name-based key.
        """
        ...


@dataclass
class LiveMockDraftClient:
    """Stubbed live client.

    Each public source needs its own bespoke HTML/JSON parser; those parsers
    are out of scope for this PR (the consensus aggregator and forward
    scorer are wired against the Protocol and tested with fakes). When the
    individual scrapers land, fill in ``fetch`` to dispatch on ``source``.
    """

    def fetch(self, source: str, draft_year: int) -> pd.DataFrame:
        raise RuntimeError(
            f"LiveMockDraftClient.fetch not implemented for source={source!r}; "
            "inject a concrete client or pre-stage parquet snapshots under "
            f"{MOCK_DRAFT_DIR}/."
        )


def refresh_mock_drafts(
    draft_year: int,
    client: MockDraftClient,
    sources: tuple[str, ...] = SOURCES,
    cache_dir: Path = MOCK_DRAFT_DIR,
    today: date | None = None,
) -> list[Path]:
    """Pull each source for ``draft_year`` and persist dated parquet snapshots.

    Skips sources that raise (logged), so a partial refresh still produces
    something. Returns the list of files actually written.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    snap_date = today or date.today()
    written: list[Path] = []
    for source in sources:
        try:
            df = client.fetch(source, draft_year)
        except Exception as e:  # pragma: no cover - networking errors are tolerated
            logger.warning("mock-draft fetch failed for %s/%d: %s", source, draft_year, e)
            continue
        df = _normalize_mock_frame(df, source=source, draft_year=draft_year)
        out = cache_dir / f"{source}_{draft_year}_{snap_date.isoformat()}.parquet"
        df.to_parquet(out, index=False)
        logger.info("wrote %d %s mock rows → %s", len(df), source, out)
        written.append(out)
    return written


def _normalize_mock_frame(df: pd.DataFrame, source: str, draft_year: int) -> pd.DataFrame:
    out = df.copy()
    out["source"] = source
    out["draft_year"] = draft_year
    if "fetched_at" not in out.columns:
        out["fetched_at"] = pd.Timestamp.now(tz="UTC")
    for col in MOCK_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA
    out["rank"] = pd.to_numeric(out["rank"], errors="coerce")
    out["player_name"] = out["player_name"].astype(str)
    out["br_slug"] = out["br_slug"].fillna("").astype(str)
    return out[MOCK_COLUMNS].copy()


def aggregate_consensus_rank(
    draft_year: int,
    as_of: date,
    cache_dir: Path = MOCK_DRAFT_DIR,
    sources: tuple[str, ...] = SOURCES,
    min_sources: int = MIN_SOURCES_FOR_CONSENSUS,
) -> pd.DataFrame:
    """Aggregate the latest snapshot ≤ ``as_of`` from each source.

    Returns one row per player with columns:
    ``br_slug, player_name, consensus_rank, sources_count, rank_dispersion``.

    Players with fewer than ``min_sources`` rankings are dropped.
    """
    frames: list[pd.DataFrame] = []
    for source in sources:
        snap = _latest_snapshot(cache_dir, source, draft_year, as_of)
        if snap is None:
            continue
        df = pd.read_parquet(snap)
        # Players are joined on br_slug when present, name when not. We don't
        # synthesise br_slug here — name-only rows still get aggregated.
        df["join_key"] = df.apply(
            lambda r: str(r["br_slug"]) if r["br_slug"] else _name_to_key(str(r["player_name"])),
            axis=1,
        )
        frames.append(df)
    if not frames:
        return pd.DataFrame(
            columns=["br_slug", "player_name", "consensus_rank", "sources_count", "rank_dispersion"]
        )

    pooled = pd.concat(frames, ignore_index=True)
    # Aggregate per (join_key); preserve the most common br_slug + display name.
    grouped = pooled.groupby("join_key", sort=False)
    agg = grouped.agg(
        consensus_rank=("rank", "median"),
        sources_count=("rank", "size"),
        rank_dispersion=("rank", _iqr),
        br_slug=("br_slug", _first_nonempty),
        player_name=("player_name", "first"),
    ).reset_index(drop=True)

    agg = agg[agg["sources_count"] >= min_sources].copy()
    return agg[
        ["br_slug", "player_name", "consensus_rank", "sources_count", "rank_dispersion"]
    ].reset_index(drop=True)


def _latest_snapshot(
    cache_dir: Path,
    source: str,
    draft_year: int,
    as_of: date,
) -> Path | None:
    """Return the freshest snapshot for ``source`` with date ≤ ``as_of``."""
    if not cache_dir.exists():
        return None
    prefix = f"{source}_{draft_year}_"
    candidates: list[tuple[date, Path]] = []
    for p in cache_dir.glob(f"{prefix}*.parquet"):
        date_str = p.stem.removeprefix(prefix)
        try:
            d = date.fromisoformat(date_str)
        except ValueError:
            continue
        if d <= as_of:
            candidates.append((d, p))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0])
    return candidates[-1][1]


def _iqr(s: pd.Series) -> float:
    arr = np.asarray(s.dropna(), dtype=float)
    if arr.size < 2:
        return 0.0
    q1, q3 = np.percentile(arr, [25, 75])
    return float(q3 - q1)


def _first_nonempty(s: pd.Series) -> str:
    for v in s:
        if isinstance(v, str) and v:
            return v
    return ""


def _name_to_key(name: str) -> str:
    """Deterministic lowercase-alnum key for name-based joining when slug missing."""
    return "".join(ch for ch in name.lower() if ch.isalnum())
