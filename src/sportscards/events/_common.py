"""Shared helpers for player_event ingestors.

Three small utilities that the per-source ingestors (injuries, schedule,
awards, transactions) all need:

- :func:`resolve_player_by_name` — case-insensitive name lookup.
- :func:`existing_event_keys` — one batched SELECT for dedupe.
- :func:`write_json_cache` — uniform on-disk cache layout.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent

logger = logging.getLogger("sportscards.events")

DEFAULT_CACHE_ROOT = Path("data/events_cache")


def resolve_player_by_name(session: Session, name: str) -> int | None:
    """Case-insensitive exact match against :attr:`Player.name`.

    Returns the matched ``player_id`` or ``None``; logs a warning on miss.
    """
    stmt = select(Player.player_id).where(func.lower(Player.name) == name.strip().lower())
    pid = session.execute(stmt).scalars().first()
    if pid is None:
        logger.warning("could not resolve player by name: %s", name)
    return pid


def existing_event_keys(
    session: Session,
    *,
    player_ids: Sequence[int] | None = None,
    event_types: Sequence[str] | None = None,
    date_range: tuple[datetime, datetime] | None = None,
) -> set[tuple[int, str, datetime]]:
    """Return existing ``(player_id, event_type, event_date)`` tuples.

    One batched SELECT honoring any subset of filters; each ``None`` arg
    is simply skipped. Callers do membership checks in Python instead of
    one SELECT per candidate row.
    """
    stmt = select(PlayerEvent.player_id, PlayerEvent.event_type, PlayerEvent.event_date)
    if player_ids is not None:
        if not player_ids:
            return set()
        stmt = stmt.where(PlayerEvent.player_id.in_(list(player_ids)))
    if event_types is not None:
        if not event_types:
            return set()
        stmt = stmt.where(PlayerEvent.event_type.in_(list(event_types)))
    if date_range is not None:
        start, end = date_range
        stmt = stmt.where(PlayerEvent.event_date.between(start, end))
    return {(pid, etype, edate) for pid, etype, edate in session.execute(stmt).all()}


def write_json_cache(
    rows: Iterable[Any],
    source: str,
    as_of: date | str,
    cache_dir: Path | None,
) -> Path:
    """Write ``rows`` (list-of-dicts or list-of-dataclasses) to a JSON cache file.

    Layout: ``<cache_dir>/<source>/<as_of>.json`` when ``cache_dir`` is the
    default root; if the caller passes an explicit ``cache_dir`` we treat
    it as a leaf directory and write directly inside it (preserves the
    pre-refactor on-disk shape used by tests and existing scripts).
    ``cache_dir=None`` falls back to ``data/events_cache/<source>/``.
    """
    if cache_dir is None:
        target_dir = DEFAULT_CACHE_ROOT / source
    else:
        target_dir = Path(cache_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
    out_path = target_dir / f"{stem}.json"

    payload = [r if isinstance(r, dict) else _to_dict(r) for r in rows]
    out_path.write_text(json.dumps(payload, indent=2, default=_json_default))
    return out_path


def _to_dict(obj: Any) -> dict[str, Any]:
    # Lightweight dataclass-or-mapping coercion without importing dataclasses
    # at every call site.
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "_asdict"):
        return obj._asdict()
    return dict(obj)


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")
