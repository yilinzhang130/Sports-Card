"""Tests for momentum factor (trailing returns + cross-sectional ranks)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, TxClean, TxRaw
from sportscards.factors.momentum import card_returns, compute_cs_momentum


def _player(sess: Session, slug: str) -> int:
    p = Player(name=f"P-{slug}", br_slug=slug, draft_year=2020, draft_pick=10, team="LAL")
    sess.add(p)
    sess.flush()
    return p.player_id


def _card(sess: Session, pid: int, number: str) -> int:
    c = Card(
        year=2022,
        manufacturer="Panini",
        set_name="Prizm",
        card_number=number,
        parallel="Base",
        player_id=pid,
        is_rookie=True,
    )
    sess.add(c)
    sess.flush()
    return c.card_id


def _sale(sess: Session, card_id: int, sold_at: datetime, price: float) -> None:
    raw = TxRaw(source="t", raw_title="x", raw_price=Decimal(str(price)), sold_at=sold_at)
    sess.add(raw)
    sess.flush()
    sess.add(
        TxClean(
            raw_id=raw.raw_id,
            card_id=card_id,
            slab_grader="PSA",
            slab_grade=Decimal("10"),
            price_usd=Decimal(str(round(price, 2))),
            sold_at=sold_at,
            parser_method="t",
            parser_confidence=Decimal("1.000"),
        )
    )


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    yield sess
    sess.close()


AS_OF = datetime(2024, 12, 1)


def _seed_card_with_return(
    sess: Session, slug: str, number: str, start_price: float, end_price: float
) -> int:
    pid = _player(sess, slug)
    cid = _card(sess, pid, number)
    # 4 sales early in the 90d window at start_price, 4 sales near as_of at end_price.
    for d in range(80, 70, -3):
        _sale(sess, cid, AS_OF - timedelta(days=d), start_price)
    for d in range(15, 5, -3):
        _sale(sess, cid, AS_OF - timedelta(days=d), end_price)
    return cid


def test_top_decile_card_ranked_in_top_decile(session):
    # 9 flat cards (return ~0%) and 1 winner (+50%)
    flat_ids = [
        _seed_card_with_return(session, f"flat{i}", f"f{i}", 100.0, 100.0) for i in range(9)
    ]
    winner = _seed_card_with_return(session, "win", "w1", 100.0, 150.0)
    session.commit()

    panel = compute_cs_momentum(session, AS_OF, lookback=90)
    assert not panel.empty
    win_row = panel[panel["card_id"] == winner].iloc[0]
    assert win_row["cs_momentum_pct"] >= 0.9, panel
    # All flats should rank below the winner
    for fid in flat_ids:
        assert (
            panel[panel["card_id"] == fid].iloc[0]["cs_momentum_pct"] <= win_row["cs_momentum_pct"]
        )


def test_hyped_flag_set_when_r7_more_than_double_r30(session):
    pid = _player(session, "hype")
    cid = _card(session, pid, "h1")
    # 30d ago: $100; 25d ago: $100; 10d ago: $102 (small r30 ≈ +2%);
    # but 1 day ago: $300 → r7 huge spike vs r30.
    for d in (30, 25, 20):
        _sale(session, cid, AS_OF - timedelta(days=d), 100.0)
    _sale(session, cid, AS_OF - timedelta(days=10), 102.0)
    _sale(session, cid, AS_OF - timedelta(days=1), 300.0)
    session.commit()

    panel = compute_cs_momentum(session, AS_OF, lookback=90, min_sales=3)
    row = panel[panel["card_id"] == cid].iloc[0]
    assert bool(row["is_hyped"]), row


def test_card_returns_30_90(session):
    pid = _player(session, "ret")
    cid = _card(session, pid, "r1")
    # Two well-separated weekly buckets in the 90d window at $100 then $120.
    for d in (85, 80):
        _sale(session, cid, AS_OF - timedelta(days=d), 100.0)
    for d in (10, 5):
        _sale(session, cid, AS_OF - timedelta(days=d), 120.0)
    session.commit()

    out = card_returns(session, cid, AS_OF)
    assert out["r90"] is not None
    assert float(out["r90"]) == pytest.approx(0.2, abs=0.01)


def test_asof_safety_ignores_future_sales(session):
    pid = _player(session, "future")
    cid = _card(session, pid, "x1")
    # Past sale that should be visible
    _sale(session, cid, AS_OF - timedelta(days=30), 100.0)
    _sale(session, cid, AS_OF - timedelta(days=5), 100.0)
    # Sale after as_of that MUST be excluded
    _sale(session, cid, AS_OF + timedelta(days=10), 999.0)
    session.commit()

    panel = compute_cs_momentum(session, AS_OF, lookback=90, min_sales=2)
    # No spike — the future $999 sale must not contribute
    row = panel[panel["card_id"] == cid]
    assert not row.empty
    r90 = row.iloc[0]["r90"]
    # Should be ~0, definitely not the 9x ratio that would happen with leakage
    assert abs(float(r90)) < 0.5
