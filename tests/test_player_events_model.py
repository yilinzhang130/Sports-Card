"""Round-trip and constraint tests for the PlayerEvent ORM model."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Player, PlayerEvent, PlayerEventType


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    yield sess
    sess.close()


def _make_player(sess: Session) -> int:
    p = Player(name="Test Player", br_slug="testplayer")
    sess.add(p)
    sess.flush()
    return p.player_id


def test_player_event_roundtrip(session: Session) -> None:
    pid = _make_player(session)
    event_date = datetime(2026, 4, 15, 19, 30, tzinfo=timezone.utc)
    session.add(
        PlayerEvent(
            player_id=pid,
            event_type=PlayerEventType.MVP.value,
            event_date=event_date,
            event_payload={"season": "2025-26", "votes": 95.4},
        )
    )
    session.commit()

    fetched = session.query(PlayerEvent).one()
    assert fetched.player_id == pid
    assert fetched.event_type == "mvp"
    # SQLite strips tzinfo; compare naive components.
    assert fetched.event_date.replace(tzinfo=timezone.utc) == event_date
    assert fetched.event_payload == {"season": "2025-26", "votes": 95.4}
    assert fetched.ingested_at is not None
    assert fetched.event_id is not None


def test_player_event_type_enum_values() -> None:
    # Spot-check that all four categories are represented.
    values = {e.value for e in PlayerEventType}
    assert "injury_out" in values
    assert "playoff_finals_win" in values
    assert "all_nba_1st" in values
    assert "signed" in values
    # Exactly 18 enum members per the spec.
    assert len(values) == 18


def test_player_event_dedupe_unique_constraint(session: Session) -> None:
    pid = _make_player(session)
    event_date = datetime(2026, 1, 10, tzinfo=timezone.utc)
    session.add(
        PlayerEvent(
            player_id=pid,
            event_type=PlayerEventType.INJURY_OUT.value,
            event_date=event_date,
            event_payload={"side": "left ankle"},
        )
    )
    session.commit()

    session.add(
        PlayerEvent(
            player_id=pid,
            event_type=PlayerEventType.INJURY_OUT.value,
            event_date=event_date,
            event_payload={"side": "left ankle", "source": "rerun"},
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Differing event_type for the same player+date should be allowed.
    session.add(
        PlayerEvent(
            player_id=pid,
            event_type=PlayerEventType.INJURY_DTD.value,
            event_date=event_date,
            event_payload={},
        )
    )
    session.commit()
    assert session.query(PlayerEvent).count() == 2
