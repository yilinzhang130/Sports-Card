"""NBA transactions ingestor (call-ups, two-ways, trades, signings)."""

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

DEFAULT_CACHE_DIR = Path("data/events_cache/transactions")

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
    def get_transactions(self, since: date) -> list[TxnRow]:
        ...


class LiveTransactionsClient:
    def get_transactions(self, since: date) -> list[TxnRow]:  # pragma: no cover
        # TODO: scrape NBA.com transactions feed or Spotrac for two-way / call-up data.
        raise NotImplementedError("LiveTransactionsClient not implemented")


def _write_cache(rows: list[TxnRow], since: date, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"since-{since.isoformat()}.json"
    payload = [
        {"txn_type": r.txn_type, "txn_date": r.txn_date.isoformat(), "player_name": r.player_name}
        for r in rows
    ]
    out_path.write_text(json.dumps(payload, indent=2))
    return out_path


def _resolve_player(session: Session, name: str) -> Player | None:
    stmt = select(Player).where(func.lower(Player.name) == name.strip().lower())
    return session.execute(stmt).scalars().first()


def ingest_transactions(
    session: Session,
    *,
    client: TransactionsClient,
    since: date,
    cache_dir: Path | None = None,
) -> int:
    rows = client.get_transactions(since)
    _write_cache(rows, since, cache_dir or DEFAULT_CACHE_DIR)

    written = 0
    for row in rows:
        if row.txn_type not in _VALID_TXN:
            logger.warning("unknown txn_type %r — skipping", row.txn_type)
            continue

        player = _resolve_player(session, row.player_name)
        if player is None:
            logger.warning("could not resolve transaction player by name: %s", row.player_name)
            continue

        event_dt = datetime.combine(row.txn_date, datetime.min.time())
        exists = session.execute(
            select(PlayerEvent.event_id).where(
                PlayerEvent.player_id == player.player_id,
                PlayerEvent.event_type == row.txn_type,
                PlayerEvent.event_date == event_dt,
            )
        ).first()
        if exists:
            continue

        session.add(
            PlayerEvent(
                player_id=player.player_id,
                event_type=row.txn_type,
                event_date=event_dt,
                event_payload={},
            )
        )
        session.flush()
        written += 1

    session.commit()
    return written
