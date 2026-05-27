from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from sportscards.db.models import (
    Card,
    FactorPanel,
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
        p = Player(name="Test Player")
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
            external_id="test-1",
            raw_title="Test Card",
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


@pytest.fixture
def seeded_stale_index():
    """Card with last_sold $100 30d ago, index last updated 30d ago (stale)."""
    with session_scope() as s:
        p = Player(name="Stale Player")
        s.add(p)
        s.flush()
        c = Card(
            year=2018,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="2",
            parallel="Base",
            player_id=p.player_id,
        )
        s.add(c)
        s.flush()
        now = datetime(2026, 5, 27, tzinfo=UTC)
        s.add(
            RepeatSalesIndex(
                sport="NBA",
                era="modern",
                bucket="weekly",
                grade_tier="PSA10",
                period_start=now - timedelta(days=30),
                index_value=Decimal("100"),
                n_pairs=0,
            )
        )
        raw = TxRaw(
            source="ebay",
            external_id="test-2",
            raw_title="Stale Card",
            raw_json={},
        )
        s.add(raw)
        s.flush()
        s.add(
            TxClean(
                raw_id=raw.raw_id,
                card_id=c.card_id,
                price_usd=Decimal("100"),
                sold_at=now - timedelta(days=30),
                parser_confidence=Decimal("0.9"),
                parser_method="rule",
            )
        )
        s.flush()
        return c.card_id


@pytest.fixture
def seeded_panel_with_pricing_inputs(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    now = datetime(2026, 5, 27, tzinfo=UTC)
    # Seed 6 extra transactions across different days so estimate_half_spread
    # computes a real Corwin-Schultz half-spread (not the tier fallback).
    # Prices vary each day to produce a non-degenerate high/low range.
    # Two transactions per day gives H > L so CS estimator produces a
    # non-zero spread; spread will differ from the tier-A fallback (0.03)
    # ensuring stop_loss < bid_max in the integration assertion.
    extra_sales = [
        (now - timedelta(days=d), price)
        for d, price in [
            (2, Decimal("90")),
            (2, Decimal("110")),   # day-2: H=110, L=90
            (3, Decimal("92")),
            (3, Decimal("108")),   # day-3: H=108, L=92
            (4, Decimal("94")),
            (4, Decimal("106")),   # day-4: H=106, L=94
        ]
    ]
    with session_scope() as s:
        for i, (sold_at, price) in enumerate(extra_sales):
            raw = TxRaw(
                source="ebay",
                external_id=f"panel-seed-{i}",
                raw_title="Test Card Extra",
                raw_json={},
            )
            s.add(raw)
            s.flush()
            s.add(
                TxClean(
                    raw_id=raw.raw_id,
                    card_id=card_id,
                    price_usd=price,
                    sold_at=sold_at,
                    parser_confidence=Decimal("0.9"),
                    parser_method="rule",
                )
            )
        s.add(
            FactorPanel(
                card_id=card_id,
                as_of_date=datetime(2026, 5, 27),
                r30=Decimal("0.05"),
                r90=Decimal("0.10"),
                r365=Decimal("0.30"),
                cs_momentum_pct=Decimal("0.6"),
                is_hyped=False,
                sales_count_90d=10,
                dollar_volume_90d=Decimal("1000"),
                bid_ask_proxy=Decimal("0.05"),
                last_sale_recency_days=1,
                liquidity_tier="A",
            )
        )
    return card_id


@pytest.fixture
def seeded_holding_for_flow(seeded_panel_with_pricing_inputs):
    from datetime import UTC, datetime

    from sportscards.db.models import PortfolioHolding

    card_id = seeded_panel_with_pricing_inputs
    with session_scope() as s:
        s.add(PortfolioHolding(
            card_id=card_id,
            acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
            acquired_cost_usd=Decimal("80"),
            channel="ebay",
            status="held",
            entry_factor_decile=10,
            entry_liquidity_tier="A",
        ))
    return card_id
