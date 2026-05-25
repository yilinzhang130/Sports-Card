"""NBA schedule ingestor — playoff/finals wins only (for now).

Coarse model: all players whose ``player_master.team == winner_team`` get
a per-game event. Series-clincher and finals flags trigger heavier event
types when the client supplies them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent, PlayerEventType
from sportscards.events._common import existing_event_keys, write_json_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameRow:
    game_id: str
    game_date: date
    home_team: str
    away_team: str
    is_playoff: bool
    is_finals: bool
    is_all_star: bool
    winner_team: str | None
    is_series_clincher: bool = False


class ScheduleClient(Protocol):
    def get_schedule(self, season: str) -> list[GameRow]: ...


class LiveScheduleClient:
    def get_schedule(self, season: str) -> list[GameRow]:  # pragma: no cover
        # TODO: nba_api.stats.endpoints.leaguegamefinder filtered by season type.
        raise NotImplementedError("LiveScheduleClient not implemented; add nba_api dep first")


_PLAYOFF_TYPES = [
    PlayerEventType.PLAYOFF_WIN.value,
    PlayerEventType.PLAYOFF_SERIES_WIN.value,
    PlayerEventType.PLAYOFF_FINALS_WIN.value,
]


def ingest_schedule(
    session: Session,
    *,
    client: ScheduleClient,
    season: str,
    cache_dir: Path | None = None,
) -> int:
    """Emit playoff/finals win events. Returns count written."""
    rows = client.get_schedule(season)
    write_json_cache(
        [
            {
                "game_id": r.game_id,
                "game_date": r.game_date.isoformat(),
                "home_team": r.home_team,
                "away_team": r.away_team,
                "is_playoff": r.is_playoff,
                "is_finals": r.is_finals,
                "is_all_star": r.is_all_star,
                "winner_team": r.winner_team,
                "is_series_clincher": r.is_series_clincher,
            }
            for r in rows
        ],
        source="schedule",
        as_of=season,
        cache_dir=cache_dir,
    )

    # First pass: gather playoff games + their rosters, collect candidate
    # event keys so we can dedupe in a single batched SELECT.
    plans: list[tuple[GameRow, str, datetime, list[Player]]] = []
    all_player_ids: set[int] = set()
    all_dates: list[datetime] = []

    for game in rows:
        if not game.is_playoff or not game.winner_team:
            continue

        if game.is_finals and game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_FINALS_WIN.value
        elif game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_SERIES_WIN.value
        else:
            event_type = PlayerEventType.PLAYOFF_WIN.value

        roster = (
            session.execute(select(Player).where(Player.team == game.winner_team)).scalars().all()
        )
        if not roster:
            logger.warning(
                "no players found for winner team %s on %s", game.winner_team, game.game_date
            )
            continue

        event_dt = datetime.combine(game.game_date, datetime.min.time())
        plans.append((game, event_type, event_dt, list(roster)))
        all_player_ids.update(p.player_id for p in roster)
        all_dates.append(event_dt)

    if not plans:
        session.commit()
        return 0

    existing = existing_event_keys(
        session,
        player_ids=list(all_player_ids),
        event_types=_PLAYOFF_TYPES,
        date_range=(min(all_dates), max(all_dates)),
    )

    written = 0
    for game, event_type, event_dt, roster in plans:
        for player in roster:
            key = (player.player_id, event_type, event_dt)
            if key in existing:
                continue
            session.add(
                PlayerEvent(
                    player_id=player.player_id,
                    event_type=event_type,
                    event_date=event_dt,
                    event_payload={
                        "game_id": game.game_id,
                        "winner_team": game.winner_team,
                        "is_finals": game.is_finals,
                        "is_series_clincher": game.is_series_clincher,
                    },
                )
            )
            existing.add(key)
            written += 1

    session.commit()
    return written
