"""NBA transactions ingestor (call-ups, two-ways, trades, signings)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
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

_VALID_TXN = {
    PlayerEventType.CALL_UP.value,
    PlayerEventType.TWO_WAY.value,
    PlayerEventType.TRADED.value,
    PlayerEventType.SIGNED.value,
}


@dataclass(frozen=True)
class TxnRow:
    txn_type: str
    txn_date: date
    player_name: str


class TransactionsClient(Protocol):
    def get_transactions(self, since: date) -> list[TxnRow]: ...


class LiveTransactionsClient:
    def get_transactions(self, since: date) -> list[TxnRow]:  # pragma: no cover
        # TODO: scrape NBA.com transactions feed or Spotrac for two-way / call-up data.
        raise NotImplementedError("LiveTransactionsClient not implemented")


def ingest_transactions(
    session: Session,
    *,
    client: TransactionsClient,
    since: date,
    cache_dir: Path | None = None,
) -> int:
    rows = client.get_transactions(since)
    write_json_cache(
        [
            {
                "txn_type": r.txn_type,
                "txn_date": r.txn_date.isoformat(),
                "player_name": r.player_name,
            }
            for r in rows
        ],
        source="transactions",
        as_of=f"since-{since.isoformat()}",
        cache_dir=cache_dir,
    )

    candidates: list[tuple[int, str, datetime]] = []
    for row in rows:
        if row.txn_type not in _VALID_TXN:
            logger.warning("unknown txn_type %r — skipping", row.txn_type)
            continue
        pid = resolve_player_by_name(session, row.player_name)
        if pid is None:
            continue
        event_dt = datetime.combine(row.txn_date, datetime.min.time())
        candidates.append((pid, row.txn_type, event_dt))

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
    for pid, etype, event_dt in candidates:
        key = (pid, etype, event_dt)
        if key in existing:
            continue
        session.add(
            PlayerEvent(
                player_id=pid,
                event_type=etype,
                event_date=event_dt,
                event_payload={},
            )
        )
        existing.add(key)
        written += 1

    session.commit()
    return written
