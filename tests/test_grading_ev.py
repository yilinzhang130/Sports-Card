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


from sportscards.db.models import Card, TxClean, TxRaw
from sportscards.factors.grading_ev import (
    GradingEV,
    compute_grading_ev,
    rank_grading_candidates,
)


def _planted_card(s, card_id, hedonic_p10, hedonic_p9, gem_rate):
    """Patch hedonic price + gem rate, and implant a high-n pop snapshot."""
    from sportscards.factors import grading_ev as ge

    ge._HEDONIC_OVERRIDES[card_id] = (Decimal(str(hedonic_p10)), Decimal(str(hedonic_p9)))
    ge._GEM_RATE_OVERRIDES[card_id] = Decimal(str(gem_rate))
    total = 1000
    psa10 = int(gem_rate * total)
    psa9 = total - psa10
    _add_pop(s, card_id, datetime(2026, 5, 1, tzinfo=timezone.utc),
             psa8=0, psa9=psa9, psa10=psa10)


def _add_raw_comp(s, card_id, price):
    raw = TxRaw(source="ebay", external_id=f"x-{card_id}-{price}",
                raw_title=f"raw-{card_id}", raw_price=Decimal(price),
                sold_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
    s.add(raw)
    s.flush()
    s.add(TxClean(raw_id=raw.raw_id, card_id=card_id, slab_grader=None,
                  slab_grade=None, price_usd=Decimal(price),
                  sold_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
                  parser_confidence=Decimal("0.90"),
                  parser_method="regex_raw"))
    s.commit()


def test_compute_grading_ev_matches_formula(session):
    # gem=0.20, P10=1000, P9=100, cost=24.99 (value_bulk), raw=80
    _planted_card(session, 1, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
    _add_raw_comp(session, 1, "80")
    ev = compute_grading_ev(session, card_id=1,
                            as_of=datetime(2026, 5, 1, tzinfo=timezone.utc))
    # net P10 = 1000*(1-0.1325)-0.30 = 867.20; net P9 = 100*(1-0.1325)-0.30 = 86.45
    # EV = 0.20*867.20 + 0.80*86.45 − 24.99 − 80 = 173.44 + 69.16 − 104.99 = 137.61
    assert abs(ev.ev - Decimal("137.61")) < Decimal("0.50")
    assert ev.raw_price == Decimal("80")
    assert ev.sample_size == 1000


def test_rank_excludes_negative_ev(session):
    session.add(Card(card_id=2, year=2020, manufacturer="Panini", set_name="Prizm",
                     card_number="2", parallel="Base", player_id=1))
    session.add(Card(card_id=3, year=2020, manufacturer="Panini", set_name="Prizm",
                     card_number="3", parallel="Base", player_id=1))
    session.commit()
    _planted_card(session, 2, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
    _add_raw_comp(session, 2, "80")
    _planted_card(session, 3, hedonic_p10=1000, hedonic_p9=100, gem_rate=0.20)
    _add_raw_comp(session, 3, "500")  # raw too expensive → negative EV
    df = rank_grading_candidates(
        session,
        as_of=datetime(2026, 5, 1, tzinfo=timezone.utc),
        min_ev_per_dollar=Decimal("0.15"),
    )
    assert 2 in df["card_id"].tolist()
    assert 3 not in df["card_id"].tolist()


def test_trend_adjustment_dampens_gem_rate_when_recent_share_drops(session):
    from sportscards.factors.grading_ev import trend_adjustment

    old = datetime(2025, 5, 1, tzinfo=timezone.utc)    # 365d-window start
    mid = datetime(2026, 2, 1, tzinfo=timezone.utc)    # ~90d ago
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # Cumulative pop at each snapshot (numbers are TOTAL, not increments):
    #   old: P10=50, P9=50  → snapshot share = 50/100 = 0.50
    #   mid: P10=150, P9=150
    #   now: P10=152, P9=248 → 90d window: ΔP10=2, ΔP9=98 → share = 2/100 = 0.02
    # 365d: ΔP10=102, ΔP9=198 → share = 102/300 ≈ 0.34
    # 0.34 − 0.02 = 0.32 ≥ 0.05 → adj = 0.02 / 0.34 ≈ 0.059 < 1.0  ✓
    _add_pop(session, 1, old, psa8=0, psa9=50, psa10=50)
    _add_pop(session, 1, mid, psa8=0, psa9=150, psa10=150)
    _add_pop(session, 1, now, psa8=0, psa9=248, psa10=152)

    adj = trend_adjustment(session, 1, now)
    assert adj < Decimal("1")
