"""Unit tests for the Live*Client parsers + helpers.

These tests do not hit the network; they exercise the pure-parsing
functions against committed HTML fixtures and the schedule-classifier
against a synthetic LeagueGameLog payload.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from sportscards.db.models import Base, Player, PlayerEvent, PlayerEventType
from sportscards.events import awards, injuries, schedule, transactions
from sportscards.events._common import resolve_player_by_name

FIXTURES = Path(__file__).parent / "fixtures" / "events"


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sess = Session(engine)
    sess.add_all(
        [
            Player(name="LeBron James", team="LAL"),
            Player(name="Anthony Davis", team="LAL"),
            Player(name="Stephen Curry", team="GSW"),
            Player(name="Klay Thompson", team="GSW"),
            Player(name="Nikola Jokic", team="DEN"),
            Player(name="Luka Doncic", team="DAL"),
            Player(name="Victor Wembanyama", team="SAS"),
            Player(name="Rudy Gobert", team="MIN"),
            Player(name="Shai Gilgeous-Alexander", team="OKC"),
            Player(name="Jayson Tatum", team="BOS"),
            Player(name="Giannis Antetokounmpo", team="MIL"),
            Player(name="Jalen Brunson", team="NYK"),
            Player(name="Anthony Edwards", team="MIN"),
            Player(name="Kevin Durant", team="PHX"),
            Player(name="Kawhi Leonard", team="LAC"),
            Player(name="Tyrese Haliburton", team="IND"),
            Player(name="Devin Booker", team="PHX"),
            Player(name="Jaylen Brown", team="BOS"),
            Player(name="Domantas Sabonis", team="SAC"),
        ]
    )
    sess.commit()
    yield sess
    sess.close()


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------


def test_parse_espn_injuries_extracts_rows() -> None:
    html = (FIXTURES / "espn_injuries.html").read_text()
    rows = injuries.parse_espn_injuries(html, as_of=date(2026, 1, 20))
    names = [r.name for r in rows]
    assert "LeBron James" in names
    assert "Stephen Curry" in names
    assert "Klay Thompson" in names
    # Note merges est-return + comment.
    lebron = next(r for r in rows if r.name == "LeBron James")
    assert lebron.status == "Out"
    assert lebron.status_date == date(2026, 1, 20)
    assert "two weeks" in (lebron.note or "")


def test_live_injury_ingest_against_fixture_writes_only_changes(
    session: Session, tmp_path: Path
) -> None:
    html = (FIXTURES / "espn_injuries.html").read_text()
    cache = tmp_path / "html"
    cache.mkdir()
    (cache / "2026-01-20.html").write_text(html)

    # Stub: fetch_html raises, forcing the fallback to read cached HTML.
    import sportscards.events._http as http_mod

    def _boom(_url: str, **_kwargs: object) -> str:
        raise RuntimeError("network disabled in test")

    client = injuries.LiveInjuryClient(cache_dir=cache)
    original = http_mod.fetch_html
    http_mod.fetch_html = _boom  # type: ignore[assignment]
    try:
        n = injuries.ingest_injuries(
            session,
            client=client,
            as_of=date(2026, 1, 20),
            cache_dir=tmp_path / "json",
        )
    finally:
        http_mod.fetch_html = original  # type: ignore[assignment]

    # 4 of 5 fixture rows resolve to known players; Fictional Newperson skipped.
    assert n == 4
    # Re-run with same data should be a no-op (status unchanged).
    http_mod.fetch_html = _boom  # type: ignore[assignment]
    try:
        n2 = injuries.ingest_injuries(
            session,
            client=client,
            as_of=date(2026, 1, 20),
            cache_dir=tmp_path / "json",
        )
    finally:
        http_mod.fetch_html = original  # type: ignore[assignment]
    assert n2 == 0


# ---------------------------------------------------------------------------
# Awards
# ---------------------------------------------------------------------------


def test_parse_br_awards_extracts_mvp_and_all_nba() -> None:
    html = (FIXTURES / "br_awards_2023-24.html").read_text()
    rows = awards.parse_br_awards(html, season="2023-24")
    by_type: dict[str, list[str]] = {}
    for r in rows:
        by_type.setdefault(r.award_type, []).append(r.player_name)
    assert by_type[PlayerEventType.MVP.value] == ["Nikola Jokić"]
    assert by_type[PlayerEventType.ROY.value] == ["Victor Wembanyama"]
    assert by_type[PlayerEventType.DPOY.value] == ["Rudy Gobert"]
    assert len(by_type[PlayerEventType.ALL_NBA_1ST.value]) == 5
    assert len(by_type[PlayerEventType.ALL_NBA_2ND.value]) == 5
    assert len(by_type[PlayerEventType.ALL_NBA_3RD.value]) == 5


def test_live_awards_ingest_writes_events(session: Session, tmp_path: Path) -> None:
    html = (FIXTURES / "br_awards_2023-24.html").read_text()
    cache = tmp_path / "html"
    cache.mkdir()
    (cache / "2023-24.html").write_text(html)
    client = awards.LiveAwardsClient(cache_dir=cache, throttle_seconds=0.0)
    n = awards.ingest_awards(session, client=client, season="2023-24", cache_dir=tmp_path / "json")
    # 1 MVP + 1 ROY + 1 DPOY + 15 All-NBA = 18; minus any unresolved names.
    # Fixture is curated so all map to seeded players (diacritics resolved via fuzzy).
    assert n == 18


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def _team_row(*, game_id: str, date_str: str, team: str, opp: str, home: bool, win: bool) -> dict:
    return {
        "GAME_ID": game_id,
        "GAME_DATE": date_str,
        "TEAM_ABBREVIATION": team,
        "MATCHUP": f"{team} vs. {opp}" if home else f"{team} @ {opp}",
        "WL": "W" if win else "L",
    }


def test_build_schedule_classifies_game_id_prefix() -> None:
    rows = [
        _team_row(
            game_id="0022400123",
            date_str="2024-12-01",
            team="GSW",
            opp="LAL",
            home=True,
            win=True,
        ),
        _team_row(
            game_id="0022400123",
            date_str="2024-12-01",
            team="LAL",
            opp="GSW",
            home=False,
            win=False,
        ),
        _team_row(
            game_id="0032400001",
            date_str="2025-02-16",
            team="GSW",
            opp="LAL",
            home=True,
            win=True,
        ),
        _team_row(
            game_id="0032400001",
            date_str="2025-02-16",
            team="LAL",
            opp="GSW",
            home=False,
            win=False,
        ),
    ]
    games = schedule.build_schedule_from_rows(rows)
    by_id = {g.game_id: g for g in games}
    assert by_id["0022400123"].is_playoff is False
    assert by_id["0022400123"].is_all_star is False
    assert by_id["0032400001"].is_all_star is True
    assert by_id["0032400001"].winner_team == "GSW"


def test_build_schedule_flags_series_clincher_and_finals() -> None:
    # Two series. Series A: GSW vs LAL, GSW sweeps 4-0 (round 1).
    # Series B: GSW vs BOS, BOS wins 4-2 (Finals — chronologically later).
    rows: list[dict] = []
    # Series A clinch in 4 games.
    for i, d in enumerate(["2025-04-20", "2025-04-22", "2025-04-24", "2025-04-26"], start=1):
        gid = f"004240010{i}"
        rows.append(_team_row(game_id=gid, date_str=d, team="GSW", opp="LAL", home=True, win=True))
        rows.append(
            _team_row(game_id=gid, date_str=d, team="LAL", opp="GSW", home=False, win=False)
        )
    # Series B finals — BOS wins game 6.
    finals_dates = [
        "2025-06-05",
        "2025-06-08",
        "2025-06-11",
        "2025-06-14",
        "2025-06-16",
        "2025-06-19",
    ]
    finals_winners = ["GSW", "BOS", "BOS", "GSW", "BOS", "BOS"]
    for i, (d, w) in enumerate(zip(finals_dates, finals_winners, strict=False), start=1):
        gid = f"004240020{i}"
        bos_wins = w == "BOS"
        rows.append(
            _team_row(game_id=gid, date_str=d, team="BOS", opp="GSW", home=True, win=bos_wins)
        )
        rows.append(
            _team_row(game_id=gid, date_str=d, team="GSW", opp="BOS", home=False, win=not bos_wins)
        )

    games = schedule.build_schedule_from_rows(rows)
    by_id = {g.game_id: g for g in games}
    # First series clincher is game 4 of series A.
    assert by_id["0042400104"].is_series_clincher is True
    assert by_id["0042400104"].is_finals is False
    # Finals series → all 6 games flagged is_finals; game 6 is the clincher.
    finals_ids = [f"004240020{i}" for i in range(1, 7)]
    for gid in finals_ids:
        assert by_id[gid].is_finals is True
    assert by_id["0042400206"].is_series_clincher is True


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


def test_parse_espn_transactions_classifies_and_extracts_names() -> None:
    html = (FIXTURES / "espn_transactions.html").read_text()
    rows = transactions.parse_espn_transactions(html, today=date(2026, 1, 20))
    by_type: dict[str, list[str]] = {}
    for r in rows:
        by_type.setdefault(r.txn_type, []).append(r.player_name)
    assert any(n.startswith("LeBron") for n in by_type[PlayerEventType.SIGNED.value])
    assert any(n.startswith("Klay") for n in by_type[PlayerEventType.TRADED.value])
    assert any(n.startswith("Stephen") for n in by_type[PlayerEventType.CALL_UP.value])


def test_live_transactions_ingest_filters_by_since(session: Session, tmp_path: Path) -> None:
    html = (FIXTURES / "espn_transactions.html").read_text()
    cache = tmp_path / "html"
    cache.mkdir()
    (cache / "2026-01-20.html").write_text(html)

    import sportscards.events._http as http_mod

    def _boom(_url: str, **_kwargs: object) -> str:
        raise RuntimeError("network disabled in test")

    original = http_mod.fetch_html
    http_mod.fetch_html = _boom  # type: ignore[assignment]
    try:
        client = transactions.LiveTransactionsClient(cache_dir=cache)
        # Monkeypatch today() inside the client so date-parsing year_hint is stable.
        import sportscards.events.transactions as tx_mod

        class _StubDate(date):
            @classmethod
            def today(cls) -> date:
                return date(2026, 1, 20)

        original_date = tx_mod.date
        tx_mod.date = _StubDate  # type: ignore[misc,assignment]
        try:
            rows = client.get_transactions(date(2026, 1, 11))
        finally:
            tx_mod.date = original_date  # type: ignore[misc,assignment]
    finally:
        http_mod.fetch_html = original  # type: ignore[assignment]

    # Two of the four fixture rows are on/after Jan 11 (Jan 15 LeBron, Jan 12 Klay).
    assert {r.txn_type for r in rows} == {
        PlayerEventType.SIGNED.value,
        PlayerEventType.TRADED.value,
    }


# ---------------------------------------------------------------------------
# Fuzzy name resolution
# ---------------------------------------------------------------------------


def test_resolve_player_by_name_handles_diacritics(session: Session) -> None:
    # player_master row is "Luka Doncic" (no diacritic); incoming "Luka Dončić" must match.
    pid = resolve_player_by_name(session, "Luka Dončić")
    assert pid is not None
    name = session.execute(select(Player.name).where(Player.player_id == pid)).scalar_one()
    assert name == "Luka Doncic"


def test_resolve_player_by_name_returns_none_for_unknown(session: Session) -> None:
    assert resolve_player_by_name(session, "Definitely Not A Player") is None


# Sanity: existing event_count test of the in-memory session.
def test_session_fixture_is_clean(session: Session) -> None:
    assert session.execute(select(func.count()).select_from(PlayerEvent)).scalar_one() == 0
