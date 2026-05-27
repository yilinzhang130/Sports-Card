from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from sportscards.db.models import (
    ExitSignal,
    FactorPanel,
    PortfolioHolding,
    TradeTargets,
    TxClean,
    TxRaw,
)
from sportscards.db.session import session_scope
from sportscards.pricing.exit_rules import evaluate_open_positions


def _make_holding(s, card_id, **kwargs):
    defaults = dict(
        card_id=card_id,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
        acquired_cost_usd=Decimal("100"),
        channel="ebay",
        status="held",
        entry_factor_decile=10,
        entry_liquidity_tier="A",
    )
    defaults.update(kwargs)
    h = PortfolioHolding(**defaults)
    s.add(h)
    s.flush()
    return h


def _add_last_sold(s, card_id, price, when):
    raw = TxRaw(
        source="ebay",
        external_id=f"r-{card_id}-{when.isoformat()}",
        raw_json={},
        raw_title="Test",
    )
    s.add(raw)
    s.flush()
    s.add(TxClean(
        raw_id=raw.raw_id, card_id=card_id, price_usd=Decimal(str(price)),
        sold_at=when, parser_confidence=Decimal("0.9"), parser_method="rule",
    ))
    s.flush()


def _add_targets(s, card_id, as_of, **prices):
    s.add(TradeTargets(
        card_id=card_id, as_of_date=as_of,
        fair_value=Decimal(str(prices.get("fair", 100))),
        bid_max=Decimal(str(prices.get("bid_max", 90))),
        sell_target=Decimal(str(prices.get("sell_target", 115))),
        stop_loss=Decimal(str(prices.get("stop_loss", 80))),
        confidence=Decimal("0.9"),
        half_spread_pct=Decimal("0.025"),
        liquidity_margin_pct=Decimal("0.05"),
    ))
    s.flush()


def _add_panel(s, card_id, as_of, **kwargs):
    defaults = dict(
        card_id=card_id, as_of_date=datetime.combine(as_of, datetime.min.time()),
        r30=Decimal("0"), r90=Decimal("0"), r365=Decimal("0"),
        cs_momentum_pct=Decimal("0.5"), is_hyped=False,
        sales_count_90d=10, dollar_volume_90d=Decimal("1000"),
        bid_ask_proxy=Decimal("0.05"), last_sale_recency_days=5,
        liquidity_tier="A",
    )
    defaults.update(kwargs)
    s.add(FactorPanel(**defaults))
    s.flush()


@pytest.mark.usefixtures("migrated_db")
def test_rule_target_hit_emits_sell_50pct(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 120, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of, sell_target=115)
        _add_panel(s, card_id, as_of, cs_momentum_pct=Decimal("0.6"))
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("target_hit", "sell_50pct") in rules


@pytest.mark.usefixtures("migrated_db")
def test_rule_factor_reversal_when_dropped_out_of_decile(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id, entry_factor_decile=10)
        _add_last_sold(s, card_id, 100, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        _add_panel(s, card_id, as_of, cs_momentum_pct=Decimal("0.05"))
        # Seed a second card with high momentum so the test card ranks below top decile
        from sportscards.db.models import Card, Player
        p2 = Player(name="Other Player")
        s.add(p2); s.flush()
        c2 = Card(year=2018, manufacturer="Panini", set_name="Prizm",
                  player_id=p2.player_id, card_number="9", parallel="Base")
        s.add(c2); s.flush()
        _add_panel(s, c2.card_id, as_of, cs_momentum_pct=Decimal("0.99"))
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("factor_reversal", "sell_100pct") in rules


@pytest.mark.usefixtures("migrated_db")
def test_rule_time_stop_after_540_days_no_gain(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(
            s, card_id,
            acquired_at=datetime(2024, 1, 1, tzinfo=UTC),
            acquired_cost_usd=Decimal("100"),
        )
        _add_last_sold(s, card_id, 105, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        _add_panel(s, card_id, as_of)
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("time_stop", "sell_100pct") in rules


@pytest.mark.usefixtures("migrated_db")
def test_rule_price_stop_when_below_stop_loss(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 70, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of, stop_loss=80)
        _add_panel(s, card_id, as_of)
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("price_stop", "sell_100pct") in rules


@pytest.mark.usefixtures("migrated_db")
def test_rule_liquidity_degrade(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id, entry_liquidity_tier="A")
        _add_last_sold(s, card_id, 100, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        _add_panel(s, card_id, as_of, liquidity_tier="C")
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("liquidity_degrade", "sell_100pct") in rules


@pytest.mark.usefixtures("migrated_db")
def test_unique_constraint_dedupes_reruns(seeded_card_and_index):
    """Running evaluate twice on the same day must not duplicate signals."""
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 120, datetime(2026, 5, 26, 12, 0, 0, tzinfo=UTC))
        _add_targets(s, card_id, as_of, sell_target=115)
        _add_panel(s, card_id, as_of)
        evaluate_open_positions(s, as_of)
        evaluate_open_positions(s, as_of)
        rows = s.execute(
            select(ExitSignal).where(ExitSignal.rule_triggered == "target_hit")
        ).scalars().all()
    assert len(rows) == 1
