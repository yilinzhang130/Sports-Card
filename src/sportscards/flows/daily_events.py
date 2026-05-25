"""Daily catalyst-events refresh flow.

Pulls injury / schedule / awards / transactions feeds and writes change
events into ``player_events``. Each ingestor is wrapped in try/except so a
single broken feed doesn't take down the whole flow.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from prefect import flow, get_run_logger

from sportscards.db.session import session_scope
from sportscards.events.awards import LiveAwardsClient, ingest_awards
from sportscards.events.injuries import LiveInjuryClient, ingest_injuries
from sportscards.events.schedule import LiveScheduleClient, ingest_schedule
from sportscards.events.transactions import LiveTransactionsClient, ingest_transactions


def _current_season(today: date | None = None) -> str:
    """Return current NBA season string e.g. ``"2025-26"``."""
    today = today or date.today()
    start_year = today.year if today.month >= 10 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


@flow(name="daily-events")
def daily_events_flow(
    *,
    injuries_client: Any = None,
    schedule_client: Any = None,
    awards_client: Any = None,
    transactions_client: Any = None,
    today: date | None = None,
) -> dict[str, int]:
    log = get_run_logger()
    today = today or date.today()
    injuries_client = injuries_client or LiveInjuryClient()
    schedule_client = schedule_client or LiveScheduleClient()
    awards_client = awards_client or LiveAwardsClient()
    transactions_client = transactions_client or LiveTransactionsClient()

    n_i = n_s = n_a = n_t = 0
    season = _current_season(today)
    is_monday = today.weekday() == 0

    with session_scope() as session:
        try:
            n_i = ingest_injuries(session, client=injuries_client, as_of=today)
        except Exception as exc:
            log.exception("injuries ingest failed: %s", exc)

        if is_monday:
            try:
                n_s = ingest_schedule(session, client=schedule_client, season=season)
            except Exception as exc:
                log.exception("schedule ingest failed: %s", exc)

            try:
                n_a = ingest_awards(session, client=awards_client, season=season)
            except Exception as exc:
                log.exception("awards ingest failed: %s", exc)
        else:
            log.info("skipping schedule + awards (not Monday; weekday=%d)", today.weekday())

        try:
            n_t = ingest_transactions(
                session, client=transactions_client, since=today - timedelta(days=7)
            )
        except Exception as exc:
            log.exception("transactions ingest failed: %s", exc)

    return {"injuries": n_i, "schedule": n_s, "awards": n_a, "transactions": n_t}


if __name__ == "__main__":
    daily_events_flow()
