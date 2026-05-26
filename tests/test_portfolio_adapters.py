"""Tests for sportscards.portfolio.adapters.load_mispricing.

Covers the cross-phase schema fix: tx_mispricing has columns
(tx_id, model_version, residual, predicted_log_price, fit_at).
The adapter must join tx_clean + card_master to project the
(card_id, mispricing_residual, computed_at, sport, parallel_tier)
shape that portfolio.construction expects.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from sportscards.db.models import Card, Player, TxClean, TxMispricing, TxRaw
from sportscards.db.session import session_scope
from sportscards.portfolio.adapters import load_mispricing


def _seed(s):
    p = Player(name="Test Player", br_slug="t-player")
    s.add(p)
    s.flush()
    base_card = Card(
        year=2020,
        manufacturer="Panini",
        set_name="Prizm",
        card_number="1",
        parallel="Base",
        print_run=None,
        player_id=p.player_id,
        is_rookie=True,
        is_one_of_one=False,
    )
    rare_card = Card(
        year=2020,
        manufacturer="Panini",
        set_name="Prizm",
        card_number="1",
        parallel="Gold /10",
        print_run=10,
        player_id=p.player_id,
        is_rookie=True,
        is_one_of_one=False,
    )
    s.add_all([base_card, rare_card])
    s.flush()
    return base_card, rare_card


def _make_tx(s, card, price, sold_at, raw_ext_id):
    raw = TxRaw(
        source="ebay",
        raw_title="test",
        raw_price=Decimal(str(price)),
        sold_at=sold_at,
        external_id=raw_ext_id,
    )
    s.add(raw)
    s.flush()
    tx = TxClean(
        raw_id=raw.raw_id,
        card_id=card.card_id,
        slab_grader="PSA",
        slab_grade=Decimal("10"),
        cert_number=None,
        price_usd=Decimal(str(price)),
        sold_at=sold_at,
        parser_confidence=Decimal("0.9"),
        parser_method="regex",
    )
    s.add(tx)
    s.flush()
    return tx


def test_load_mispricing_joins_real_schema(migrated_db):
    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as s:
        base_card, rare_card = _seed(s)
        tx1 = _make_tx(s, base_card, 100.0, as_of - timedelta(days=10), "x1")
        tx2 = _make_tx(s, rare_card, 2000.0, as_of - timedelta(days=5), "x2")
        s.add(
            TxMispricing(
                tx_id=tx1.tx_id,
                model_version="v1",
                residual=Decimal("0.12"),
                predicted_log_price=Decimal("4.50"),
            )
        )
        s.add(
            TxMispricing(
                tx_id=tx2.tx_id,
                model_version="v1",
                residual=Decimal("-0.30"),
                predicted_log_price=Decimal("7.50"),
            )
        )

    with session_scope() as s:
        df = load_mispricing(s, as_of)

    assert df is not None
    assert set(df.columns) >= {
        "card_id",
        "mispricing_residual",
        "computed_at",
        "sport",
        "parallel_tier",
    }
    assert len(df) == 2
    row_base = df[df["card_id"] == base_card.card_id].iloc[0]
    row_rare = df[df["card_id"] == rare_card.card_id].iloc[0]
    assert float(row_base["mispricing_residual"]) == pytest.approx(0.12)
    assert float(row_rare["mispricing_residual"]) == pytest.approx(-0.30)
    assert (df["sport"] == "NBA").all()
    # Base parallel maps to tier 0, /10 parallel maps to tier 4
    assert int(row_base["parallel_tier"]) == 0
    assert int(row_rare["parallel_tier"]) == 4


def test_load_mispricing_takes_latest_per_card(migrated_db):
    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as s:
        base_card, _ = _seed(s)
        tx_old = _make_tx(s, base_card, 90.0, as_of - timedelta(days=20), "old")
        tx_new = _make_tx(s, base_card, 110.0, as_of - timedelta(days=2), "new")
        s.add(
            TxMispricing(
                tx_id=tx_old.tx_id,
                model_version="v1",
                residual=Decimal("0.01"),
                predicted_log_price=Decimal("4.5"),
            )
        )
        s.add(
            TxMispricing(
                tx_id=tx_new.tx_id,
                model_version="v1",
                residual=Decimal("0.20"),
                predicted_log_price=Decimal("4.7"),
            )
        )

    with session_scope() as s:
        df = load_mispricing(s, as_of)

    assert len(df) == 1
    assert float(df.iloc[0]["mispricing_residual"]) == pytest.approx(0.20)


def test_load_mispricing_excludes_post_as_of_sales(migrated_db):
    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as s:
        base_card, _ = _seed(s)
        tx_future = _make_tx(s, base_card, 110.0, as_of + timedelta(days=1), "future")
        s.add(
            TxMispricing(
                tx_id=tx_future.tx_id,
                model_version="v1",
                residual=Decimal("0.20"),
                predicted_log_price=Decimal("4.7"),
            )
        )

    with session_scope() as s:
        df = load_mispricing(s, as_of)

    assert df is None


def test_load_mispricing_returns_none_when_table_missing(migrated_db):
    # tx_mispricing table exists per migrations but has no rows
    as_of = datetime(2026, 1, 15, tzinfo=timezone.utc)
    with session_scope() as s:
        df = load_mispricing(s, as_of)
    assert df is None
