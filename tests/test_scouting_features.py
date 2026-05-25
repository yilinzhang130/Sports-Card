from datetime import UTC, datetime
from decimal import Decimal

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, PlayerStardomScore
from sportscards.factors.scouting_features import (
    get_stardom_premium_batch,
    get_stardom_premium_for_card,
)


@pytest.fixture()
def session() -> Session:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = Session(eng)
    p = Player(name="P", br_slug="p", draft_year=2023)
    s.add(p)
    s.flush()
    c = Card(
        year=2023,
        manufacturer="Panini",
        set_name="Prizm",
        card_number="1",
        parallel="Silver",
        player_id=p.player_id,
        is_rookie=True,
    )
    s.add(c)
    s.flush()
    s.add_all(
        [
            PlayerStardomScore(
                player_id=p.player_id,
                model_version="prism_v1",
                draft_year=2023,
                premium=Decimal("0.20"),
                percentile_rank=Decimal("0.80"),
                fit_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
            PlayerStardomScore(
                player_id=p.player_id,
                model_version="prism_v1.1",
                draft_year=2023,
                premium=Decimal("0.35"),
                percentile_rank=Decimal("0.90"),
                fit_at=datetime(2024, 6, 1, tzinfo=UTC),
            ),
        ]
    )
    s.commit()
    yield s
    s.close()


def test_returns_latest_premium_at_as_of(session):
    out = get_stardom_premium_for_card(session, card_id=1, as_of=datetime(2024, 7, 1, tzinfo=UTC))
    assert out == Decimal("0.35")


def test_excludes_future_fit_at(session):
    out = get_stardom_premium_for_card(session, card_id=1, as_of=datetime(2024, 3, 1, tzinfo=UTC))
    assert out == Decimal("0.20")


def test_returns_none_when_no_score(session):
    p2 = Player(name="Q", br_slug="q")
    session.add(p2)
    session.flush()
    c2 = Card(
        year=2020,
        manufacturer="Panini",
        set_name="Prizm",
        card_number="2",
        parallel="Base",
        player_id=p2.player_id,
    )
    session.add(c2)
    session.commit()
    assert (
        get_stardom_premium_for_card(
            session, card_id=c2.card_id, as_of=datetime(2024, 7, 1, tzinfo=UTC)
        )
        is None
    )


def test_batch_returns_series_indexed_by_card_id(session):
    s = get_stardom_premium_batch(session, card_ids=[1], as_of=datetime(2024, 7, 1, tzinfo=UTC))
    assert isinstance(s, pd.Series)
    assert s.loc[1] == Decimal("0.35")
