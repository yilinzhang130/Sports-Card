"""Grading-EV optionality model (raw → PSA 10).

EV = gem_rate × P10_net + (1 − gem_rate) × P9_net − cost_to_grade − raw_price

Task 5 scope: gem-rate estimator + trend adjustment. The full EV
computation (compute_grading_ev / rank_grading_candidates) lands in Task 6.
"""

from __future__ import annotations

import pandas as pd
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Card, PopSnapshot, TxClean
from sportscards.portfolio.transaction_costs import (
    DEFAULT_SCHEDULE,
    FeeSchedule,
    cost_to_grade,
    net_proceeds,
)

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


# ---------------------------------------------------------------------------
# Task 6: full EV computation + ranking
# ---------------------------------------------------------------------------

# Test seam — tests patch this dict to inject hedonic prices without
# requiring a fitted model on disk.
_HEDONIC_OVERRIDES: dict[int, tuple[Decimal, Decimal]] = {}

# Test seam — tests patch this dict to inject gem rates without
# requiring real pop snapshots (bypasses Beta shrinkage).
_GEM_RATE_OVERRIDES: dict[int, Decimal] = {}


@dataclass(frozen=True)
class GradingEV:
    card_id: int
    gem_rate: Decimal
    p10_price: Decimal
    p9_price: Decimal
    cost_to_grade: Decimal
    raw_price: Decimal | None
    ev: Decimal
    ev_per_dollar: Decimal | None
    p10_pop: int
    sample_size: int
    computed_at: datetime


def _hedonic_prices(session: Session, card_id: int) -> tuple[Decimal, Decimal] | None:
    """Return (P10_gross, P9_gross) fair value or None when unavailable.

    Tests inject via ``_HEDONIC_OVERRIDES``. Production path loads the
    persisted model and runs ``hedonic.predict`` on synthesized rows at
    grade=10 and grade=9.
    """
    if card_id in _HEDONIC_OVERRIDES:
        return _HEDONIC_OVERRIDES[card_id]

    import numpy as np

    from sportscards.factors import hedonic

    try:
        model, encoder, _ = hedonic.load_model()
    except FileNotFoundError:
        return None

    card = session.get(Card, card_id)
    if card is None:
        return None

    latest_date = session.execute(
        select(PopSnapshot.snapshot_date)
        .where(PopSnapshot.card_id == card_id)
        .where(PopSnapshot.grader == "PSA")
        .order_by(PopSnapshot.snapshot_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    pops: dict[Decimal, int] = {}
    if latest_date is not None:
        rows = session.execute(
            select(PopSnapshot.grade, PopSnapshot.pop_count)
            .where(PopSnapshot.card_id == card_id)
            .where(PopSnapshot.grader == "PSA")
            .where(PopSnapshot.snapshot_date == latest_date)
        ).all()
        pops = {Decimal(str(g)): int(n) for g, n in rows}
    pop10 = pops.get(Decimal("10"), 0)
    pop9plus = pop10 + pops.get(Decimal("9"), 0)

    def _row(grade: float) -> dict:
        return {
            "log_pop_psa10": float(np.log1p(pop10)),
            "log_pop_psa9_or_better": float(np.log1p(pop9plus)),
            "parallel_tier": 0,  # neutral; refine in follow-up
            "print_run_log": float(np.log1p(card.print_run or 0)),
            "slab_grade": grade,
            "player_age_at_sale": 25,
            "years_since_draft": 5,
            "draft_pick": 15,
            "is_rookie": bool(card.is_rookie),
            "has_auto": bool(card.has_auto),
            "has_patch": bool(card.has_patch),
            "is_one_of_one": bool(card.is_one_of_one),
            "era_modern": (card.year or 0) >= 2010,
            "set_tier": "mid",
            "team_market": "mid",
            "slab_grader": "PSA",
        }

    df = pd.DataFrame([_row(10.0), _row(9.0)])
    log_prices = hedonic.predict(model, encoder, df)
    p10 = Decimal(str(float(np.expm1(log_prices[0])))).quantize(Decimal("0.01"))
    p9 = Decimal(str(float(np.expm1(log_prices[1])))).quantize(Decimal("0.01"))
    return p10, p9


def _raw_clearing_price(
    session: Session, card_id: int, as_of: datetime, window_days: int = 30
) -> Decimal | None:
    since = as_of - timedelta(days=window_days)
    rows = session.execute(
        select(TxClean.price_usd)
        .where(TxClean.card_id == card_id)
        .where(TxClean.slab_grader.is_(None))
        .where(TxClean.sold_at >= since)
        .where(TxClean.sold_at <= as_of)
    ).scalars().all()
    if not rows:
        return None
    prices = sorted(Decimal(str(p)) for p in rows)
    mid = len(prices) // 2
    return prices[mid] if len(prices) % 2 == 1 else (prices[mid - 1] + prices[mid]) / 2


def compute_grading_ev(
    session: Session,
    card_id: int,
    as_of: datetime,
    *,
    grade_tier: str = "value_bulk",
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
    apply_trend_adjustment: bool = False,
) -> GradingEV:
    gem = estimate_gem_rate(session, card_id, as_of)
    gem_rate = _GEM_RATE_OVERRIDES.get(card_id, gem.rate)
    if apply_trend_adjustment:
        gem_rate = (gem_rate * trend_adjustment(session, card_id, as_of)).quantize(
            Decimal("0.0001")
        )

    hed = _hedonic_prices(session, card_id)
    if hed is None:
        raise ValueError(f"no hedonic price available for card {card_id}")
    p10_gross, p9_gross = hed
    p10_net = Decimal(str(net_proceeds(float(p10_gross), "ebay", schedule))).quantize(
        Decimal("0.01")
    )
    p9_net = Decimal(str(net_proceeds(float(p9_gross), "ebay", schedule))).quantize(
        Decimal("0.01")
    )
    grade_cost = Decimal(str(cost_to_grade(grade_tier, schedule))).quantize(Decimal("0.01"))

    raw = _raw_clearing_price(session, card_id, as_of)
    ev_raw_term = raw or Decimal(0)
    ev = (
        gem_rate * p10_net + (Decimal(1) - gem_rate) * p9_net - grade_cost - ev_raw_term
    ).quantize(Decimal("0.01"))
    evpd = (ev / raw).quantize(Decimal("0.0001")) if raw and raw > 0 else None

    p10_pop = session.execute(
        select(PopSnapshot.pop_count)
        .where(PopSnapshot.card_id == card_id)
        .where(PopSnapshot.grader == "PSA")
        .where(PopSnapshot.grade == Decimal("10"))
        .order_by(PopSnapshot.snapshot_date.desc())
        .limit(1)
    ).scalar_one_or_none() or 0

    computed_at = datetime.now(tz=as_of.tzinfo) if as_of.tzinfo else datetime.utcnow()
    return GradingEV(
        card_id=card_id,
        gem_rate=gem_rate,
        p10_price=p10_gross,
        p9_price=p9_gross,
        cost_to_grade=grade_cost,
        raw_price=raw,
        ev=ev,
        ev_per_dollar=evpd,
        p10_pop=int(p10_pop),
        sample_size=gem.sample_size,
        computed_at=computed_at,
    )


def rank_grading_candidates(
    session: Session,
    as_of: datetime,
    *,
    min_ev_per_dollar: Decimal = Decimal("0.15"),
    grade_tier: str = "value_bulk",
    schedule: FeeSchedule = DEFAULT_SCHEDULE,
    min_sample_size: int = 10,
    apply_trend_adjustment: bool = False,
) -> pd.DataFrame:
    """Sorted DataFrame of positive-EV grading candidates.

    Considers cards with ≥1 raw comp in the last 30 days and ≥``min_sample_size``
    graded copies in the latest pop snapshot.
    """
    since = as_of - timedelta(days=30)
    cand_ids = session.execute(
        select(TxClean.card_id)
        .where(TxClean.slab_grader.is_(None))
        .where(TxClean.sold_at >= since)
        .where(TxClean.card_id.is_not(None))
        .group_by(TxClean.card_id)
    ).scalars().all()

    rows = []
    for cid in cand_ids:
        try:
            ev = compute_grading_ev(
                session, cid, as_of, grade_tier=grade_tier, schedule=schedule,
                apply_trend_adjustment=apply_trend_adjustment,
            )
        except ValueError:
            continue
        if ev.sample_size < min_sample_size:
            continue
        if ev.raw_price is None or ev.ev_per_dollar is None:
            continue
        if ev.ev_per_dollar < min_ev_per_dollar:
            continue
        rows.append(
            dict(
                card_id=ev.card_id,
                gem_rate=float(ev.gem_rate),
                p10_price=float(ev.p10_price),
                p9_price=float(ev.p9_price),
                cost_to_grade=float(ev.cost_to_grade),
                raw_price=float(ev.raw_price),
                ev=float(ev.ev),
                ev_per_dollar=float(ev.ev_per_dollar),
                p10_pop=ev.p10_pop,
                sample_size=ev.sample_size,
            )
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("ev_per_dollar", ascending=False).reset_index(drop=True)
