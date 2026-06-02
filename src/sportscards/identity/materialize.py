"""Materialize card identity candidates from raw transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sportscards.db.models import CardIdentityCandidate, TxRaw
from sportscards.db.session import session_scope
from sportscards.identity.card_identity import CardIdentity, parse_card_identity


def materialize_card_identity_candidates(
    *,
    engine: Engine | None = None,
    source: str = "cardladder_manual",
    limit: int | None = None,
) -> dict[str, int]:
    context = session_scope() if engine is None else _session_for_engine(engine)
    processed = inserted = updated = skipped = 0
    with context as session:
        existing_raw_ids = select(CardIdentityCandidate.raw_id)
        query = (
            select(TxRaw)
            .where(TxRaw.source == source)
            .where(TxRaw.raw_id.notin_(existing_raw_ids))
            .order_by(TxRaw.raw_id)
        )
        if limit is not None:
            query = query.limit(limit)
        rows = session.execute(query).scalars().all()
        for raw in rows:
            processed += 1
            identity = parse_card_identity(
                raw.raw_title,
                search_query=_search_query(raw.raw_json),
            )
            if not identity.canonical_key:
                skipped += 1
                continue
            existing = session.get(CardIdentityCandidate, raw.raw_id)
            candidate = _candidate_from_identity(raw.raw_id, identity)
            if existing is None:
                session.add(candidate)
                inserted += 1
            else:
                _copy_candidate(candidate, existing)
                updated += 1
    return {"processed": processed, "inserted": inserted, "updated": updated, "skipped": skipped}


class _session_for_engine:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def __enter__(self) -> Session:
        self.session = Session(self.engine)
        return self.session

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self.session.commit()
        else:
            self.session.rollback()
        self.session.close()


def _search_query(raw_json: dict[str, Any] | None) -> str | None:
    if not raw_json:
        return None
    value = raw_json.get("search_query")
    return str(value) if value else None


def _candidate_from_identity(raw_id: int, identity: CardIdentity) -> CardIdentityCandidate:
    now = datetime.now(UTC)
    return CardIdentityCandidate(
        raw_id=raw_id,
        canonical_key=identity.canonical_key,
        player_name=identity.player_name,
        manufacturer=identity.manufacturer,
        year=identity.year,
        set_name=identity.set_name,
        subset=identity.subset,
        card_number=identity.card_number,
        parallel=identity.parallel,
        print_run=identity.print_run,
        is_rookie=identity.is_rookie,
        has_auto=identity.has_auto,
        has_patch=identity.has_patch,
        slab_grader=identity.slab_grader,
        slab_grade=identity.slab_grade,
        confidence=identity.confidence,
        needs_review=identity.needs_review,
        evidence_json=identity.evidence,
        created_at=now,
        updated_at=now,
    )


def _copy_candidate(source: CardIdentityCandidate, target: CardIdentityCandidate) -> None:
    for field in (
        "canonical_key",
        "player_name",
        "manufacturer",
        "year",
        "set_name",
        "subset",
        "card_number",
        "parallel",
        "print_run",
        "is_rookie",
        "has_auto",
        "has_patch",
        "slab_grader",
        "slab_grade",
        "confidence",
        "needs_review",
        "evidence_json",
        "updated_at",
    ):
        setattr(target, field, getattr(source, field))
