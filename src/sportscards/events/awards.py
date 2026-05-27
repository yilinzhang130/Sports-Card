"""NBA awards ingestor (MVP / ROY / DPOY / All-Star / All-NBA / HOF)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from sportscards.db.models import PlayerEvent, PlayerEventType
from sportscards.events._common import (
    existing_event_keys,
    resolve_player_by_name,
    write_json_cache,
)

logger = logging.getLogger(__name__)

_VALID_AWARDS = {
    PlayerEventType.MVP.value,
    PlayerEventType.ROY.value,
    PlayerEventType.DPOY.value,
    PlayerEventType.ALL_STAR.value,
    PlayerEventType.ALL_NBA_1ST.value,
    PlayerEventType.ALL_NBA_2ND.value,
    PlayerEventType.ALL_NBA_3RD.value,
    PlayerEventType.HOF.value,
}


@dataclass(frozen=True)
class AwardRow:
    award_type: str
    season: str  # e.g. "2024-25"
    player_name: str


class AwardsClient(Protocol):
    def get_awards(self, season: str) -> list[AwardRow]: ...


_BR_AWARDS_URL = "https://www.basketball-reference.com/awards/awards_{end_year}.html"
_BR_THROTTLE_SECONDS = 3.0


def _season_end_year(season: str) -> int:
    if "-" in season:
        start_str, _ = season.split("-", 1)
        return int(start_str) + 1
    return int(season)


def _first_player_cell(table: object) -> str | None:
    """Return the player-name text in the first data row of a BR table."""
    from bs4 import Tag

    if not isinstance(table, Tag):
        return None
    body = table.find("tbody")
    if body is None:
        return None
    first = body.find("tr")
    if first is None:
        return None
    cell = first.find("td", {"data-stat": "player"}) or first.find("th", {"data-stat": "player"})
    if cell is None:
        return None
    return cell.get_text(strip=True) or None


def _all_player_cells(table: object) -> list[str]:
    """Return every player-name text in a BR table body."""
    from bs4 import Tag

    if not isinstance(table, Tag):
        return []
    out: list[str] = []
    body = table.find("tbody")
    if body is None:
        return out
    for tr in body.find_all("tr"):
        cell = tr.find("td", {"data-stat": "player"}) or tr.find("th", {"data-stat": "player"})
        if cell is None:
            continue
        text = cell.get_text(strip=True)
        if text:
            out.append(text)
    return out


_BR_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)


def _unwrap_br_comments(html: str) -> str:
    """basketball-reference.com wraps secondary tables in HTML comments to
    discourage casual scraping. Strip the comment markers so BeautifulSoup
    sees those tables alongside the visible ones.
    """
    return _BR_COMMENT_RE.sub(lambda m: m.group(1), html)


# BR encodes tier as "1T"/"2T"/"3T" on the live page; older fixture syntax
# used "1st"/"2nd"/"3rd". Accept both so unit fixtures and the live page work.
_ALL_NBA_TIER_MAP: dict[str, str | None] = {
    "1T": PlayerEventType.ALL_NBA_1ST.value,
    "2T": PlayerEventType.ALL_NBA_2ND.value,
    "3T": PlayerEventType.ALL_NBA_3RD.value,
    "1st": PlayerEventType.ALL_NBA_1ST.value,
    "2nd": PlayerEventType.ALL_NBA_2ND.value,
    "3rd": PlayerEventType.ALL_NBA_3RD.value,
    "ORV": None,
}


def parse_br_awards(html: str, season: str) -> list[AwardRow]:
    """Parse a basketball-reference season awards page into ``AwardRow``s.

    Handles BR's commented-out-table convention so both the always-visible
    tables (mvp, roy) and the comment-wrapped ones (dpoy, leading_all_nba,
    leading_all_defense) parse uniformly.
    """
    from bs4 import BeautifulSoup

    unwrapped = _unwrap_br_comments(html)
    soup = BeautifulSoup(unwrapped, "html.parser")
    out: list[AwardRow] = []

    single_winner_tables = {
        "mvp": PlayerEventType.MVP.value,
        "roy": PlayerEventType.ROY.value,
        "dpoy": PlayerEventType.DPOY.value,
    }
    for table_id, award_type in single_winner_tables.items():
        table = soup.find("table", id=table_id)
        winner = _first_player_cell(table)
        if winner:
            out.append(AwardRow(award_type=award_type, season=season, player_name=winner))

    # All-NBA: each row carries the tier ("1st"/"2nd"/"3rd") in
    # data-stat="all_nba_team".
    all_nba = soup.find("table", id="leading_all_nba")
    from bs4 import Tag

    if isinstance(all_nba, Tag):
        body = all_nba.find("tbody")
        if isinstance(body, Tag):
            for tr in body.find_all("tr"):
                tier_cell = tr.find(attrs={"data-stat": "all_nba_team"})
                player_cell = tr.find(attrs={"data-stat": "player"})
                if tier_cell is None or player_cell is None:
                    continue
                tier_key = tier_cell.get_text(strip=True)
                tier_award = _ALL_NBA_TIER_MAP.get(tier_key)
                if not tier_award:
                    continue
                name = player_cell.get_text(strip=True)
                if name:
                    out.append(AwardRow(award_type=tier_award, season=season, player_name=name))

    return out


class LiveAwardsClient:
    """Scrapes basketball-reference.com for season awards.

    One request per season, throttled (BR rate-limits >20/min). Successful
    fetches are cached to disk so re-runs against the same season skip the
    network entirely.
    """

    def __init__(
        self,
        *,
        cache_dir: Path = Path("data/events_cache/awards/html"),
        throttle_seconds: float = _BR_THROTTLE_SECONDS,
    ) -> None:
        self._cache_dir = cache_dir
        self._throttle_seconds = throttle_seconds

    def get_awards(self, season: str) -> list[AwardRow]:
        import time

        from sportscards.events._http import fetch_html

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_dir / f"{season}.html"

        if cache_path.exists() and cache_path.stat().st_size > 0:
            html = cache_path.read_text(encoding="utf-8")
        else:
            time.sleep(self._throttle_seconds)
            url = _BR_AWARDS_URL.format(end_year=_season_end_year(season))
            html = fetch_html(url)
            cache_path.write_text(html, encoding="utf-8")

        return parse_br_awards(html, season=season)


def _season_end_date(season: str) -> datetime:
    """Map 'YYYY-YY' (or 'YYYY') to June 30 of the latter year."""
    if "-" in season:
        start_str, _ = season.split("-", 1)
        end_year = int(start_str) + 1
    else:
        end_year = int(season)
    return datetime(end_year, 6, 30)


def ingest_awards(
    session: Session,
    *,
    client: AwardsClient,
    season: str,
    cache_dir: Path | None = None,
) -> int:
    rows = client.get_awards(season)
    write_json_cache(
        [
            {"award_type": r.award_type, "season": r.season, "player_name": r.player_name}
            for r in rows
        ],
        source="awards",
        as_of=season,
        cache_dir=cache_dir,
    )

    # Resolve + classify once, then dedupe with a single SELECT.
    candidates: list[tuple[int, str, datetime, AwardRow]] = []
    for row in rows:
        if row.award_type not in _VALID_AWARDS:
            logger.warning("unknown award_type %r — skipping", row.award_type)
            continue
        pid = resolve_player_by_name(session, row.player_name)
        if pid is None:
            continue
        event_dt = _season_end_date(row.season)
        candidates.append((pid, row.award_type, event_dt, row))

    if not candidates:
        session.commit()
        return 0

    player_ids = {c[0] for c in candidates}
    types = {c[1] for c in candidates}
    dates = [c[2] for c in candidates]
    existing = existing_event_keys(
        session,
        player_ids=list(player_ids),
        event_types=list(types),
        date_range=(min(dates), max(dates)),
    )

    written = 0
    for pid, etype, event_dt, row in candidates:
        key = (pid, etype, event_dt)
        if key in existing:
            continue
        session.add(
            PlayerEvent(
                player_id=pid,
                event_type=etype,
                event_date=event_dt,
                event_payload={"season": row.season},
            )
        )
        existing.add(key)
        written += 1

    session.commit()
    return written
