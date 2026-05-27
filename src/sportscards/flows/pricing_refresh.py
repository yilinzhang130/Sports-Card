"""Weekly pricing refresh: trade_targets + exit_signal.

Runs immediately after ``flows/daily_ebay`` and the factor-panel build.
Idempotent for a given ``as_of`` date.
"""

from __future__ import annotations

from datetime import date

from prefect import flow, get_run_logger

from sportscards.db.session import session_scope
from sportscards.pricing.exit_rules import evaluate_open_positions
from sportscards.pricing.targets import persist_targets_for_panel


@flow(name="pricing-refresh")
def pricing_refresh_flow(as_of: date | None = None) -> dict[str, int]:
    if as_of is None:
        from datetime import UTC, datetime
        as_of = datetime.now(tz=UTC).date()
    log = get_run_logger()

    with session_scope() as s:
        targets_written = persist_targets_for_panel(s, as_of)
    log.info("trade_targets: wrote %d rows for as_of=%s", targets_written, as_of)

    with session_scope() as s:
        fired = evaluate_open_positions(s, as_of)
    signals_written = len(fired)
    log.info("exit_signal: fired %d signals for as_of=%s", signals_written, as_of)

    return {
        "targets_written": targets_written,
        "signals_written": signals_written,
    }


if __name__ == "__main__":
    from datetime import UTC, datetime

    pricing_refresh_flow(as_of=datetime.now(tz=UTC).date())
