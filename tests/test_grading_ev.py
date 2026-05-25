"""Tests for grading-EV optionality model."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, PopSnapshot
from sportscards.factors.grading_ev import UNIVERSE_PRIOR, estimate_gem_rate


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    s.add(Player(player_id=1, name="X"))
    s.add(
        Card(
            card_id=1,
            year=2020,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="1",
            parallel="Base",
            player_id=1,
        )
    )
    s.commit()
    yield s
    s.close()


def _add_pop(s: Session, card_id: int, when: datetime, psa8: int, psa9: int, psa10: int) -> None:
    for grade, n in [(Decimal("8"), psa8), (Decimal("9"), psa9), (Decimal("10"), psa10)]:
        s.add(
            PopSnapshot(
                snapshot_date=when, card_id=card_id, grader="PSA", grade=grade, pop_count=n
            )
        )
    s.commit()


def test_gem_rate_uses_latest_snapshot(session):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    _add_pop(session, 1, now, psa8=10, psa9=40, psa10=50)
    r = estimate_gem_rate(session, card_id=1, as_of=now)
    # 50 / (10 + 40 + 50) = 0.50, large n → minimal shrinkage
    assert Decimal("0.48") <= r.rate <= Decimal("0.52")
    assert r.sample_size == 100


def test_gem_rate_small_sample_shrinks_toward_prior(session):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    _add_pop(session, 1, now, psa8=0, psa9=0, psa10=2)
    r = estimate_gem_rate(session, card_id=1, as_of=now)
    # raw rate = 1.00; with prior strength = 20 and prior = 0.50,
    # posterior ≈ (2 + 10) / (2 + 20) = 12/22 ≈ 0.545 — strongly pulled to prior
    assert abs(r.rate - UNIVERSE_PRIOR) < Decimal("0.10")
    assert r.sample_size == 2


def test_gem_rate_no_snapshot_returns_prior(session):
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    r = estimate_gem_rate(session, card_id=1, as_of=now)
    assert r.rate == UNIVERSE_PRIOR
    assert r.sample_size == 0
