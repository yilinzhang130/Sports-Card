"""Tests for liquidity factor (sales count, bid-ask proxy, tiering)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, TxClean, TxRaw
from sportscards.factors.liquidity import (
    compute_liquidity_panel,
    liquidity_metrics,
)
from sportscards.portfolio.construction import _apply_liquidity_hype_filters


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


def test_zero_sales_in_window_is_tier_D(session):
    pid = _player(session, "dead")
    cid = _card(session, pid, "d1")
    # Sale older than the 90d window
    _sale(session, cid, AS_OF - timedelta(days=200), 100.0)
    session.commit()

    out = liquidity_metrics(session, cid, AS_OF)
    assert out["sales_count_90d"] == 0
    assert out["liquidity_tier"] == "D"


def test_tier_a_threshold_10_sales(session):
    pid = _player(session, "active")
    cid = _card(session, pid, "a1")
    for d in range(80, 0, -8):  # 10 sales spread across window
        _sale(session, cid, AS_OF - timedelta(days=d), 100.0)
    session.commit()

    out = liquidity_metrics(session, cid, AS_OF)
    assert out["sales_count_90d"] == 10
    assert out["liquidity_tier"] == "A"


def test_bid_ask_proxy_tight_vs_wide(session):
    # Tight: 10 sales at ~$100 (small spread)
    pid_t = _player(session, "tight")
    cid_t = _card(session, pid_t, "t1")
    tight_prices = [98, 99, 100, 100, 100, 100, 101, 101, 102, 103]
    for i, p in enumerate(tight_prices):
        _sale(session, cid_t, AS_OF - timedelta(days=80 - i * 5), float(p))

    # Wide: 10 sales scattered from $50 to $300
    pid_w = _player(session, "wide")
    cid_w = _card(session, pid_w, "w1")
    wide_prices = [50, 70, 90, 100, 110, 120, 140, 180, 240, 300]
    for i, p in enumerate(wide_prices):
        _sale(session, cid_w, AS_OF - timedelta(days=80 - i * 5), float(p))
    session.commit()

    tight = liquidity_metrics(session, cid_t, AS_OF)
    wide = liquidity_metrics(session, cid_w, AS_OF)
    assert tight["bid_ask_proxy"] is not None
    assert wide["bid_ask_proxy"] is not None
    assert float(tight["bid_ask_proxy"]) < float(wide["bid_ask_proxy"]) / 4


def test_compute_liquidity_panel_universe_includes_inactive(session):
    pid_a = _player(session, "act")
    cid_a = _card(session, pid_a, "x1")
    for d in range(80, 0, -8):
        _sale(session, cid_a, AS_OF - timedelta(days=d), 100.0)
    pid_d = _player(session, "dead")
    cid_d = _card(session, pid_d, "x2")
    session.commit()

    panel = compute_liquidity_panel(session, AS_OF)
    assert set(panel["card_id"]) == {cid_a, cid_d}
    tiers = dict(zip(panel["card_id"], panel["liquidity_tier"], strict=True))
    assert tiers[cid_a] == "A"
    assert tiers[cid_d] == "D"


def test_portfolio_filter_excludes_tier_d():
    import pandas as pd

    df = pd.DataFrame(
        [
            {"card_id": 1, "liquidity_tier": "A", "is_hyped": False, "mispricing_residual": 0.5},
            {"card_id": 2, "liquidity_tier": "D", "is_hyped": False, "mispricing_residual": 0.9},
            {"card_id": 3, "liquidity_tier": "B", "is_hyped": True, "mispricing_residual": 0.7},
        ]
    )
    long = _apply_liquidity_hype_filters(df, drop_hyped=True)
    assert set(long["card_id"]) == {1}  # 2 dropped (tier D), 3 dropped (hyped)
    short = _apply_liquidity_hype_filters(df, drop_hyped=False)
    assert set(short["card_id"]) == {1, 3}  # only tier D dropped


def test_asof_safety_excludes_future_sales(session):
    pid = _player(session, "f")
    cid = _card(session, pid, "f1")
    _sale(session, cid, AS_OF - timedelta(days=10), 100.0)
    _sale(session, cid, AS_OF + timedelta(days=20), 100.0)  # future
    session.commit()

    out = liquidity_metrics(session, cid, AS_OF)
    assert out["sales_count_90d"] == 1
