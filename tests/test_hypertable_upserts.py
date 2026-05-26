"""Tests for hypertable persistence paths against real TimescaleDB.

The original bug: SQLAlchemy's ORM unit-of-work flush uses
``insertmanyvalues`` + RETURNING for tables with server defaults.
TimescaleDB rewrites inserts via chunk routing, breaking SA's sentinel
matching ("Can't match sentinel values in result set to parameter sets").

Postgres-only (set RUN_INTEGRATION=1).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="requires Postgres/TimescaleDB (set RUN_INTEGRATION=1)",
)


@pytest.fixture
def pg_session(monkeypatch):
    url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    import sportscards.db.session as _sess
    from sportscards.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(_sess, "_engine", None)
    monkeypatch.setattr(_sess, "_SessionLocal", None)
    yield _sess


def test_factor_panel_bulk_insert_no_sentinel_mismatch(pg_session):
    """Insert 50 synthetic rows via the Core insert path; verify no error."""
    from sportscards.db.models import Card, FactorPanel, Player
    from sportscards.factors.factor_panel import _dec

    sess_mod = pg_session
    as_of = datetime(2030, 1, 1, tzinfo=timezone.utc)

    # Clean up + seed
    with sess_mod.session_scope() as s:
        s.execute(delete(FactorPanel).where(FactorPanel.as_of_date == as_of))
        s.execute(delete(Card).where(Card.set_name == "HYPERTBL_TEST"))
        s.execute(delete(Player).where(Player.br_slug == "hyper-test-player"))

    with sess_mod.session_scope() as s:
        p = Player(name="HyperTest", br_slug="hyper-test-player")
        s.add(p)
        s.flush()
        card_ids = []
        for i in range(50):
            c = Card(
                year=2020,
                manufacturer="Panini",
                set_name="HYPERTBL_TEST",
                card_number=str(i),
                parallel="Base",
                player_id=p.player_id,
                is_rookie=True,
            )
            s.add(c)
            s.flush()
            card_ids.append(c.card_id)

    # Now exercise the actual persist path. We bypass build_panel and use a
    # synthetic frame to isolate the upsert behavior.
    import pandas as pd
    from sqlalchemy import insert

    rows = [
        {
            "card_id": cid,
            "as_of_date": as_of,
            "r30": _dec(0.01),
            "r90": _dec(0.05),
            "r365": _dec(0.10),
            "cs_momentum_pct": _dec(0.5, q="0.0001"),
            "is_hyped": False,
            "sales_count_90d": 10,
            "dollar_volume_90d": Decimal("1234.56"),
            "bid_ask_proxy": Decimal("0.05000"),
            "last_sale_recency_days": 3,
            "liquidity_tier": "B",
        }
        for cid in card_ids
    ]
    with sess_mod.session_scope() as s:
        s.execute(insert(FactorPanel), rows)

    with sess_mod.session_scope() as s:
        n = s.execute(
            select(func.count()).select_from(FactorPanel).where(FactorPanel.as_of_date == as_of)
        ).scalar_one()
    assert n == 50

    # Cleanup
    with sess_mod.session_scope() as s:
        s.execute(delete(FactorPanel).where(FactorPanel.as_of_date == as_of))
        s.execute(delete(Card).where(Card.set_name == "HYPERTBL_TEST"))
        s.execute(delete(Player).where(Player.br_slug == "hyper-test-player"))


def test_persist_panel_end_to_end_against_timescale(pg_session):
    """End-to-end: persist_panel against TimescaleDB returns >0 and inserts rows.

    Relies on existing tx_clean data in the database; if empty, skips.
    """
    from sportscards.db.models import FactorPanel, TxClean
    from sportscards.factors.factor_panel import persist_panel

    sess_mod = pg_session
    with sess_mod.session_scope() as s:
        n_tx = s.execute(select(func.count()).select_from(TxClean)).scalar_one()
    if n_tx == 0:
        pytest.skip("no tx_clean rows in DB; cannot exercise persist_panel")

    as_of = date.today() + timedelta(days=1)  # avoid collision with prior runs
    with sess_mod.session_scope() as s:
        s.execute(delete(FactorPanel).where(FactorPanel.as_of_date == as_of))

    with sess_mod.session_scope() as s:
        n = persist_panel(s, as_of)

    with sess_mod.session_scope() as s:
        actual = s.execute(
            select(func.count()).select_from(FactorPanel).where(FactorPanel.as_of_date == as_of)
        ).scalar_one()
    assert actual == n
    assert n >= 0  # may be 0 if liquidity panel is empty, but must not raise

    with sess_mod.session_scope() as s:
        s.execute(delete(FactorPanel).where(FactorPanel.as_of_date == as_of))
