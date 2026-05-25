"""Grading-EV optionality model (raw → PSA 10).

EV = gem_rate × P10_net + (1 − gem_rate) × P9_net − cost_to_grade − raw_price

Task 5 scope: gem-rate estimator + trend adjustment. The full EV
computation (compute_grading_ev / rank_grading_candidates) lands in Task 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import PopSnapshot

# Universe-wide PSA-10 rate among modern-era cards. Used as the Beta prior
# mean for gem-rate shrinkage; revisit with an empirical universe estimate
# once we have enough graded-outcome data.
UNIVERSE_PRIOR: Decimal = Decimal("0.50")

# Beta prior strength α+β. Larger ⇒ stronger pull toward UNIVERSE_PRIOR for
# small-sample cards. At α+β=20, a card with 200 graded copies barely shrinks;
# one with 2 graded copies is pulled almost all the way to the prior.
PRIOR_STRENGTH: Decimal = Decimal("20")


@dataclass(frozen=True)
class GemRateEstimate:
    rate: Decimal
    sample_size: int
    raw_rate: Decimal | None


def estimate_gem_rate(
    session: Session,
    card_id: int,
    as_of: datetime,
    *,
    universe_prior: Decimal = UNIVERSE_PRIOR,
    prior_strength: Decimal = PRIOR_STRENGTH,
) -> GemRateEstimate:
    """Latest-snapshot PSA-10 rate with Beta(α,β) shrinkage to the prior.

    Treats every graded copy in the latest ``pop_snapshots`` row for the
    card as a 'submission outcome'. Known limitation: a single bulk
    submission can spike pop. Switching to event-level grading outcomes is
    a follow-up once PSA exposes them.
    """
    latest_date = session.execute(
        select(PopSnapshot.snapshot_date)
        .where(PopSnapshot.card_id == card_id)
        .where(PopSnapshot.grader == "PSA")
        .where(PopSnapshot.snapshot_date <= as_of)
        .order_by(PopSnapshot.snapshot_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_date is None:
        return GemRateEstimate(rate=universe_prior, sample_size=0, raw_rate=None)

    rows = session.execute(
        select(PopSnapshot.grade, PopSnapshot.pop_count)
        .where(PopSnapshot.card_id == card_id)
        .where(PopSnapshot.grader == "PSA")
        .where(PopSnapshot.snapshot_date == latest_date)
    ).all()
    pops = {Decimal(str(g)): int(n) for g, n in rows}
    psa10 = pops.get(Decimal("10"), 0)
    n = psa10 + pops.get(Decimal("9"), 0) + pops.get(Decimal("8"), 0)
    if n == 0:
        return GemRateEstimate(rate=universe_prior, sample_size=0, raw_rate=None)

    raw_rate = Decimal(psa10) / Decimal(n)
    alpha = universe_prior * prior_strength + Decimal(psa10)
    beta = (Decimal(1) - universe_prior) * prior_strength + Decimal(n - psa10)
    shrunk = (alpha / (alpha + beta)).quantize(Decimal("0.0001"))
    return GemRateEstimate(rate=shrunk, sample_size=n, raw_rate=raw_rate)


def trend_adjustment(
    session: Session,
    card_id: int,
    as_of: datetime,
) -> Decimal:
    """Multiplicative factor on gem_rate when recent 90d pop growth is
    materially gem-light vs trailing 365d.

    Returns 1.0 when no signal. Returns (s90/s365) when the trailing-365d
    PSA-10 share exceeds the trailing-90d share by ≥5 percentage points.
    """

    def _share(start: datetime, end: datetime) -> Decimal | None:
        rows = session.execute(
            select(PopSnapshot.snapshot_date, PopSnapshot.grade, PopSnapshot.pop_count)
            .where(PopSnapshot.card_id == card_id)
            .where(PopSnapshot.grader == "PSA")
            .where(PopSnapshot.snapshot_date >= start)
            .where(PopSnapshot.snapshot_date <= end)
            .order_by(PopSnapshot.snapshot_date)
        ).all()
        if not rows:
            return None
        first_date = rows[0][0]
        last_date = rows[-1][0]
        first = {Decimal(str(g)): int(n) for d, g, n in rows if d == first_date}
        last = {Decimal(str(g)): int(n) for d, g, n in rows if d == last_date}

        def total(d: dict[Decimal, int]) -> int:
            return d.get(Decimal("10"), 0) + d.get(Decimal("9"), 0) + d.get(Decimal("8"), 0)

        delta10 = last.get(Decimal("10"), 0) - first.get(Decimal("10"), 0)
        delta_n = total(last) - total(first)
        if delta_n <= 0:
            return None
        return Decimal(delta10) / Decimal(delta_n)

    s90 = _share(as_of - timedelta(days=90), as_of)
    s365 = _share(as_of - timedelta(days=365), as_of)
    if s90 is None or s365 is None or s365 == 0:
        return Decimal("1")
    if s365 - s90 >= Decimal("0.05"):
        return (s90 / s365).quantize(Decimal("0.0001"))
    return Decimal("1")
