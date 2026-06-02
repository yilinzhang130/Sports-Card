from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import CardIdentityCandidate, TxRaw
from sportscards.reports import queries


def test_card_identity_review_summary_counts_candidates(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        raw = _raw("2014 Panini Prizm Stephen Curry #176 PSA 10", "raw-a")
        session.add(raw)
        session.flush()
        session.add(
            CardIdentityCandidate(
                raw_id=raw.raw_id,
                canonical_key="stephen-curry|panini|2014|prizm|176|base",
                player_name="Stephen Curry",
                manufacturer="Panini",
                year=2014,
                set_name="Prizm",
                card_number="176",
                parallel="Base",
                is_rookie=False,
                has_auto=False,
                has_patch=False,
                slab_grader="PSA",
                slab_grade=Decimal("10"),
                confidence=Decimal("1.000"),
                needs_review=False,
                evidence_json={},
            )
        )
        session.commit()

    summary = queries.card_identity_review_summary(engine=engine)

    assert summary == {
        "candidates": 1,
        "distinct_identities": 1,
        "needs_review": 0,
        "high_confidence": 1,
        "rejected": 0,
    }


def test_card_identity_review_queue_includes_raw_title_and_key(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        raw = _raw("2023", "raw-b")
        session.add(raw)
        session.flush()
        session.add(
            CardIdentityCandidate(
                raw_id=raw.raw_id,
                canonical_key="2023|base",
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

    queue = queries.card_identity_review_queue(engine=engine)

    assert len(queue) == 1
    row = queue.iloc[0].to_dict()
    assert row["raw_title"] == "2023"
    assert row["canonical_key"] == "2023|base"
    assert row["needs_review"] is True
    assert row["search_query"] == "Victor Wembanyama Prizm PSA 10"


def _raw(title: str, external_id: str) -> TxRaw:
    return TxRaw(
        source="cardladder_manual",
        raw_title=title,
        raw_price=Decimal("100.00"),
        raw_currency="USD",
        sold_at=datetime(2026, 6, 1, tzinfo=UTC),
        external_id=external_id,
        raw_json={"search_query": "Stephen Curry Prizm PSA 10"},
    )
