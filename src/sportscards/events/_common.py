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
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent

_FUZZY_SCORE_CUTOFF = 88


def _normalize_name(s: str) -> str:
    """Lowercase + strip diacritics so 'Dončić' matches 'Doncic'."""
    decomposed = unicodedata.normalize("NFKD", s)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().lower()

logger = logging.getLogger("sportscards.events")

DEFAULT_CACHE_ROOT = Path("data/events_cache")


def resolve_player_by_name(session: Session, name: str) -> int | None:
    """Resolve ``name`` to a ``player_id``: exact match first, then fuzzy.

    Live scrapers feed in names with diacritics ("Luka Dončić"), suffixes
    ("Jr."), or alternate spellings that the player_master rows may not
    carry verbatim. Exact case-insensitive match is tried first; on miss we
    fall back to rapidfuzz WRatio with score cutoff 88 against the full
    name list. Returns ``None`` (and warns) when neither resolves.
    """
    cleaned = name.strip()
    stmt = select(Player.player_id).where(func.lower(Player.name) == cleaned.lower())
    pid = session.execute(stmt).scalars().first()
    if pid is not None:
        return pid

    # Diacritic-tolerant exact match before falling back to fuzzy scoring.
    target = _normalize_name(cleaned)
    all_players = session.execute(select(Player.player_id, Player.name)).all()
    if not all_players:
        logger.warning("could not resolve player by name: %s", name)
        return None
    for row_pid, row_name in all_players:
        if _normalize_name(row_name) == target:
            return int(row_pid)

    choices = {row_pid: _normalize_name(row_name) for row_pid, row_name in all_players}
    match = process.extractOne(
        target, choices, scorer=fuzz.WRatio, score_cutoff=_FUZZY_SCORE_CUTOFF
    )
    if match is None:
        logger.warning("could not resolve player by name: %s", name)
        return None
    # extractOne with a dict returns (matched_value, score, key)
    return int(match[2])


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
    target_dir = DEFAULT_CACHE_ROOT / source if cache_dir is None else Path(cache_dir)
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

    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "_asdict"):
        return dict(obj._asdict())
    return dict(obj)


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")
