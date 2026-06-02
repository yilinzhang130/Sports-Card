from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from reports.app._components.card_identity_actions import (
    approve_candidate,
    reject_candidate,
    update_candidate,
)
from sportscards.db.models import AuditLog, CardIdentityCandidate, TxRaw
from sportscards.reports import queries


def test_approve_candidate_clears_review_flag_and_audits(migrated_db):
    engine = create_engine(migrated_db)
    raw_id = _seed_candidate(engine, raw_title="2023", canonical_key="2023|base")

    approve_candidate(raw_id, actor="tester")

    with Session(engine) as session:
        candidate = session.get(CardIdentityCandidate, raw_id)
        assert candidate is not None
        assert candidate.needs_review is False
        assert candidate.confidence == Decimal("1.000")
        assert candidate.evidence_json["review_status"] == "approved"
        audit = session.execute(select(AuditLog)).scalar_one()
        assert audit.action == "card_identity_approved"
        assert audit.payload_json == {"raw_id": raw_id, "canonical_key": "2023|base"}
        assert audit.actor == "tester"


def test_update_candidate_recomputes_key_and_records_manual_fields(migrated_db):
    engine = create_engine(migrated_db)
    raw_id = _seed_candidate(engine, raw_title="2023", canonical_key="2023|base")

    update_candidate(
        raw_id,
        {
            "player_name": "Victor Wembanyama",
            "manufacturer": "Panini",
            "year": 2023,
            "set_name": "Prizm",
            "card_number": "136",
            "parallel": "Silver Prizm",
            "is_rookie": True,
            "slab_grader": "PSA",
            "slab_grade": Decimal("10"),
        },
        reviewer_note="manual Card Ladder correction",
        actor="tester",
    )

    with Session(engine) as session:
        candidate = session.get(CardIdentityCandidate, raw_id)
        assert candidate is not None
        assert candidate.canonical_key == (
            "victor-wembanyama|panini|2023|prizm|136|silver-prizm|rookie"
        )
        assert candidate.needs_review is False
        assert candidate.confidence == Decimal("1.000")
        assert candidate.evidence_json["review_status"] == "manual_override"
        assert candidate.evidence_json["review_note"] == "manual Card Ladder correction"
        audit = session.execute(select(AuditLog)).scalar_one()
        assert audit.action == "card_identity_updated"
        assert audit.payload_json["raw_id"] == raw_id
        assert audit.payload_json["canonical_key"] == candidate.canonical_key


def test_reject_candidate_hides_row_from_default_review_queue(migrated_db):
    engine = create_engine(migrated_db)
    raw_id = _seed_candidate(engine, raw_title="junk lot", canonical_key="junk|base")

    reject_candidate(raw_id, reason="not a single-card sale", actor="tester")

    queue = queries.card_identity_review_queue(engine=engine)
    rejected_queue = queries.card_identity_review_queue(engine=engine, include_rejected=True)
    summary = queries.card_identity_review_summary(engine=engine)

    assert queue.empty
    assert len(rejected_queue) == 1
    assert summary["high_confidence"] == 0
    assert summary["rejected"] == 1
    with Session(engine) as session:
        candidate = session.get(CardIdentityCandidate, raw_id)
        assert candidate is not None
        assert candidate.needs_review is False
        assert candidate.evidence_json["review_status"] == "rejected"
        assert candidate.evidence_json["reject_reason"] == "not a single-card sale"
        audit = session.execute(select(AuditLog)).scalar_one()
        assert audit.action == "card_identity_rejected"
        assert audit.payload_json == {"raw_id": raw_id, "reason": "not a single-card sale"}


def _seed_candidate(engine, *, raw_title: str, canonical_key: str) -> int:
    with Session(engine) as session:
        raw = TxRaw(
            source="cardladder_manual",
            raw_title=raw_title,
            raw_price=Decimal("100.00"),
            raw_currency="USD",
            sold_at=datetime(2026, 6, 1, tzinfo=UTC),
            external_id=f"raw-{raw_title}",
            raw_json={"search_query": "Victor Wembanyama Prizm PSA 10"},
        )
        session.add(raw)
        session.flush()
        session.add(
            CardIdentityCandidate(
                raw_id=raw.raw_id,
                canonical_key=canonical_key,
                year=2023,
                parallel="Base",
                is_rookie=False,
                has_auto=False,
                has_patch=False,
                confidence=Decimal("0.180"),
                needs_review=True,
                evidence_json={"search_query": "Victor Wembanyama Prizm PSA 10"},
            )
        )
        session.commit()
        return raw.raw_id
