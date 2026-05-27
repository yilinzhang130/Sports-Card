"""Weekly factor-panel build: momentum + liquidity → ``factor_panel`` hypertable.

Runs Mondays at 05:00 LA, ahead of ``pricing-refresh`` at 06:00.
"""

from __future__ import annotations

from datetime import date

from prefect import flow, get_run_logger

from sportscards.db.session import session_scope
from sportscards.factors.factor_panel import persist_panel


@flow(name="factor-panel-refresh")
def factor_panel_flow(as_of: date | None = None) -> dict[str, int]:
    if as_of is None:
        from datetime import UTC, datetime
        as_of = datetime.now(tz=UTC).date()
    log = get_run_logger()

    with session_scope() as s:
        n = persist_panel(s, as_of)
    log.info("factor_panel: wrote %d rows for as_of=%s", n, as_of)
    return {"rows_written": n}


if __name__ == "__main__":
    factor_panel_flow()
