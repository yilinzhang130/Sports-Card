"""Daily grading-EV flow — recomputes EV for all eligible cards."""

from __future__ import annotations

from datetime import datetime, timezone

from prefect import flow, get_run_logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sportscards.db.models import GradingEv, TxClean
from sportscards.db.session import session_scope
from sportscards.factors.grading_ev import compute_grading_ev


@flow(name="daily-grading-ev")
def daily_grading_ev_flow(
    grade_tier: str = "value_bulk",
    apply_trend_adjustment: bool = False,
    as_of: datetime | None = None,
) -> int:
    log = get_run_logger()
    if as_of is None:
        as_of = datetime.now(tz=timezone.utc)
    written = 0
    with session_scope() as s:
        cand_ids = s.execute(
            select(TxClean.card_id)
            .where(TxClean.slab_grader.is_(None))
            .where(TxClean.card_id.is_not(None))
            .group_by(TxClean.card_id)
        ).scalars().all()
        for cid in cand_ids:
            try:
                ev = compute_grading_ev(
                    s, cid, as_of,
                    grade_tier=grade_tier,
                    apply_trend_adjustment=apply_trend_adjustment,
                )
            except ValueError:
                continue
            s.execute(
                pg_insert(GradingEv)
                .values(
                    card_id=ev.card_id, as_of_date=as_of, grade_tier=grade_tier,
                    gem_rate=ev.gem_rate, p10_price=ev.p10_price, p9_price=ev.p9_price,
                    cost_to_grade=ev.cost_to_grade, raw_price=ev.raw_price,
                    ev=ev.ev, ev_per_dollar=ev.ev_per_dollar,
                    sample_size=ev.sample_size, p10_pop=ev.p10_pop,
                )
                .on_conflict_do_update(
                    index_elements=["card_id", "as_of_date", "grade_tier"],
                    set_=dict(
                        gem_rate=ev.gem_rate, p10_price=ev.p10_price,
                        p9_price=ev.p9_price, cost_to_grade=ev.cost_to_grade,
                        raw_price=ev.raw_price, ev=ev.ev,
                        ev_per_dollar=ev.ev_per_dollar,
                        sample_size=ev.sample_size, p10_pop=ev.p10_pop,
                    ),
                )
            )
            written += 1
    log.info("daily-grading-ev: wrote %d rows", written)
    return written


if __name__ == "__main__":
    daily_grading_ev_flow()
