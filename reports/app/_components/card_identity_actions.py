"""Card identity review write helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from reports.app._components.audit import write_audit
from sportscards.db.models import CardIdentityCandidate
from sportscards.db.session import session_scope
from sportscards.identity.card_identity import _canonical_key

EDITABLE_FIELDS = {
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
}


def approve_candidate(raw_id: int, *, actor: str = "ui") -> None:
    with session_scope() as session:
        candidate = _get_candidate(session, raw_id)
        candidate.needs_review = False
        candidate.confidence = Decimal("1.000")
        candidate.evidence_json = {
            **(candidate.evidence_json or {}),
            "review_status": "approved",
        }
        candidate.updated_at = datetime.now(UTC)
        canonical_key = candidate.canonical_key
    write_audit(
        "card_identity_approved",
        {"raw_id": raw_id, "canonical_key": canonical_key},
        actor=actor,
    )


def update_candidate(
    raw_id: int,
    fields: dict[str, Any],
    *,
    reviewer_note: str | None = None,
    actor: str = "ui",
) -> None:
    unknown_fields = sorted(set(fields) - EDITABLE_FIELDS)
    if unknown_fields:
        raise ValueError(f"Unsupported card identity fields: {', '.join(unknown_fields)}")

    with session_scope() as session:
        candidate = _get_candidate(session, raw_id)
        for field, value in fields.items():
            setattr(candidate, field, _clean_value(value))
        candidate.canonical_key = _canonical_key(
            candidate.player_name,
            candidate.manufacturer,
            candidate.year,
            candidate.set_name,
            candidate.subset,
            candidate.card_number,
            candidate.parallel or "Base",
            candidate.print_run,
            candidate.is_rookie,
            candidate.has_auto,
            candidate.has_patch,
        )
        candidate.needs_review = False
        candidate.confidence = Decimal("1.000")
        evidence = {
            **(candidate.evidence_json or {}),
            "review_status": "manual_override",
        }
        if reviewer_note:
            evidence["review_note"] = reviewer_note
        candidate.evidence_json = evidence
        candidate.updated_at = datetime.now(UTC)
        canonical_key = candidate.canonical_key
    write_audit(
        "card_identity_updated",
        {"raw_id": raw_id, "canonical_key": canonical_key, "fields": sorted(fields)},
        actor=actor,
    )


def reject_candidate(raw_id: int, *, reason: str, actor: str = "ui") -> None:
    with session_scope() as session:
        candidate = _get_candidate(session, raw_id)
        candidate.needs_review = False
        candidate.evidence_json = {
            **(candidate.evidence_json or {}),
            "review_status": "rejected",
            "reject_reason": reason,
        }
        candidate.updated_at = datetime.now(UTC)
    write_audit(
        "card_identity_rejected",
        {"raw_id": raw_id, "reason": reason},
        actor=actor,
    )


def _get_candidate(session, raw_id: int) -> CardIdentityCandidate:
    candidate = session.get(CardIdentityCandidate, raw_id)
    if candidate is None:
        raise ValueError(f"Card identity candidate raw_id={raw_id} not found")
    return candidate


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value
