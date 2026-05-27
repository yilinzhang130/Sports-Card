"""Fixtures for tests/reports/ — mirrors the seeded_card_and_index fixture
from tests/pricing/conftest.py without importing it cross-module."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sportscards.db.models import (
    Card,
    Player,
    RepeatSalesIndex,
    TxClean,
    TxRaw,
)
from sportscards.db.session import session_scope


@pytest.fixture
def seeded_card_and_index():
    """Card with last_sold $100 at t-1d, index 100 then, 200 now."""
    with session_scope() as s:
        p = Player(name="Test Player Reports")
        s.add(p)
        s.flush()
        c = Card(
            year=2018,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="1",
            parallel="Base",
            player_id=p.player_id,
        )
        s.add(c)
        s.flush()

        now = datetime(2026, 5, 27, tzinfo=UTC)
        s.add_all(
            [
                RepeatSalesIndex(
                    sport="NBA",
                    era="modern",
                    bucket="weekly",
                    grade_tier="PSA10",
                    period_start=now - timedelta(days=1),
                    index_value=Decimal("100"),
                    n_pairs=0,
                ),
                RepeatSalesIndex(
                    sport="NBA",
                    era="modern",
                    bucket="weekly",
                    grade_tier="PSA10",
                    period_start=now,
                    index_value=Decimal("200"),
                    n_pairs=0,
                ),
            ]
        )
        raw = TxRaw(
            source="ebay",
            external_id="reports-test-1",
            raw_title="Test Card Reports",
            raw_json={},
        )
        s.add(raw)
        s.flush()
        s.add(
            TxClean(
                raw_id=raw.raw_id,
                card_id=c.card_id,
                price_usd=Decimal("100"),
                sold_at=now - timedelta(days=1),
                parser_confidence=Decimal("0.9"),
                parser_method="rule",
            )
        )
        s.flush()
        return c.card_id, p.player_id
