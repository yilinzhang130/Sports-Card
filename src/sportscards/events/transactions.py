"""NBA transactions ingestor (call-ups, two-ways, trades, signings)."""

from __future__ import annotations

import logging
import re
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


_ESPN_TRANSACTIONS_URL = "https://www.espn.com/nba/transactions"

# Order matters: more-specific phrases are tried before generic ones.
_TXN_RULES: list[tuple[str, str]] = [
    ("two-way to standard", PlayerEventType.CALL_UP.value),
    ("converted", PlayerEventType.CALL_UP.value),
    ("called up", PlayerEventType.CALL_UP.value),
    ("two-way", PlayerEventType.TWO_WAY.value),
    ("traded", PlayerEventType.TRADED.value),
    ("acquired", PlayerEventType.TRADED.value),
    ("signed", PlayerEventType.SIGNED.value),
    ("re-signed", PlayerEventType.SIGNED.value),
]

_NAME_PATTERN = re.compile(r"([A-Z][a-zA-Z'\.\-]+(?:\s+[A-Z][a-zA-Z'\.\-]+){1,2})")


def _classify_txn(description: str) -> str | None:
    lower = description.lower()
    for needle, etype in _TXN_RULES:
        if needle in lower:
            return etype
    return None


_VERB_TOKENS = re.compile(
    r"\b(?:Signed|Re-signed|Traded|Acquired|Sent|Waived|Released|Converted|Called)\b",
    re.IGNORECASE,
)


def _extract_player_name(description: str, etype: str) -> str | None:
    """Pull the player name out of an ESPN transaction sentence.

    Sentences look like "Signed F LeBron James to a two-year contract."
    Strategy: drop everything up to and including the verb, strip any
    position abbreviation immediately after, then take the first
    two-or-three-word capitalized run.
    """
    verb_match = _VERB_TOKENS.search(description)
    if verb_match is None:
        return None
    tail = description[verb_match.end() :]
    tail = re.sub(r"^\s*(?:PG|SG|SF|PF|C|G|F|G/F|F/C)\b", "", tail).strip()
    name_match = _NAME_PATTERN.search(tail)
    if name_match is None:
        return None
    return name_match.group(1)


def _parse_espn_date(raw: str, *, year_hint: int) -> date | None:
    # ESPN dates look like "Jan 15" (current year implied) or "Dec 30".
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%b %d", "%B %d", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if parsed.year == 1900:
            parsed = parsed.replace(year=year_hint)
        return parsed.date()
    return None


def parse_espn_transactions(html: str, *, today: date) -> list[TxnRow]:
    """Parse ESPN's NBA transactions page into ``TxnRow``s."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    out: list[TxnRow] = []
    for table in soup.select("table"):
        for tr in table.select("tbody tr"):
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 3:
                continue
            date_cell, _team_cell, desc = cells[0], cells[1], cells[-1]
            txn_date = _parse_espn_date(date_cell, year_hint=today.year)
            if txn_date is None or txn_date > today:
                continue
            etype = _classify_txn(desc)
            if etype is None:
                continue
            player = _extract_player_name(desc, etype)
            if not player:
                continue
            out.append(TxnRow(txn_type=etype, txn_date=txn_date, player_name=player))
    return out


class LiveTransactionsClient:
    """Scrapes ESPN's NBA transactions feed (last ~30 days)."""

    def __init__(
        self,
        *,
        url: str = _ESPN_TRANSACTIONS_URL,
        cache_dir: Path = Path("data/events_cache/transactions/html"),
    ) -> None:
        self._url = url
        self._cache_dir = cache_dir

    def get_transactions(self, since: date) -> list[TxnRow]:
        from sportscards.events._http import fetch_html

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        today = date.today()
        cache_path = self._cache_dir / f"{today.isoformat()}.html"

        html: str | None = None
        try:
            html = fetch_html(self._url)
            cache_path.write_text(html, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("ESPN transactions fetch failed (%s); falling back to cache", exc)

        if html is None:
            cached = sorted(self._cache_dir.glob("*.html"))
            if not cached:
                raise RuntimeError(
                    "ESPN transactions unavailable and no cached HTML to fall back to"
                )
            html = cached[-1].read_text(encoding="utf-8")

        rows = parse_espn_transactions(html, today=today)
        return [r for r in rows if r.txn_date >= since]


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
