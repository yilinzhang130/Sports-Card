from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import (
    Base,
    Card,
    Player,
    PlayerStardomScore,
    PopSnapshot,
    TxClean,
    TxRaw,
)
from sportscards.factors.features import build_features
from sportscards.factors.hedonic import MODEL_VERSION, fit, predict
from sportscards.factors.synthetic_data import generate_synthetic_transactions


@pytest.fixture()
def session() -> Session:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = Session(eng)
    yield s
    s.close()


def _seed_one(s: Session, *, player_premium_fit_at, tx_sold_at):
    p = Player(
        name="Rookie", br_slug="rookie", draft_year=2023, dob=datetime(2003, 1, 1), team="LAL"
    )
    s.add(p)
    s.flush()
    c = Card(
        year=2023,
        set_name="Prizm",
        card_number="1",
        parallel="Silver",
        manufacturer="Panini",  # NOT NULL in card_master
        player_id=p.player_id,
        is_rookie=True,
    )
    s.add(c)
    s.flush()
    raw = TxRaw(
        source="test",
        raw_title="x",
        raw_price=Decimal("500"),
        raw_currency="USD",
        sold_at=tx_sold_at,
        external_id="ext-1",
        raw_json={},
    )
    s.add(raw)
    s.flush()
    s.add(
        TxClean(
            raw_id=raw.raw_id,
            card_id=c.card_id,
            sold_at=tx_sold_at,
            price_usd=Decimal("500"),
            slab_grader="PSA",
            slab_grade=Decimal("10"),
            parser_confidence=Decimal("1.000"),
            parser_method="test",
        )
    )
    s.add(
        PopSnapshot(
            card_id=c.card_id, grader="PSA", grade=10, snapshot_date=tx_sold_at, pop_count=100
        )
    )
    s.add(
        PlayerStardomScore(
            player_id=p.player_id,
            model_version="prism_v1",
            draft_year=2023,
            premium=Decimal("0.40"),
            percentile_rank=Decimal("0.95"),
            fit_at=player_premium_fit_at,
        )
    )
    s.commit()
    return c.card_id


def test_features_include_stardom_columns(session):
    _seed_one(
        session,
        player_premium_fit_at=datetime(2024, 1, 1, tzinfo=UTC),
        tx_sold_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    df = build_features(session)
    assert {"stardom_premium", "has_stardom_score", "stardom_premium_x_is_rookie"}.issubset(
        df.columns
    )
    row = df.iloc[0]
    assert bool(row["has_stardom_score"]) is True
    assert float(row["stardom_premium"]) == pytest.approx(0.40)
    assert float(row["stardom_premium_x_is_rookie"]) == pytest.approx(0.40)


def test_no_lookahead_future_score_excluded(session):
    _seed_one(
        session,
        player_premium_fit_at=datetime(2024, 12, 1, tzinfo=UTC),
        tx_sold_at=datetime(2024, 6, 1, tzinfo=UTC),
    )
    df = build_features(session)
    row = df.iloc[0]
    assert bool(row["has_stardom_score"]) is False
    # missing premium fall back to cohort median = 0 when no peer has a score
    assert float(row["stardom_premium"]) == pytest.approx(0.0)


# --- Hedonic v2 model tests ---


def test_model_version_is_v3():
    assert MODEL_VERSION == "hedonic_v3"


def test_high_stardom_lifts_rookie_fitted_price(session):
    import importlib.util
    import pathlib

    from sqlalchemy import select as _select

    from sportscards.db.models import Card as _Card
    from sportscards.db.models import PlayerStardomScore as _PSS
    from sportscards.db.models import TxClean as _TC

    _spec = importlib.util.spec_from_file_location(
        "_test_hedonic_helper",
        pathlib.Path(__file__).parent / "test_hedonic.py",
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    _mod._seed(session)
    session.commit()
    generate_synthetic_transactions(session, n_per_card=20, seed=11)
    rookie_card = session.execute(
        _select(_Card).where(_Card.is_rookie == True).limit(1)  # noqa: E712
    ).scalar_one()
    first_sale = session.execute(
        _select(_TC.sold_at)
        .where(_TC.card_id == rookie_card.card_id)
        .order_by(_TC.sold_at)
        .limit(1)
    ).scalar_one()
    session.add(
        _PSS(
            player_id=rookie_card.player_id,
            model_version="prism_v1",
            draft_year=2023,
            premium=Decimal("0.50"),
            percentile_rank=Decimal("0.99"),
            fit_at=first_sale,
        )
    )
    session.commit()

    df = build_features(session)
    model, enc, _ = fit(df, n_trials=4)
    pred = predict(model, enc, df)
    df = df.assign(pred=pred)
    pid = rookie_card.player_id
    star_mean = df.loc[(df.player_id == pid) & (df.is_rookie == 1), "pred"].mean()
    other_mean = df.loc[(df.player_id != pid) & (df.is_rookie == 1), "pred"].mean()
    assert star_mean > other_mean, (star_mean, other_mean)
