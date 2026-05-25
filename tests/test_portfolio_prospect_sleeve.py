from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Card, Player, PlayerStardomScore
from sportscards.portfolio.adapters import load_stardom


@pytest.fixture()
def session() -> Session:
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    s = Session(eng)
    yield s
    s.close()


def _seed_player_with_rcs(s, *, name, slug, draft_year, premium, sets):
    p = Player(name=name, br_slug=slug, draft_year=draft_year)
    s.add(p); s.flush()
    cards = []
    for (set_name, parallel) in sets:
        c = Card(year=draft_year, set_name=set_name, card_number="1",
                 parallel=parallel, manufacturer="Panini",
                 player_id=p.player_id, is_rookie=True)
        s.add(c); s.flush()
        cards.append(c)
    s.add(PlayerStardomScore(
        player_id=p.player_id, model_version="prism_v1",
        draft_year=draft_year, premium=Decimal(str(premium)),
        percentile_rank=Decimal("0.90"),
        fit_at=datetime(draft_year, 12, 1, tzinfo=timezone.utc),
    ))
    s.commit()
    return p, cards


def _player_name(s, pid):
    return s.get(Player, int(pid)).name


def test_load_stardom_picks_prizm_silver_when_available(session):
    p, cards = _seed_player_with_rcs(
        session, name="A", slug="a", draft_year=2024, premium=0.5,
        sets=[("Topps Chrome", "Base"), ("Prizm", "Base"), ("Prizm", "Silver")],
    )
    df = load_stardom(session, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert df is not None
    silver_id = next(c.card_id for c in cards
                     if c.set_name == "Prizm" and c.parallel == "Silver")
    assert set(df["card_id"]) == {silver_id}


def test_load_stardom_excludes_outside_rookie_window(session):
    _seed_player_with_rcs(
        session, name="Old", slug="old", draft_year=2019, premium=0.9,
        sets=[("Prizm", "Silver")],
    )
    _seed_player_with_rcs(
        session, name="New", slug="new", draft_year=2023, premium=0.4,
        sets=[("Prizm", "Silver")],
    )
    df = load_stardom(session, as_of=datetime(2025, 6, 1, tzinfo=timezone.utc))
    assert df is not None
    names = {_player_name(session, pid) for pid in df["player_id"]}
    assert "New" in names
    assert "Old" not in names


def test_load_stardom_returns_none_when_empty(session):
    assert load_stardom(
        session, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc)) is None


def test_load_stardom_returns_none_when_table_missing():
    """If player_stardom_score table doesn't exist (fresh DB / migrations not
    run), load_stardom returns None rather than raising."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    # Build a DB with only Card/Player tables, not PlayerStardomScore.
    from sportscards.db.models import Card, Player
    eng = create_engine("sqlite:///:memory:")
    Card.__table__.create(eng)
    Player.__table__.create(eng)
    s = Session(eng)
    try:
        assert load_stardom(
            s, as_of=datetime(2026, 1, 1, tzinfo=timezone.utc)) is None
    finally:
        s.close()
