"""NBA schedule ingestor — playoff/finals wins only (for now).

Coarse model: all players whose ``player_master.team == winner_team`` get
a per-game event. Series-clincher and finals flags trigger heavier event
types when the client supplies them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Player, PlayerEvent, PlayerEventType
from sportscards.events._common import existing_event_keys, write_json_cache

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GameRow:
    game_id: str
    game_date: date
    home_team: str
    away_team: str
    is_playoff: bool
    is_finals: bool
    is_all_star: bool
    winner_team: str | None
    is_series_clincher: bool = False


class ScheduleClient(Protocol):
    def get_schedule(self, season: str) -> list[GameRow]: ...


def _parse_game_date(s: str) -> date:
    # LeagueGameLog rows carry GAME_DATE as 'YYYY-MM-DD' (or sometimes 'YYYY-MM-DDT00:00:00').
    return date.fromisoformat(s[:10])


def build_schedule_from_rows(team_rows: list[dict[str, Any]]) -> list[GameRow]:
    """Collapse per-team LeagueGameLog rows into per-game ``GameRow``s.

    Two rows per ``GAME_ID`` (one per team). Pair them, derive winner from
    ``WL == 'W'``, classify by GAME_ID prefix, and post-process playoff
    games to flag series clinchers + the Finals series.
    """
    by_game: dict[str, list[dict[str, Any]]] = {}
    for row in team_rows:
        by_game.setdefault(row["GAME_ID"], []).append(row)

    games: list[GameRow] = []
    for game_id, pair in by_game.items():
        if len(pair) < 2:
            # Sometimes only one team's row makes it through filtering; skip.
            continue
        a, b = pair[0], pair[1]
        winner = a if a.get("WL") == "W" else b if b.get("WL") == "W" else None
        loser = b if winner is a else a
        prefix = game_id[:3]
        is_all_star = prefix == "003"
        is_playoff = prefix == "004"
        matchup = a.get("MATCHUP", "")
        # MATCHUP is "GSW vs. LAL" (home) or "LAL @ GSW" (away); the row whose
        # matchup contains "vs." is the home team.
        if "vs." in matchup:
            home_team, away_team = a["TEAM_ABBREVIATION"], b["TEAM_ABBREVIATION"]
        else:
            home_team, away_team = b["TEAM_ABBREVIATION"], a["TEAM_ABBREVIATION"]
        games.append(
            GameRow(
                game_id=game_id,
                game_date=_parse_game_date(a["GAME_DATE"]),
                home_team=home_team,
                away_team=away_team,
                is_playoff=is_playoff,
                is_finals=False,
                is_all_star=is_all_star,
                winner_team=winner["TEAM_ABBREVIATION"] if winner else None,
                is_series_clincher=False,
            )
        )
        # Silence unused-var lint for `loser` (kept for readability of pairing).
        del loser

    # Playoff post-processing: detect series clinchers + Finals series.
    playoff_games = sorted(
        (g for g in games if g.is_playoff and g.winner_team),
        key=lambda g: (g.game_date, g.game_id),
    )
    wins_by_series_team: dict[frozenset[str], dict[str, int]] = {}
    clinchers: list[GameRow] = []
    promoted: list[GameRow] = []
    for g in playoff_games:
        series = frozenset({g.home_team, g.away_team})
        tally = wins_by_series_team.setdefault(series, {g.home_team: 0, g.away_team: 0})
        if g.winner_team is None:
            continue
        tally[g.winner_team] = tally.get(g.winner_team, 0) + 1
        if tally[g.winner_team] >= 4:
            clincher = GameRow(
                game_id=g.game_id,
                game_date=g.game_date,
                home_team=g.home_team,
                away_team=g.away_team,
                is_playoff=True,
                is_finals=False,
                is_all_star=False,
                winner_team=g.winner_team,
                is_series_clincher=True,
            )
            clinchers.append(clincher)
            promoted.append(g)

    if clinchers:
        # Finals = the series containing the chronologically last clincher.
        last_clincher = max(clinchers, key=lambda g: (g.game_date, g.game_id))
        finals_series = frozenset({last_clincher.home_team, last_clincher.away_team})
        rewritten: list[GameRow] = []
        clinch_ids = {c.game_id: c for c in clinchers}
        for g in games:
            if g.is_playoff:
                series = frozenset({g.home_team, g.away_team})
                is_finals = series == finals_series
                clinch = clinch_ids.get(g.game_id)
                rewritten.append(
                    GameRow(
                        game_id=g.game_id,
                        game_date=g.game_date,
                        home_team=g.home_team,
                        away_team=g.away_team,
                        is_playoff=True,
                        is_finals=is_finals,
                        is_all_star=False,
                        winner_team=g.winner_team,
                        is_series_clincher=clinch is not None,
                    )
                )
            else:
                rewritten.append(g)
        return rewritten

    return games


class LiveScheduleClient:
    """Pulls a full NBA season from nba_api's LeagueGameLog endpoint.

    stats.nba.com is rate-sensitive and frequently 429s on the default
    nba_api UA. We retry with exponential backoff and override the UA to
    look like a real browser.
    """

    def __init__(
        self,
        *,
        cache_dir: Path = Path("data/events_cache/schedule/json"),
    ) -> None:
        self._cache_dir = cache_dir

    def get_schedule(self, season: str) -> list[GameRow]:
        import json

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self._cache_dir / f"{season}.json"

        if cache_path.exists() and cache_path.stat().st_size > 0:
            team_rows = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            team_rows = self._fetch_team_rows(season)
            cache_path.write_text(json.dumps(team_rows), encoding="utf-8")

        return build_schedule_from_rows(team_rows)

    @staticmethod
    def _fetch_team_rows(season: str) -> list[dict[str, Any]]:
        from nba_api.stats.endpoints import leaguegamelog
        from nba_api.stats.library.http import NBAStatsHTTP
        from tenacity import retry, stop_after_attempt, wait_exponential

        from sportscards.events._http import DEFAULT_USER_AGENT

        # stats.nba.com rate-limits nba_api's default UA aggressively. The
        # private attribute name has changed across nba_api versions; try
        # the public-ish one first, then fall back.
        for attr in ("headers", "_headers"):
            hdrs = getattr(NBAStatsHTTP, attr, None)
            if isinstance(hdrs, dict):
                hdrs["User-Agent"] = DEFAULT_USER_AGENT
                break

        @retry(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=2, max=60),
            reraise=True,
        )
        def _one(season_type: str) -> list[dict[str, Any]]:
            ep = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star=season_type,
                player_or_team_abbreviation="T",
            )
            data = ep.get_normalized_dict().get("LeagueGameLog", [])
            return list(data)

        merged: list[dict[str, Any]] = []
        for st in ("Regular Season", "Playoffs", "All Star"):
            try:
                merged.extend(_one(st))
            except Exception as exc:  # noqa: BLE001
                logger.warning("LeagueGameLog %s fetch failed: %s", st, exc)
        return merged


_PLAYOFF_TYPES = [
    PlayerEventType.PLAYOFF_WIN.value,
    PlayerEventType.PLAYOFF_SERIES_WIN.value,
    PlayerEventType.PLAYOFF_FINALS_WIN.value,
]


def ingest_schedule(
    session: Session,
    *,
    client: ScheduleClient,
    season: str,
    cache_dir: Path | None = None,
) -> int:
    """Emit playoff/finals win events. Returns count written."""
    rows = client.get_schedule(season)
    write_json_cache(
        [
            {
                "game_id": r.game_id,
                "game_date": r.game_date.isoformat(),
                "home_team": r.home_team,
                "away_team": r.away_team,
                "is_playoff": r.is_playoff,
                "is_finals": r.is_finals,
                "is_all_star": r.is_all_star,
                "winner_team": r.winner_team,
                "is_series_clincher": r.is_series_clincher,
            }
            for r in rows
        ],
        source="schedule",
        as_of=season,
        cache_dir=cache_dir,
    )

    # First pass: gather playoff games + their rosters, collect candidate
    # event keys so we can dedupe in a single batched SELECT.
    plans: list[tuple[GameRow, str, datetime, list[Player]]] = []
    all_player_ids: set[int] = set()
    all_dates: list[datetime] = []

    for game in rows:
        if not game.is_playoff or not game.winner_team:
            continue

        if game.is_finals and game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_FINALS_WIN.value
        elif game.is_series_clincher:
            event_type = PlayerEventType.PLAYOFF_SERIES_WIN.value
        else:
            event_type = PlayerEventType.PLAYOFF_WIN.value

        roster = (
            session.execute(select(Player).where(Player.team == game.winner_team)).scalars().all()
        )
        if not roster:
            logger.warning(
                "no players found for winner team %s on %s", game.winner_team, game.game_date
            )
            continue

        event_dt = datetime.combine(game.game_date, datetime.min.time())
        plans.append((game, event_type, event_dt, list(roster)))
        all_player_ids.update(p.player_id for p in roster)
        all_dates.append(event_dt)

    if not plans:
        session.commit()
        return 0

    existing = existing_event_keys(
        session,
        player_ids=list(all_player_ids),
        event_types=_PLAYOFF_TYPES,
        date_range=(min(all_dates), max(all_dates)),
    )

    written = 0
    for game, event_type, event_dt, roster in plans:
        for player in roster:
            key = (player.player_id, event_type, event_dt)
            if key in existing:
                continue
            session.add(
                PlayerEvent(
                    player_id=player.player_id,
                    event_type=event_type,
                    event_date=event_dt,
                    event_payload={
                        "game_id": game.game_id,
                        "winner_team": game.winner_team,
                        "is_finals": game.is_finals,
                        "is_series_clincher": game.is_series_clincher,
                    },
                )
            )
            existing.add(key)
            written += 1

    session.commit()
    return written
