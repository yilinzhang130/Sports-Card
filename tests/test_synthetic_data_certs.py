"""Test that ``generate_synthetic_transactions`` produces repeat sales.

Without repeat sales (same cert_number appearing >1x across time), the
Mei-Moses repeat-sales regression returns 0 rows. The bug was that the
old generator set ``cert_number=None`` for every tx.
"""

from __future__ import annotations

from sqlalchemy import func, select

from sportscards.db.models import Card, Player, TxClean
from sportscards.db.session import session_scope
from sportscards.factors.synthetic_data import generate_synthetic_transactions


def _seed_cards(s, n: int = 3):
    p = Player(name="P", br_slug="p")
    s.add(p)
    s.flush()
    for i in range(n):
        s.add(
            Card(
                year=2020,
                manufacturer="Panini",
                set_name="Prizm",
                card_number=str(i),
                parallel="Base",
                player_id=p.player_id,
                is_rookie=True,
            )
        )
    s.flush()


def test_generates_repeat_sales(migrated_db):
    with session_scope() as s:
        _seed_cards(s, n=3)
        n = generate_synthetic_transactions(s, n_per_card=10, seed=7)
        assert n > 0

    with session_scope() as s:
        repeat_certs = s.execute(
            select(TxClean.cert_number, func.count().label("c"))
            .where(TxClean.cert_number.is_not(None))
            .group_by(TxClean.cert_number)
            .having(func.count() > 1)
        ).all()
    assert len(repeat_certs) >= 1, "expected at least one repeat-sale cert_number"


def test_all_tx_have_cert_number(migrated_db):
    with session_scope() as s:
        _seed_cards(s, n=2)
        generate_synthetic_transactions(s, n_per_card=5, seed=11)

    with session_scope() as s:
        n_null = s.execute(
            select(func.count()).select_from(TxClean).where(TxClean.cert_number.is_(None))
        ).scalar_one()
    assert n_null == 0
