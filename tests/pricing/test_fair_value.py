import math
from datetime import date

import pytest

from sportscards.pricing.fair_value import (
    TAU_DAYS,
    blend,
    recency_confidence,
)


def test_recency_confidence_decays_exponentially():
    assert recency_confidence(0) == pytest.approx(1.0)
    assert recency_confidence(TAU_DAYS) == pytest.approx(math.e ** -1)
    assert recency_confidence(3 * TAU_DAYS) < 0.05


def test_blend_full_confidence_uses_index_projected():
    out = blend(index_projected=100.0, hedonic_predicted=80.0, confidence=1.0)
    assert out == pytest.approx(100.0)


def test_blend_zero_confidence_falls_back_to_hedonic():
    out = blend(index_projected=100.0, hedonic_predicted=80.0, confidence=0.0)
    assert out == pytest.approx(80.0)


def test_blend_midweight():
    out = blend(index_projected=100.0, hedonic_predicted=80.0, confidence=0.5)
    assert out == pytest.approx(90.0)


def test_blend_drops_hedonic_when_missing():
    out = blend(index_projected=100.0, hedonic_predicted=None, confidence=0.3)
    assert out == pytest.approx(100.0)


def test_blend_returns_none_when_both_missing():
    out = blend(index_projected=None, hedonic_predicted=None, confidence=0.5)
    assert out is None


@pytest.mark.usefixtures("migrated_db")
def test_compute_fair_value_uses_index_reprojection(seeded_card_and_index):
    """When index doubles between t_then and t_now, fair value doubles."""
    from sportscards.db.session import session_scope
    from sportscards.pricing.fair_value import compute_fair_value

    card_id, _ = seeded_card_and_index
    with session_scope() as s:
        fv = compute_fair_value(s, card_id, date(2026, 5, 27), hedonic_predicted=None)
    assert fv.index_projected == pytest.approx(200.0, rel=0.01)
    assert fv.fair_value == pytest.approx(200.0, rel=0.05)


@pytest.mark.usefixtures("migrated_db")
def test_compute_fair_value_stale_index_falls_back_to_hedonic(seeded_stale_index):
    from sportscards.db.session import session_scope
    from sportscards.pricing.fair_value import compute_fair_value

    card_id = seeded_stale_index
    with session_scope() as s:
        fv = compute_fair_value(s, card_id, date(2026, 5, 27), hedonic_predicted=150.0)
    assert fv.index_projected is None
    assert fv.confidence == 0.0
    assert fv.fair_value == pytest.approx(150.0)
