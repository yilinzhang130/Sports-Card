from decimal import Decimal

import pandas as pd

from reports.app._components.overrides import list_overrides, set_override
from sportscards.db.session import session_scope
from sportscards.portfolio.construction import (
    AllocationConfig,
    UniverseSnapshot,
    apply_overrides,
    build_portfolio,
)


def _make_universe() -> UniverseSnapshot:
    anchors = pd.DataFrame({"card_id": [1, 2, 3]})
    return UniverseSnapshot(anchors_df=anchors)


def test_apply_overrides_replaces_weight(migrated_db):
    positions = build_portfolio(_make_universe(), AllocationConfig(total_aum_usd=1_000_000.0))
    target = positions[0].card_id
    set_override(target, Decimal("0.42"), reason="test", actor="ui")
    with session_scope() as s:
        applied = apply_overrides(positions, s)
    row = next(p for p in applied if p.card_id == target)
    assert float(row.target_weight_pct) == 0.42
    assert row.is_override is True
    # Untouched positions remain non-override
    other = next(p for p in applied if p.card_id != target)
    assert other.is_override is False


def test_apply_overrides_degrades_when_table_missing(migrated_db):
    positions = build_portfolio(_make_universe(), AllocationConfig(total_aum_usd=1_000_000.0))
    with session_scope() as s:
        s.execute(__import__("sqlalchemy").text("DROP TABLE portfolio_overrides"))
        applied = apply_overrides(positions, s)
    assert applied == positions  # unchanged, no crash


def test_list_overrides_roundtrip(migrated_db):
    set_override(7, Decimal("0.10"), reason="manual hold", actor="ui")
    rows = list_overrides()
    assert len(rows) == 1
    assert rows[0]["card_id"] == 7
    assert rows[0]["reason"] == "manual hold"


def test_portfolio_page_renders(migrated_db):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("reports/app/pages/2_💼_Portfolio.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
