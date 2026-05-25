"""NBA injury-status ingestor.

Emits ``injury_out`` / ``injury_dtd`` / ``injury_return`` events into
``player_events`` only when a player's status CHANGES vs. the most recent
prior event. The actual upstream feed (nba_api) is wrapped behind
``InjuryClient`` so tests can inject canned data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import PlayerEvent, PlayerEventType
from sportscards.events._common import (
    existing_event_keys,
    resolve_player_by_name,
    write_json_cache,
)

logger = logging.getLogger(__name__)

# Map raw upstream status -> our internal event_type.
_OUT_STATUSES = {"out", "season_ending", "season-ending"}
_DTD_STATUSES = {"day_to_day", "day-to-day", "questionable", "probable", "doubtful"}
_RETURN_STATUSES = {"available", "active", "healthy"}

_INJURY_TYPES = [
    PlayerEventType.INJURY_OUT.value,
    PlayerEventType.INJURY_DTD.value,
    PlayerEventType.INJURY_RETURN.value,
]


@dataclass(frozen=True)
class InjuryRow:
    nba_player_id: int
    name: str
    status: str
    status_date: date
    note: str | None = None


class InjuryClient(Protocol):
    """Narrow protocol for the upstream injury feed."""

    def get_injury_report(self, as_of: date) -> list[InjuryRow]: ...


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
    stmt = (
        select(PlayerEvent)
        .where(PlayerEvent.player_id == player_id)
        .where(PlayerEvent.event_type.in_(_INJURY_TYPES))
        .order_by(PlayerEvent.event_date.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def ingest_injuries(
    session: Session,
    *,
    client: InjuryClient,
    as_of: date,
    cache_dir: Path | None = None,
) -> int:
    """Pull injury report for ``as_of`` and emit change-events. Returns count written."""
    rows = client.get_injury_report(as_of)
    write_json_cache(
        [
            {
                "nba_player_id": r.nba_player_id,
                "name": r.name,
                "status": r.status,
                "status_date": r.status_date.isoformat(),
                "note": r.note,
            }
            for r in rows
        ],
        source="injuries",
        as_of=as_of,
        cache_dir=cache_dir,
    )

    # Pre-resolve all candidate (player_id, type, dt) keys so we can dedupe
    # against the unique constraint without a per-row SELECT.
    candidates: list[tuple[int, str, datetime, InjuryRow]] = []
    for row in rows:
        new_type = _classify(row.status)
        if new_type is None:
            logger.warning("unknown injury status %r for %s", row.status, row.name)
            continue
        pid = resolve_player_by_name(session, row.name)
        if pid is None:
            continue
        event_dt = datetime.combine(row.status_date, datetime.min.time())
        candidates.append((pid, new_type, event_dt, row))

    if not candidates:
        session.commit()
        return 0

    player_ids = {c[0] for c in candidates}
    dates = [c[2] for c in candidates]
    existing = existing_event_keys(
        session,
        player_ids=list(player_ids),
        event_types=_INJURY_TYPES,
        date_range=(min(dates), max(dates)),
    )

    # Track in-batch additions so multiple rows for the same player in a
    # single call still see the "prior" type without needing a flush.
    latest_in_batch: dict[int, tuple[str, datetime]] = {}

    written = 0
    for pid, new_type, event_dt, row in candidates:
        in_batch = latest_in_batch.get(pid)
        if in_batch is not None:
            prior_type: str | None = in_batch[0]
        else:
            prior = _latest_event(session, pid)
            prior_type = prior.event_type if prior is not None else None

        # Skip an initial "return" when there's no prior injury context.
        if prior_type is None and new_type == PlayerEventType.INJURY_RETURN.value:
            continue
        if prior_type is not None and prior_type == new_type:
            continue
        if (pid, new_type, event_dt) in existing:
            continue

        session.add(
            PlayerEvent(
                player_id=pid,
                event_type=new_type,
                event_date=event_dt,
                event_payload={
                    "status": row.status,
                    "note": row.note,
                    "nba_player_id": row.nba_player_id,
                },
            )
        )
        existing.add((pid, new_type, event_dt))
        latest_in_batch[pid] = (new_type, event_dt)
        written += 1

    session.commit()
    return written
