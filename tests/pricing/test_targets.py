
import pytest
from sqlalchemy import select

from sportscards.pricing.targets import (
    derive_targets,
)


def test_invariant_bid_max_lt_fair_lt_sell_target():
    out = derive_targets(
        fair_value=100.0, half_spread=0.025,
        liquidity_tier="A", factor_zscore=1.0,
    )
    assert out.bid_max < out.fair_value < out.sell_target


def test_stop_loss_below_bid_max():
    out = derive_targets(
        fair_value=100.0, half_spread=0.025,
        liquidity_tier="A", factor_zscore=0.0,
    )
    assert out.stop_loss < out.fair_value
    assert out.stop_loss < out.bid_max


def test_tier_D_has_wider_margin_than_tier_A():
    a = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=0.0)
    d = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="D", factor_zscore=0.0)
    assert d.bid_max < a.bid_max
    assert d.stop_loss < a.stop_loss


def test_sell_target_increases_with_factor_zscore():
    low = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=-2.0)
    high = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=+2.0)
    assert low.sell_target < high.sell_target


def test_exact_arithmetic():
    # half_spread=0.05, tier="B" (margin=0.05):
    # bid_max = 100*(1-0.10) = 90
    # tier_anchored_stop = 100*(1-0.10) = 90
    # stop_cap = 90 - 5 = 85
    # stop_loss = min(90, 85) = 85
    out = derive_targets(
        fair_value=100.0, half_spread=0.05,
        liquidity_tier="B", factor_zscore=1.0,
    )
    assert out.fair_value == pytest.approx(100.0)
    assert out.bid_max == pytest.approx(100 * (1 - 0.05 - 0.05))
    assert out.sell_target == pytest.approx(100 * (1 + 0.05 + 0.15))
    assert out.stop_loss == pytest.approx(85.0)


def test_stop_loss_capped_when_spread_exceeds_margin():
    out = derive_targets(
        fair_value=100.0,
        half_spread=0.10,        # 10% C-S spread
        liquidity_tier="A",      # margin = 0.03
        factor_zscore=0.0,
    )
    # bid_max = 100 * (1 - 0.10 - 0.03) = 87
    # tier_anchored_stop = 100 * (1 - 0.06) = 94  (would violate invariant)
    # stop_cap = 87 - 3 = 84
    assert out.bid_max == pytest.approx(87.0)
    assert out.stop_loss == pytest.approx(84.0)
    assert out.stop_loss < out.bid_max


@pytest.mark.usefixtures("migrated_db")
def test_persist_targets_writes_rows_for_panel(seeded_panel_with_pricing_inputs):
    from datetime import date as _date

    from sportscards.db.models import TradeTargets
    from sportscards.db.session import session_scope
    from sportscards.pricing.targets import persist_targets_for_panel

    as_of = _date(2026, 5, 27)
    with session_scope() as s:
        n = persist_targets_for_panel(s, as_of)
        assert n == 1
    with session_scope() as s:
        row = s.execute(
            select(TradeTargets).where(TradeTargets.as_of_date == as_of)
        ).scalar_one()
        assert float(row.fair_value) > 0
        assert float(row.bid_max) < float(row.fair_value) < float(row.sell_target)
        assert float(row.stop_loss) < float(row.fair_value)
