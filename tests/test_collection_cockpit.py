from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import CardIdentityCandidate, TxRaw
from sportscards.reports import queries


def test_collection_cockpit_targets_join_coverage_urls_and_review_pressure(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        raw = TxRaw(
            source="cardladder_manual",
            raw_title="2023 Panini Prizm Victor Wembanyama #136 PSA 10",
            raw_price=Decimal("500.00"),
            raw_currency="USD",
            sold_at=datetime(2026, 6, 1, tzinfo=UTC),
            external_id="cl-victor-1",
            raw_json={"search_query": "Victor Wembanyama Prizm PSA 10"},
        )
        session.add(raw)
        session.flush()
        session.add(
            CardIdentityCandidate(
                raw_id=raw.raw_id,
                canonical_key="victor-wembanyama|panini|2023|prizm|136|base|rookie",
                player_name="Victor Wembanyama",
                manufacturer="Panini",
                year=2023,
                set_name="Prizm",
                card_number="136",
                parallel="Base",
                is_rookie=True,
                has_auto=False,
                has_patch=False,
                slab_grader="PSA",
                slab_grade=Decimal("10"),
                confidence=Decimal("0.650"),
                needs_review=True,
                evidence_json={"search_query": "Victor Wembanyama Prizm PSA 10"},
            )
        )
        session.commit()

    targets = queries.collection_cockpit_targets(engine=engine, limit=5)
    victor = targets.loc[targets["search_query"] == "Victor Wembanyama Prizm PSA 10"].iloc[0]

    assert int(victor["rows"]) == 1
    assert int(victor["remaining_rows"]) == 99
    assert int(victor["needs_review_rows"]) == 1
    assert victor["next_action"] == "review_identity"
    assert victor["cardladder_url"] == (
        "https://app.cardladder.com/sales-history?sort=date&direction=desc"
        "&q=Victor%20Wembanyama%20Prizm%20PSA%2010"
    )
