"""NBA schedule ingestor — playoff/finals wins only (for now).

Coarse model: all players whose ``player_master.team == winner_team`` get
a per-game event. Series-clincher and finals flags trigger heavier event
types when the client supplies them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent, PlayerEventType

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/events_cache/schedule")


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
    def get_schedule(self, season: str) -> list[GameRow]:
        ...


class LiveScheduleClient:
    def get_schedule(self, season: str) -> list[GameRow]:  # pragma: no cover
        # TODO: nba_api.stats.endpoints.leaguegamefinder filtered by season type.
        raise NotImplementedError("LiveScheduleClient not implemented; add nba_api dep first")


def _write_cache(rows: list[GameRow], season: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{season}.json"
    payload = [
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
    ]
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def ingest_schedule(
    session: Session,
    *,
    client: ScheduleClient,
    season: str,
    cache_dir: Path | None = None,
) -> int:
    """Emit playoff/finals win events. Returns count written."""
    rows = client.get_schedule(season)
    _write_cache(rows, season, cache_dir or DEFAULT_CACHE_DIR)

    written = 0
    for game in rows:
        if not game.is_playoff or not game.winner_team:
            continue

        # Highest-priority event type wins (finals > series > regular playoff).
        if game.is_finals and game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_FINALS_WIN.value
        elif game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_SERIES_WIN.value
        else:
            event_type = PlayerEventType.PLAYOFF_WIN.value

        roster = session.execute(
            select(Player).where(Player.team == game.winner_team)
        ).scalars().all()
        if not roster:
            logger.warning("no players found for winner team %s on %s", game.winner_team, game.game_date)
            continue

        event_dt = datetime.combine(game.game_date, datetime.min.time())
        for player in roster:
            exists = session.execute(
                select(PlayerEvent.event_id).where(
                    PlayerEvent.player_id == player.player_id,
                    PlayerEvent.event_type == event_type,
                    PlayerEvent.event_date == event_dt,
                )
            ).first()
            if exists:
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
            session.flush()
            written += 1

    session.commit()
    return written
