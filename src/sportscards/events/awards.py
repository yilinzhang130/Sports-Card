"""NBA awards ingestor (MVP / ROY / DPOY / All-Star / All-NBA / HOF)."""

from __future__ import annotations

import logging
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


class LiveAwardsClient:
    def get_awards(self, season: str) -> list[AwardRow]:  # pragma: no cover
        # TODO: scrape Basketball-Reference awards page or nba_api leaders endpoint.
        raise NotImplementedError("LiveAwardsClient not implemented")


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
