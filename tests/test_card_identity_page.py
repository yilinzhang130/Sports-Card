from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from streamlit.testing.v1 import AppTest

from sportscards.db.models import CardIdentityCandidate, TxRaw


def test_card_identity_page_renders_against_empty_db(migrated_db):
    at = AppTest.from_file("reports/app/pages/11_🧬_Card_Identity.py").run()

    assert not at.exception, f"unexpected exception: {at.exception}"


def test_card_identity_page_renders_review_action_controls(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        raw = TxRaw(
            source="cardladder_manual",
            raw_title="2023",
            raw_price=Decimal("100.00"),
            raw_currency="USD",
            sold_at=datetime(2026, 6, 1, tzinfo=UTC),
            external_id="raw-page-actions",
            raw_json={"search_query": "Victor Wembanyama Prizm PSA 10"},
        )
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

    at = AppTest.from_file("reports/app/pages/11_🧬_Card_Identity.py").run()

    assert not at.exception, f"unexpected exception: {at.exception}"
    button_labels = {button.label for button in at.button}
    assert "Approve identity" in button_labels
    assert "Save manual override" in button_labels
    assert "Reject row" in button_labels
