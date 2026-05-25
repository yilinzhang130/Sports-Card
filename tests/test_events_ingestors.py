"""Tests for the player_events ingestors (Task 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Player, PlayerEvent, PlayerEventType
from sportscards.events import awards, injuries, schedule, transactions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    sess.add_all(
        [
            Player(name="LeBron James", team="LAL", br_slug="lebron"),
            Player(name="Stephen Curry", team="GSW", br_slug="curry"),
            Player(name="Klay Thompson", team="GSW", br_slug="klay"),
        ]
    )
    sess.commit()
    yield sess
    sess.close()


def _event_count(sess: Session) -> int:
    return sess.execute(select(func.count()).select_from(PlayerEvent)).scalar_one()


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------


@dataclass
class FakeInjuryClient:
    by_date: dict[date, list[injuries.InjuryRow]]

    def get_injury_report(self, as_of: date) -> list[injuries.InjuryRow]:
        return self.by_date.get(as_of, [])


def test_injuries_writes_events(session: Session, tmp_path: Path) -> None:
    day = date(2026, 1, 5)
    client = FakeInjuryClient(
        {
            day: [
                injuries.InjuryRow(2544, "LeBron James", "out", day, "ankle"),
                injuries.InjuryRow(201939, "Stephen Curry", "questionable", day, "knee"),
            ]
        }
    )
    n = injuries.ingest_injuries(session, client=client, as_of=day, cache_dir=tmp_path)
    assert n == 2
    types = {e.event_type for e in session.execute(select(PlayerEvent)).scalars()}
    assert types == {PlayerEventType.INJURY_OUT.value, PlayerEventType.INJURY_DTD.value}


def test_injuries_idempotent(session: Session, tmp_path: Path) -> None:
    day = date(2026, 1, 5)
    client = FakeInjuryClient(
        {day: [injuries.InjuryRow(2544, "LeBron James", "out", day, None)]}
    )
    injuries.ingest_injuries(session, client=client, as_of=day, cache_dir=tmp_path)
    before = _event_count(session)
    injuries.ingest_injuries(session, client=client, as_of=day, cache_dir=tmp_path)
    after = _event_count(session)
    assert before == after == 1


def test_injuries_status_change_flow(session: Session, tmp_path: Path) -> None:
    d1, d2, d3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
    client = FakeInjuryClient(
        {
            d1: [injuries.InjuryRow(2544, "LeBron James", "out", d1, None)],
            d2: [injuries.InjuryRow(2544, "LeBron James", "out", d2, None)],
            d3: [injuries.InjuryRow(2544, "LeBron James", "available", d3, None)],
        }
    )
    injuries.ingest_injuries(session, client=client, as_of=d1, cache_dir=tmp_path)
    assert _event_count(session) == 1
    injuries.ingest_injuries(session, client=client, as_of=d2, cache_dir=tmp_path)
    assert _event_count(session) == 1  # status unchanged
    injuries.ingest_injuries(session, client=client, as_of=d3, cache_dir=tmp_path)
    assert _event_count(session) == 2
    last = session.execute(
        select(PlayerEvent).order_by(PlayerEvent.event_date.desc()).limit(1)
    ).scalar_one()
    assert last.event_type == PlayerEventType.INJURY_RETURN.value


def test_injuries_writes_cache_file(session: Session, tmp_path: Path) -> None:
    day = date(2026, 1, 5)
    client = FakeInjuryClient(
        {day: [injuries.InjuryRow(2544, "LeBron James", "out", day, None)]}
    )
    injuries.ingest_injuries(session, client=client, as_of=day, cache_dir=tmp_path)
    assert (tmp_path / f"{day.isoformat()}.json").exists()


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


@dataclass
class FakeScheduleClient:
    rows: list[schedule.GameRow]

    def get_schedule(self, season: str) -> list[schedule.GameRow]:
        return self.rows


def test_schedule_writes_playoff_wins(session: Session, tmp_path: Path) -> None:
    rows = [
        schedule.GameRow(
            game_id="0042400301",
            game_date=date(2026, 5, 1),
            home_team="GSW",
            away_team="LAL",
            is_playoff=True,
            is_finals=False,
            is_all_star=False,
            winner_team="GSW",
            is_series_clincher=False,
        ),
        # Regular-season game — ignored.
        schedule.GameRow(
            game_id="0022400999",
            game_date=date(2026, 3, 1),
            home_team="GSW",
            away_team="LAL",
            is_playoff=False,
            is_finals=False,
            is_all_star=False,
            winner_team="GSW",
        ),
    ]
    n = schedule.ingest_schedule(
        session, client=FakeScheduleClient(rows), season="2025-26", cache_dir=tmp_path
    )
    assert n == 2  # Curry + Klay
    events = session.execute(select(PlayerEvent)).scalars().all()
    assert all(e.event_type == PlayerEventType.PLAYOFF_WIN.value for e in events)


def test_schedule_finals_clincher(session: Session, tmp_path: Path) -> None:
    rows = [
        schedule.GameRow(
            game_id="g7",
            game_date=date(2026, 6, 15),
            home_team="GSW",
            away_team="LAL",
            is_playoff=True,
            is_finals=True,
            is_all_star=False,
            winner_team="GSW",
            is_series_clincher=True,
        )
    ]
    schedule.ingest_schedule(
        session, client=FakeScheduleClient(rows), season="2025-26", cache_dir=tmp_path
    )
    events = session.execute(select(PlayerEvent)).scalars().all()
    assert {e.event_type for e in events} == {PlayerEventType.PLAYOFF_FINALS_WIN.value}


def test_schedule_idempotent(session: Session, tmp_path: Path) -> None:
    rows = [
        schedule.GameRow(
            game_id="g1",
            game_date=date(2026, 5, 1),
            home_team="GSW",
            away_team="LAL",
            is_playoff=True,
            is_finals=False,
            is_all_star=False,
            winner_team="GSW",
        )
    ]
    schedule.ingest_schedule(
        session, client=FakeScheduleClient(rows), season="2025-26", cache_dir=tmp_path
    )
    before = _event_count(session)
    schedule.ingest_schedule(
        session, client=FakeScheduleClient(rows), season="2025-26", cache_dir=tmp_path
    )
    assert _event_count(session) == before


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------


@dataclass
class FakeAwardsClient:
    rows: list[awards.AwardRow]

    def get_awards(self, season: str) -> list[awards.AwardRow]:
        return self.rows


def test_awards_writes_events(session: Session, tmp_path: Path) -> None:
    rows = [
        awards.AwardRow("mvp", "2024-25", "Stephen Curry"),
        awards.AwardRow("all_nba_1st", "2024-25", "LeBron James"),
    ]
    n = awards.ingest_awards(
        session, client=FakeAwardsClient(rows), season="2024-25", cache_dir=tmp_path
    )
    assert n == 2
    events = session.execute(
        select(PlayerEvent).order_by(PlayerEvent.event_type)
    ).scalars().all()
    assert events[0].event_date == datetime(2025, 6, 30)


def test_awards_idempotent(session: Session, tmp_path: Path) -> None:
    rows = [awards.AwardRow("mvp", "2024-25", "Stephen Curry")]
    awards.ingest_awards(
        session, client=FakeAwardsClient(rows), season="2024-25", cache_dir=tmp_path
    )
    before = _event_count(session)
    awards.ingest_awards(
        session, client=FakeAwardsClient(rows), season="2024-25", cache_dir=tmp_path
    )
    assert _event_count(session) == before == 1


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


@dataclass
class FakeTxnClient:
    rows: list[transactions.TxnRow]

    def get_transactions(self, since: date) -> list[transactions.TxnRow]:
        return self.rows


def test_transactions_writes_events(session: Session, tmp_path: Path) -> None:
    rows = [
        transactions.TxnRow("traded", date(2026, 2, 1), "LeBron James"),
        transactions.TxnRow("signed", date(2026, 2, 3), "Stephen Curry"),
    ]
    n = transactions.ingest_transactions(
        session, client=FakeTxnClient(rows), since=date(2026, 1, 1), cache_dir=tmp_path
    )
    assert n == 2


def test_transactions_idempotent(session: Session, tmp_path: Path) -> None:
    rows = [transactions.TxnRow("traded", date(2026, 2, 1), "LeBron James")]
    transactions.ingest_transactions(
        session, client=FakeTxnClient(rows), since=date(2026, 1, 1), cache_dir=tmp_path
    )
    before = _event_count(session)
    transactions.ingest_transactions(
        session, client=FakeTxnClient(rows), since=date(2026, 1, 1), cache_dir=tmp_path
    )
    assert _event_count(session) == before == 1
