from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from sportscards.db.models import FactorPanel, PortfolioHolding
from sportscards.db.session import session_scope


@pytest.mark.usefixtures("migrated_db")
def test_add_holding_copies_decile_and_tier_from_latest_panel(seeded_card_and_index):
    from reports.app._components.holdings import add_holding

    card_id, _ = seeded_card_and_index
    with session_scope() as s:
        s.add(FactorPanel(
            card_id=card_id,
            as_of_date=datetime(2026, 5, 27),
            r30=Decimal("0"), r90=Decimal("0"), r365=Decimal("0"),
            cs_momentum_pct=Decimal("0.95"),  # top decile
            is_hyped=False,
            sales_count_90d=10, dollar_volume_90d=Decimal("1000"),
            bid_ask_proxy=Decimal("0.05"), last_sale_recency_days=5,
            liquidity_tier="B",
        ))

    hid = add_holding(
        card_id=card_id,
        cert_number=None, slab_grader=None, slab_grade=None,
        acquired_at=datetime(2026, 5, 27, tzinfo=UTC),
        acquired_cost_usd=Decimal("100"),
        channel="ebay",
    )
    with session_scope() as s:
        h = s.execute(
            select(PortfolioHolding).where(PortfolioHolding.holding_id == hid)
        ).scalar_one()
        assert h.entry_factor_decile == 10
        assert h.entry_liquidity_tier == "B"


@pytest.mark.usefixtures("migrated_db")
def test_add_holding_handles_missing_panel(seeded_card_and_index):
    from reports.app._components.holdings import add_holding

    card_id, _ = seeded_card_and_index
    hid = add_holding(
        card_id=card_id,
        cert_number=None, slab_grader=None, slab_grade=None,
        acquired_at=datetime(2026, 5, 27, tzinfo=UTC),
        acquired_cost_usd=Decimal("100"),
        channel="ebay",
    )
    with session_scope() as s:
        h = s.execute(
            select(PortfolioHolding).where(PortfolioHolding.holding_id == hid)
        ).scalar_one()
        assert h.entry_factor_decile is None
        assert h.entry_liquidity_tier is None
