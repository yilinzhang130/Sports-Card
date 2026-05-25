"""NBA awards ingestor (MVP / ROY / DPOY / All-Star / All-NBA / HOF)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent, PlayerEventType

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/events_cache/awards")

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
    def get_awards(self, season: str) -> list[AwardRow]:
        ...


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


def _write_cache(rows: list[AwardRow], season: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{season}.json"
    payload = [
        {"award_type": r.award_type, "season": r.season, "player_name": r.player_name}
        for r in rows
    ]
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def _resolve_player(session: Session, name: str) -> Player | None:
    stmt = select(Player).where(func.lower(Player.name) == name.strip().lower())
    return session.execute(stmt).scalars().first()


def ingest_awards(
    session: Session,
    *,
    client: AwardsClient,
    season: str,
    cache_dir: Path | None = None,
) -> int:
    rows = client.get_awards(season)
    _write_cache(rows, season, cache_dir or DEFAULT_CACHE_DIR)

    written = 0
    for row in rows:
        if row.award_type not in _VALID_AWARDS:
            logger.warning("unknown award_type %r — skipping", row.award_type)
            continue

        player = _resolve_player(session, row.player_name)
        if player is None:
            logger.warning("could not resolve award player by name: %s", row.player_name)
            continue

        event_dt = _season_end_date(row.season)
        exists = session.execute(
            select(PlayerEvent.event_id).where(
                PlayerEvent.player_id == player.player_id,
                PlayerEvent.event_type == row.award_type,
                PlayerEvent.event_date == event_dt,
            )
        ).first()
        if exists:
            continue

        session.add(
            PlayerEvent(
                player_id=player.player_id,
                event_type=row.award_type,
                event_date=event_dt,
                event_payload={"season": row.season},
            )
        )
        session.flush()
        written += 1

    session.commit()
    return written
