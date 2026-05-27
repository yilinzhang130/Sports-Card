from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from sportscards.db.models import ExitSignal, PortfolioHolding, TradeTargets
from sportscards.db.session import session_scope

_ACQUIRED_AT = datetime(2026, 1, 1, tzinfo=UTC)


def test_trade_targets_roundtrip(migrated_db):
    with session_scope() as s:
        s.add(
            TradeTargets(
                card_id=1,
                as_of_date=date(2026, 5, 27),
                fair_value=Decimal("100.00"),
                bid_max=Decimal("90.00"),
                sell_target=Decimal("115.00"),
                stop_loss=Decimal("80.00"),
                confidence=Decimal("0.850"),
                half_spread_pct=Decimal("0.0250"),
                liquidity_margin_pct=Decimal("0.0500"),
            )
        )
    with session_scope() as s:
        got = s.execute(select(TradeTargets)).scalar_one()
        assert got.fair_value == Decimal("100.00")


def test_portfolio_holding_new_columns_default_null(migrated_db):
    with session_scope() as s:
        h = PortfolioHolding(
            card_id=1,
            acquired_at=_ACQUIRED_AT,
            acquired_cost_usd=Decimal("100"),
            channel="ebay",
        )
        s.add(h)
        s.flush()
        assert h.entry_factor_decile is None
        assert h.entry_liquidity_tier is None


def test_exit_signal_unique_per_rule_per_day(migrated_db):
    # First create the holding in its own session
    holding_id = None
    with session_scope() as s:
        h = PortfolioHolding(
            card_id=1,
            acquired_at=_ACQUIRED_AT,
            acquired_cost_usd=Decimal("100"),
            channel="ebay",
        )
        s.add(h)
        s.flush()
        holding_id = h.holding_id

    # Insert first signal
    with session_scope() as s:
        s.add(
            ExitSignal(
                holding_id=holding_id,
                rule_triggered="target_hit",
                recommended_action="sell_50pct",
                as_of_date=date(2026, 5, 27),
            )
        )

    # Second identical signal should raise IntegrityError (bubbles out of session_scope)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), session_scope() as s:
        s.add(
            ExitSignal(
                holding_id=holding_id,
                rule_triggered="target_hit",
                recommended_action="sell_50pct",
                as_of_date=date(2026, 5, 27),
            )
        )
