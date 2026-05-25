"""Tests for the daily-events Prefect flow."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Player
from sportscards.events import injuries, schedule, transactions
from sportscards.events.awards import AwardRow
from sportscards.events.injuries import InjuryRow
from sportscards.events.schedule import GameRow
from sportscards.events.transactions import TxnRow
from sportscards.flows import daily_events as flow_mod
from sportscards.flows.daily_events import _current_season, daily_events_flow


# ---------------------------------------------------------------------------
# _current_season
# ---------------------------------------------------------------------------


def test_current_season_in_season() -> None:
    assert _current_season(date(2025, 11, 5)) == "2025-26"


def test_current_season_offseason() -> None:
    assert _current_season(date(2025, 4, 5)) == "2024-25"


def test_current_season_october_boundary() -> None:
    assert _current_season(date(2025, 10, 1)) == "2025-26"


# ---------------------------------------------------------------------------
# Flow end-to-end with injected fake clients
# ---------------------------------------------------------------------------


@pytest.fixture()
def memory_session(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    sess.add_all(
        [
            Player(name="LeBron James", team="LAL", br_slug="lebron"),
            Player(name="Stephen Curry", team="GSW", br_slug="curry"),
        ]
    )
    sess.commit()

    @contextmanager
    def fake_scope():
        yield sess

    monkeypatch.setattr(flow_mod, "session_scope", fake_scope)
    yield sess
    sess.close()


@dataclass
class FakeInjuryClient:
    rows: list[InjuryRow] = field(default_factory=list)

    def get_injury_report(self, as_of: date) -> list[InjuryRow]:
        return self.rows


@dataclass
class FakeScheduleClient:
    rows: list[GameRow] = field(default_factory=list)

    def get_schedule(self, season: str) -> list[GameRow]:
        return self.rows


@dataclass
class FakeAwardsClient:
    rows: list[AwardRow] = field(default_factory=list)

    def get_awards(self, season: str) -> list[AwardRow]:
        return self.rows


@dataclass
class FakeTxnClient:
    rows: list[TxnRow] = field(default_factory=list)

    def get_transactions(self, since: date) -> list[TxnRow]:
        return self.rows


def test_flow_runs_end_to_end_on_monday(memory_session: Session, tmp_path) -> None:
    monday = date(2026, 1, 5)  # Monday
    assert monday.weekday() == 0

    inj = FakeInjuryClient([InjuryRow(2544, "LeBron James", "out", monday, None)])
    sch = FakeScheduleClient([])
    awd = FakeAwardsClient([AwardRow("mvp", "2025-26", "Stephen Curry")])
    txn = FakeTxnClient([TxnRow("traded", monday, "LeBron James")])

    result = daily_events_flow(
        injuries_client=inj,
        schedule_client=sch,
        awards_client=awd,
        transactions_client=txn,
        today=monday,
    )
    assert result == {"injuries": 1, "schedule": 0, "awards": 1, "transactions": 1}


def test_flow_skips_weekly_jobs_on_non_monday(memory_session: Session) -> None:
    tuesday = date(2026, 1, 6)
    assert tuesday.weekday() == 1

    inj = FakeInjuryClient([])
    # If these were called they'd return rows, but they should be skipped.
    sch = FakeScheduleClient([])
    awd = FakeAwardsClient([AwardRow("mvp", "2025-26", "Stephen Curry")])
    txn = FakeTxnClient([])

    result = daily_events_flow(
        injuries_client=inj,
        schedule_client=sch,
        awards_client=awd,
        transactions_client=txn,
        today=tuesday,
    )
    assert result == {"injuries": 0, "schedule": 0, "awards": 0, "transactions": 0}


class BrokenClient:
    def get_injury_report(self, as_of: date):
        raise RuntimeError("upstream is down")

    def get_schedule(self, season: str):
        raise RuntimeError("upstream is down")

    def get_awards(self, season: str):
        raise RuntimeError("upstream is down")

    def get_transactions(self, since: date):
        raise RuntimeError("upstream is down")


def test_flow_swallows_per_ingest_exceptions(memory_session: Session) -> None:
    monday = date(2026, 1, 5)
    broken = BrokenClient()
    result = daily_events_flow(
        injuries_client=broken,
        schedule_client=broken,
        awards_client=broken,
        transactions_client=broken,
        today=monday,
    )
    assert result == {"injuries": 0, "schedule": 0, "awards": 0, "transactions": 0}
