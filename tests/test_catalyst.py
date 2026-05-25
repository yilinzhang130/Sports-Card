"""Tests for catalyst-score time-decay aggregation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Player, PlayerEvent
from sportscards.factors.catalyst import (
    catalyst_score_30d_change,
    compute_catalyst_score,
    compute_catalyst_scores_bulk,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    players = [
        Player(name=f"Player {i}", br_slug=f"p{i}") for i in range(3)
    ]
    for p in players:
        sess.add(p)
    sess.flush()
    sess.commit()
    yield sess
    sess.close()


def _approx(actual: Decimal, expected: float, tol: float = 1e-3) -> bool:
    return abs(float(actual) - expected) <= tol


def _add_event(
    sess: Session,
    player_id: int,
    event_type: str,
    event_date: datetime,
    payload: dict | None = None,
) -> None:
    sess.add(
        PlayerEvent(
            player_id=player_id,
            event_type=event_type,
            event_date=event_date,
            event_payload=payload or {},
        )
    )
    sess.flush()


DAY0 = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)


def _pid(sess: Session, idx: int = 0) -> int:
    return sess.query(Player).order_by(Player.player_id).all()[idx].player_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_returns_zero(session: Session) -> None:
    pid = _pid(session)
    assert compute_catalyst_score(session, pid, DAY0) == Decimal("0")


def test_as_of_safety(session: Session) -> None:
    pid = _pid(session)
    _add_event(session, pid, "mvp", datetime(2024, 4, 1, tzinfo=UTC))
    session.commit()
    score = compute_catalyst_score(
        session, pid, datetime(2024, 3, 31, tzinfo=UTC)
    )
    assert score == Decimal("0")


def test_mvp_decay(session: Session) -> None:
    pid = _pid(session)
    _add_event(session, pid, "mvp", DAY0)
    session.commit()
    s0 = compute_catalyst_score(session, pid, DAY0)
    s90 = compute_catalyst_score(session, pid, DAY0 + timedelta(days=90))
    s180 = compute_catalyst_score(
        session, pid, DAY0 + timedelta(days=180), lookback_days=200
    )
    assert _approx(s0, 0.50)
    assert _approx(s90, 0.25)
    assert _approx(s180, 0.125)


def test_injury_negative_decay(session: Session) -> None:
    pid = _pid(session)
    _add_event(session, pid, "injury_out", DAY0)
    session.commit()
    s0 = compute_catalyst_score(session, pid, DAY0)
    s30 = compute_catalyst_score(session, pid, DAY0 + timedelta(days=30))
    s60 = compute_catalyst_score(session, pid, DAY0 + timedelta(days=60))
    assert _approx(s0, -0.15)
    assert _approx(s30, -0.075)
    assert _approx(s60, -0.0375)


def test_severity_bumps(session: Session) -> None:
    pid_se = _pid(session, 0)
    pid_short = _pid(session, 1)
    _add_event(
        session, pid_se, "injury_out", DAY0, payload={"season_ending": True}
    )
    _add_event(
        session, pid_short, "injury_out", DAY0, payload={"expected_weeks_out": 2}
    )
    session.commit()
    assert _approx(compute_catalyst_score(session, pid_se, DAY0), -0.30)
    assert _approx(compute_catalyst_score(session, pid_short, DAY0), -0.05)


def test_additivity(session: Session) -> None:
    pid = _pid(session)
    _add_event(session, pid, "mvp", DAY0)
    _add_event(session, pid, "injury_dtd", DAY0)
    session.commit()
    s = compute_catalyst_score(session, pid, DAY0)
    assert _approx(s, 0.50 + (-0.03))


def test_lookback_cutoff(session: Session) -> None:
    pid = _pid(session)
    old = DAY0 - timedelta(days=100)
    _add_event(session, pid, "injury_dtd", old)
    session.commit()
    # Default lookback=90: event is 100 days old, excluded.
    assert compute_catalyst_score(session, pid, DAY0) == Decimal("0")
    # lookback=120: event included, decayed.
    s = compute_catalyst_score(session, pid, DAY0, lookback_days=120)
    expected = -0.03 * (0.5 ** (100 / 30))
    assert _approx(s, expected)


def test_bulk_matches_single(session: Session) -> None:
    pids = [_pid(session, i) for i in range(3)]
    _add_event(session, pids[0], "mvp", DAY0)
    _add_event(session, pids[1], "injury_out", DAY0)
    # pids[2] has no events
    session.commit()

    bulk = compute_catalyst_scores_bulk(session, pids, DAY0)
    for pid in pids:
        single = compute_catalyst_score(session, pid, DAY0)
        assert pid in bulk
        assert bulk[pid] == single
    assert bulk[pids[2]] == Decimal("0")


def test_30d_change(session: Session) -> None:
    pid = _pid(session)
    _add_event(session, pid, "mvp", DAY0 - timedelta(days=10))
    session.commit()
    change = catalyst_score_30d_change(session, pid, DAY0)
    s_now = compute_catalyst_score(session, pid, DAY0)
    s_prev = compute_catalyst_score(session, pid, DAY0 - timedelta(days=30))
    assert s_prev == Decimal("0")
    assert _approx(change, float(s_now))
