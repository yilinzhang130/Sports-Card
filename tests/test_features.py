"""Tests for hedonic feature engineering."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, PlayerEvent, PopSnapshot, TxClean, TxRaw
from sportscards.factors.features import (
    build_features,
    parallel_tier,
    set_tier,
    team_market,
)
from sportscards.factors.synthetic_data import generate_synthetic_transactions

# ---------------------------------------------------------------------------
# In-memory SQLite fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    _seed(sess)
    sess.commit()
    yield sess
    sess.close()


def _seed(sess: Session) -> None:
    # Players: rookie modern, vet modern, vintage
    players = [
        Player(
            name="Modern Rookie",
            position="SG",
            draft_year=2022,
            draft_pick=1,
            team="LAL",
            dob=datetime(2003, 6, 1),
            br_slug="modernrook",
        ),
        Player(
            name="Modern Vet",
            position="PG",
            draft_year=2009,
            draft_pick=15,
            team="GSW",
            dob=datetime(1988, 3, 14),
            br_slug="modernvet",
        ),
        Player(
            name="Vintage Star",
            position="SF",
            draft_year=1996,
            draft_pick=13,
            team="LAL",
            dob=datetime(1978, 8, 23),
            br_slug="vintagestar",
        ),
    ]
    for p in players:
        sess.add(p)
    sess.flush()

    pid_rookie, pid_vet, pid_vintage = (p.player_id for p in players)

    cards = [
        # Modern rookie variants
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="100",
            parallel="Base",
            player_id=pid_rookie,
            is_rookie=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="100",
            parallel="Silver Refractor",
            player_id=pid_rookie,
            is_rookie=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Select",
            card_number="200",
            parallel="Gold",
            print_run=10,
            player_id=pid_rookie,
            is_rookie=True,
            has_auto=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="National Treasures",
            card_number="50",
            parallel="Logoman",
            print_run=1,
            player_id=pid_rookie,
            is_rookie=True,
            has_auto=True,
            has_patch=True,
            is_one_of_one=True,
        ),
        Card(
            year=2022,
            manufacturer="Panini",
            set_name="Optic",
            card_number="55",
            parallel="Holo",
            print_run=25,
            player_id=pid_rookie,
            is_rookie=True,
        ),
        # Modern vet
        Card(
            year=2015,
            manufacturer="Panini",
            set_name="Prizm",
            card_number="33",
            parallel="Base",
            player_id=pid_vet,
        ),
        Card(
            year=2018,
            manufacturer="Panini",
            set_name="Donruss",
            card_number="44",
            parallel="Base",
            player_id=pid_vet,
        ),
        Card(
            year=2019,
            manufacturer="Panini",
            set_name="Flawless",
            card_number="7",
            parallel="Ruby",
            print_run=15,
            player_id=pid_vet,
            has_patch=True,
        ),
        # Vintage (pre-2010)
        Card(
            year=2003,
            manufacturer="Topps",
            set_name="Topps Chrome",
            card_number="111",
            parallel="Refractor",
            print_run=99,
            player_id=pid_vintage,
            is_rookie=True,
        ),
        Card(
            year=2008,
            manufacturer="Panini",
            set_name="Hoops",
            card_number="22",
            parallel="Base",
            player_id=pid_vintage,
        ),
    ]
    for c in cards:
        sess.add(c)
    sess.flush()


# ---------------------------------------------------------------------------
# Tier mapping unit tests
# ---------------------------------------------------------------------------


class _CardStub:
    def __init__(self, is_one_of_one=False, print_run=None, parallel="Base"):
        self.is_one_of_one = is_one_of_one
        self.print_run = print_run
        self.parallel = parallel


def test_parallel_tier_mapping():
    assert parallel_tier(_CardStub(is_one_of_one=True)) == 5
    assert parallel_tier(_CardStub(print_run=10)) == 4
    assert parallel_tier(_CardStub(print_run=25)) == 3
    assert parallel_tier(_CardStub(print_run=99)) == 2
    assert parallel_tier(_CardStub(parallel="Silver Refractor")) == 1
    assert parallel_tier(_CardStub(parallel="Base")) == 0


def test_set_tier_mapping():
    assert set_tier("2022 National Treasures") == "premium"
    assert set_tier("Flawless") == "premium"
    assert set_tier("Prizm") == "flagship"
    assert set_tier("Topps Chrome") == "flagship"
    assert set_tier("Donruss Optic") == "mid"
    assert set_tier("Optic") == "mid"
    assert set_tier("Donruss") == "low"
    assert set_tier("Hoops") == "low"
    # unknown -> flagship default
    assert set_tier("Some Random Set") == "flagship"


def test_team_market_mapping():
    assert team_market("LAL") == "premium"
    assert team_market("BOS") == "premium"
    assert team_market("GSW") == "standard"
    assert team_market(None) == "standard"


# ---------------------------------------------------------------------------
# build_features tests
# ---------------------------------------------------------------------------


def test_build_features_columns_present(session):
    generate_synthetic_transactions(session, n_per_card=5, seed=7)
    session.commit()
    df = build_features(session)
    expected = {
        "tx_id",
        "sold_at",
        "log_price",
        "log_pop_psa10",
        "log_pop_psa9_or_better",
        "parallel_tier",
        "print_run_log",
        "slab_grade",
        "player_age_at_sale",
        "years_since_draft",
        "draft_pick",
        "is_rookie",
        "has_auto",
        "has_patch",
        "is_one_of_one",
        "era_modern",
        "set_tier",
        "team_market",
        "slab_grader",
        "catalyst_score",
        "catalyst_score_30d_change",
    }
    assert expected.issubset(set(df.columns))
    assert np.isfinite(df["log_price"]).all()
    assert set(df["era_modern"].unique()).issubset({0, 1})
    assert set(df["is_rookie"].unique()).issubset({0, 1})


def test_build_features_row_count(session):
    n = generate_synthetic_transactions(session, n_per_card=5, seed=7)
    session.commit()
    df = build_features(session)
    # All synthetic tx are PSA 9 or 10 by construction.
    assert len(df) == n


def test_build_features_no_leakage(session):
    """A snapshot dated AFTER the sale must NOT be selected by the asof join."""
    # Use the existing seeded players + cards (no synthetic tx).
    card = session.query(Card).first()
    raw_id_start = 1
    # Construct sale at t.
    t = datetime(2024, 6, 15, 12, 0, 0)
    # Snapshot BEFORE sale (should be chosen): pop=42
    # Snapshot AFTER  sale (should be ignored): pop=999
    session.add(
        PopSnapshot(
            snapshot_date=t - timedelta(days=30),
            card_id=card.card_id,
            grader="PSA",
            grade=Decimal("10"),
            pop_count=42,
        )
    )
    session.add(
        PopSnapshot(
            snapshot_date=t + timedelta(days=30),
            card_id=card.card_id,
            grader="PSA",
            grade=Decimal("10"),
            pop_count=999,
        )
    )
    session.add(
        PopSnapshot(
            snapshot_date=t - timedelta(days=30),
            card_id=card.card_id,
            grader="PSA",
            grade=Decimal("9"),
            pop_count=20,
        )
    )

    raw = TxRaw(
        source="test",
        raw_title="x",
        raw_price=Decimal("100.00"),
        raw_currency="USD",
        sold_at=t,
        external_id=f"test-{raw_id_start}",
        raw_json={},
    )
    session.add(raw)
    session.flush()
    session.add(
        TxClean(
            raw_id=raw.raw_id,
            card_id=card.card_id,
            slab_grader="PSA",
            slab_grade=Decimal("10"),
            price_usd=Decimal("100.00"),
            sold_at=t,
            parser_confidence=Decimal("1.000"),
            parser_method="test",
        )
    )
    session.commit()

    df = build_features(session)
    # Should have exactly one row (only PSA 10 tx in the DB).
    assert len(df) == 1
    row = df.iloc[0]
    # Pop value used should be 42 (the BEFORE snapshot), NOT 999.
    expected_log_pop = np.log1p(42)
    assert row["log_pop_psa10"] == pytest.approx(expected_log_pop)
    # And pop_psa9_or_better = 42 + 20 = 62
    assert row["log_pop_psa9_or_better"] == pytest.approx(np.log1p(62))


def test_build_features_catalyst_columns(session):
    """Catalyst score should reflect a recent MVP event for player A; zero for B."""
    # Player A: has MVP event 10 days before the sale → 0.5 * 0.5**(10/90).
    # Player B: no events → 0.
    players = session.query(Player).all()
    player_a = players[0]  # Modern Rookie (LAL)
    player_b = players[1]  # Modern Vet (GSW)
    cards = session.query(Card).all()
    card_a = next(c for c in cards if c.player_id == player_a.player_id)
    card_b = next(c for c in cards if c.player_id == player_b.player_id)

    t = datetime(2024, 6, 15, 12, 0, 0)

    session.add(
        PlayerEvent(
            player_id=player_a.player_id,
            event_type="mvp",
            event_date=t - timedelta(days=10),
            event_payload={},
        )
    )

    for card, ext in ((card_a, "a"), (card_b, "b")):
        raw = TxRaw(
            source="test",
            raw_title=f"cat-{ext}",
            raw_price=Decimal("100.00"),
            raw_currency="USD",
            sold_at=t,
            external_id=f"cat-{ext}",
            raw_json={},
        )
        session.add(raw)
        session.flush()
        session.add(
            TxClean(
                raw_id=raw.raw_id,
                card_id=card.card_id,
                slab_grader="PSA",
                slab_grade=Decimal("10"),
                price_usd=Decimal("100.00"),
                sold_at=t,
                parser_confidence=Decimal("1.000"),
                parser_method="test",
            )
        )
    session.commit()

    df = build_features(session)
    assert "catalyst_score" in df.columns
    assert "catalyst_score_30d_change" in df.columns

    # Find rows by looking up tx prices/timing — we have exactly 2 PSA 10
    # tx in the DB now.
    assert len(df) == 2

    # player A expected MVP contribution at age 10 days from end-of-day UTC.
    # _as_datetime_utc(date) maps a date to 23:59:59.999999; tx sold_at is
    # 12:00:00 UTC same calendar day. as_of in features.py uses normalized
    # day (00:00:00). Event is at t-10d 12:00. age in days from day 00:00 is
    # 10 - 0.5 = 9.5 days (sold_at_day at 00:00, event at 12:00 of day-10).
    # Use the actual computed value rather than recomputing the decay here;
    # assert it's in the right ballpark.
    # Identify A and B rows by joining back through cards->players is complex;
    # simpler: one has nonzero catalyst, the other is zero.
    nonzero = df[df["catalyst_score"] != 0]
    zero = df[df["catalyst_score"] == 0]
    assert len(nonzero) == 1
    assert len(zero) == 1
    # Expected ≈ 0.5 * 0.5 ** (9.5/90) ≈ 0.4647 (sold_at_day normalize → 00:00)
    expected = 0.5 * (0.5 ** (9.5 / 90))
    assert nonzero.iloc[0]["catalyst_score"] == pytest.approx(expected, rel=0.01)
