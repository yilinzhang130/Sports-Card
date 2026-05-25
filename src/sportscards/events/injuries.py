"""NBA injury-status ingestor.

Emits ``injury_out`` / ``injury_dtd`` / ``injury_return`` events into
``player_events`` only when a player's status CHANGES vs. the most recent
prior event. The actual upstream feed (nba_api) is wrapped behind
``InjuryClient`` so tests can inject canned data.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent, PlayerEventType

logger = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/events_cache/injuries")

# Map raw upstream status -> our internal event_type.
_OUT_STATUSES = {"out", "season_ending", "season-ending"}
_DTD_STATUSES = {"day_to_day", "day-to-day", "questionable", "probable", "doubtful"}
_RETURN_STATUSES = {"available", "active", "healthy"}


@dataclass(frozen=True)
class InjuryRow:
    nba_player_id: int
    name: str
    status: str
    status_date: date
    note: str | None = None


class InjuryClient(Protocol):
    """Narrow protocol for the upstream injury feed."""

    def get_injury_report(self, as_of: date) -> list[InjuryRow]:
        ...


class LiveInjuryClient:
    """Production client wrapping nba_api's injury endpoint.

    Not invoked from tests; left as a stub to be fleshed out when nba_api
    is added to the project deps.
    """

    def get_injury_report(self, as_of: date) -> list[InjuryRow]:  # pragma: no cover
        # TODO: call nba_api.stats.endpoints.playerinjuries or league_dash equivalent.
        raise NotImplementedError("LiveInjuryClient not implemented; add nba_api dep first")


def _classify(status: str) -> str | None:
    s = status.strip().lower().replace(" ", "_")
    if s in _OUT_STATUSES:
        return PlayerEventType.INJURY_OUT.value
    if s in _DTD_STATUSES:
        return PlayerEventType.INJURY_DTD.value
    if s in _RETURN_STATUSES:
        return PlayerEventType.INJURY_RETURN.value
    return None


def _latest_event(session: Session, player_id: int) -> PlayerEvent | None:
    injury_types = [
        PlayerEventType.INJURY_OUT.value,
        PlayerEventType.INJURY_DTD.value,
        PlayerEventType.INJURY_RETURN.value,
    ]
    stmt = (
        select(PlayerEvent)
        .where(PlayerEvent.player_id == player_id)
        .where(PlayerEvent.event_type.in_(injury_types))
        .order_by(PlayerEvent.event_date.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _resolve_player(session: Session, name: str) -> Player | None:
    stmt = select(Player).where(func.lower(Player.name) == name.strip().lower())
    return session.execute(stmt).scalars().first()


def _write_cache(rows: list[InjuryRow], as_of: date, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{as_of.isoformat()}.json"
    payload = [
        {
            "nba_player_id": r.nba_player_id,
            "name": r.name,
            "status": r.status,
            "status_date": r.status_date.isoformat(),
            "note": r.note,
        }
        for r in rows
    ]
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def ingest_injuries(
    session: Session,
    *,
    client: InjuryClient,
    as_of: date,
    cache_dir: Path | None = None,
) -> int:
    """Pull injury report for ``as_of`` and emit change-events. Returns count written."""
    rows = client.get_injury_report(as_of)
    _write_cache(rows, as_of, cache_dir or DEFAULT_CACHE_DIR)

    written = 0
    for row in rows:
        new_type = _classify(row.status)
        if new_type is None:
            logger.warning("unknown injury status %r for %s", row.status, row.name)
            continue

        player = _resolve_player(session, row.name)
        if player is None:
            logger.warning("could not resolve injury player by name: %s", row.name)
            continue

        prior = _latest_event(session, player.player_id)
        # Skip an initial "return" when there's no prior injury context.
        if prior is None and new_type == PlayerEventType.INJURY_RETURN.value:
            continue
        if prior is not None and prior.event_type == new_type:
            continue

        event_dt = datetime.combine(row.status_date, datetime.min.time())
        # Defensive dedupe vs unique constraint on (player_id, event_type, event_date).
        exists = session.execute(
            select(PlayerEvent.event_id).where(
                PlayerEvent.player_id == player.player_id,
                PlayerEvent.event_type == new_type,
                PlayerEvent.event_date == event_dt,
            )
        ).first()
        if exists:
            continue

        session.add(
            PlayerEvent(
                player_id=player.player_id,
                event_type=new_type,
                event_date=event_dt,
                event_payload={
                    "status": row.status,
                    "note": row.note,
                    "nba_player_id": row.nba_player_id,
                },
            )
        )
        session.flush()
        written += 1

    session.commit()
    return written
