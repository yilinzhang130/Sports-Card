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


_ESPN_INJURIES_URL = "https://www.espn.com/nba/injuries"


def parse_espn_injuries(html: str, *, as_of: date) -> list[InjuryRow]:
    """Parse the ESPN NBA injuries page into ``InjuryRow``s.

    ESPN renders a per-team ``<section class="Card">`` block, each holding
    a ``<table class="Table">`` with columns ``NAME | POS | EST. RETURN DATE
    | STATUS | COMMENT``. Header rows lack a ``data-idx`` attr so we filter
    them out via column count.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[InjuryRow] = []
    for table in soup.select("table"):
        for tr in table.select("tbody tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(cells) < 5:
                continue
            name, _pos, est_return, status, comment = cells[:5]
            if not name:
                continue
            note_parts = [p for p in (est_return, comment) if p]
            out.append(
                InjuryRow(
                    nba_player_id=0,
                    name=name,
                    status=status,
                    status_date=as_of,
                    note=" | ".join(note_parts) if note_parts else None,
                )
            )
    return out


class LiveInjuryClient:
    """Scrapes ESPN's NBA injuries page.

    The page is server-rendered so plain ``httpx`` + ``BeautifulSoup`` is
    sufficient. We cache the raw HTML on disk per day so that a layout
    change upstream (which would silently return zero rows) falls back to
    the most recent good snapshot instead of bricking the pipeline.
    """

    def __init__(
        self,
        *,
        url: str = _ESPN_INJURIES_URL,
        cache_dir: Path = Path("data/events_cache/injuries/html"),
    ) -> None:
        self._url = url
        self._cache_dir = cache_dir

    def get_injury_report(self, as_of: date) -> list[InjuryRow]:
        from sportscards.events._http import fetch_html

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_dir / f"{as_of.isoformat()}.html"

        html: str | None = None
        try:
            html = fetch_html(self._url)
            cache_path.write_text(html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - log + fall back to cache
            logger.warning("ESPN injuries fetch failed (%s); falling back to cache", exc)

        if html:
            rows = parse_espn_injuries(html, as_of=as_of)
            if rows:
                return rows
            logger.warning("ESPN injuries parser returned 0 rows; layout drift suspected")

        # Fallback: most recent cached HTML.
        cached = sorted(self._cache_dir.glob("*.html"))
        if not cached:
            raise RuntimeError("ESPN injuries unavailable and no cached HTML to fall back to")
        fallback = cached[-1].read_text(encoding="utf-8")
        return parse_espn_injuries(fallback, as_of=as_of)


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
