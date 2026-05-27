from datetime import date
from decimal import Decimal

import pytest

from sportscards.db.session import session_scope
from sportscards.reports.queries import get_open_exit_signals, get_trade_targets


@pytest.mark.usefixtures("migrated_db")
def test_get_trade_targets_returns_rows(seeded_panel_with_pricing_inputs):
    from sportscards.pricing.targets import persist_targets_for_panel
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        persist_targets_for_panel(s, as_of)
    df = get_trade_targets(card_ids=[seeded_panel_with_pricing_inputs], as_of=as_of)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["bid_max"] < row["fair_value"]
    assert row["fair_value"] < row["sell_target"]


@pytest.mark.usefixtures("migrated_db")
def test_resolve_exit_signal_is_idempotent_under_double_click():
    from datetime import UTC, datetime

    from sportscards.db.models import ExitSignal, PortfolioHolding
    from sportscards.reports.queries import resolve_exit_signal
    from sqlalchemy import select

    with session_scope() as s:
        h = PortfolioHolding(
            card_id=1, acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
            acquired_cost_usd=Decimal("100"), channel="ebay", status="held",
        )
        s.add(h)
        s.flush()
        sig = ExitSignal(
            holding_id=h.holding_id, rule_triggered="target_hit",
            recommended_action="sell_50pct", as_of_date=date(2026, 5, 27),
        )
        s.add(sig)
        s.flush()
        sig_id = sig.id

    assert resolve_exit_signal(sig_id) is True   # first click resolves
    assert resolve_exit_signal(sig_id) is False  # second click no-ops

    with session_scope() as s:
        sig = s.execute(select(ExitSignal).where(ExitSignal.id == sig_id)).scalar_one()
        assert sig.resolved_at is not None


@pytest.mark.usefixtures("migrated_db")
def test_get_open_exit_signals_only_returns_unresolved(seeded_holding_for_flow):
    from datetime import UTC, datetime

    from sportscards.db.models import ExitSignal

    as_of = date(2026, 5, 27)
    with session_scope() as s:
        s.add(ExitSignal(
            holding_id=1,
            rule_triggered="target_hit",
            recommended_action="sell_50pct",
            as_of_date=as_of,
            resolved_at=None,
        ))
        s.add(ExitSignal(
            holding_id=1,
            rule_triggered="price_stop",
            recommended_action="sell_100pct",
            as_of_date=as_of,
            resolved_at=datetime(2026, 5, 27, tzinfo=UTC),
        ))
    df = get_open_exit_signals()
    assert df["resolved_at"].isna().all()
    assert len(df) == 1
