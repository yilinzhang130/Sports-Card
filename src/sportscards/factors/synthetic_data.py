"""Synthetic data generators for factor-model validation.

Two unrelated generators live here:

* ``generate_synthetic_transactions`` (Phase 2B): writes ``tx_raw`` + ``tx_clean``
  rows to a session with prices driven by a known linear combination of card
  features, so the hedonic regression can be unit-tested against the planted
  ground-truth coefficients (``PLANTED_COEFFS``).
* ``generate_synthetic_tx`` (Phase 2A): builds an in-memory DataFrame of
  cert-tagged repeat sales driven by a known latent weekly index, for testing
  the repeat-sales estimator.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Card, PopSnapshot, TxClean, TxRaw
from sportscards.factors.features import parallel_tier

# Planted ground-truth coefficients. Tests import this dict and assert the
# fitted hedonic model recovers each coefficient within tolerance.
PLANTED_COEFFS: dict[str, float] = {
    "log_pop_psa10": -0.4,  # rarer (lower pop) = pricier
    "parallel_tier": 0.5,  # scarcer parallel = pricier
    "is_rookie": 0.6,
    "has_auto": 0.4,
    "has_patch": 0.3,
    "is_one_of_one": 1.5,
    "grade_10_premium": 0.8,  # PSA 10 vs PSA 9
    "era_modern": -0.2,  # modern (>=2010) vs vintage
}

# Intercept for the generative log-price model — picks a sensible baseline so
# raw USD prices stay in a believable range (~$50–$5000).
_INTERCEPT = 4.0  # exp(4.0) ≈ $55


def _pop_for_card(card: Card, grade: int, rng: random.Random) -> int:
    """Plausible PSA pop count for (card, grade) — scarcer parallels → lower pop."""
    tier = parallel_tier(card)
    # Base pop ranges roughly by tier; PSA 10 typically rarer than PSA 9.
    # Tier 5 (1-of-1) shares tier 4's base — both are extremely scarce.
    base_by_tier = {0: 8000, 1: 1500, 2: 400, 3: 80, 4: 5, 5: 1}
    base = base_by_tier[tier]
    if grade == 10:
        base = max(1, base // 3)
    # +/- 40% jitter
    return max(1, int(base * rng.uniform(0.6, 1.4)))


def generate_synthetic_transactions(
    session: Session,
    n_per_card: int = 20,
    noise_sigma: float = 0.15,
    seed: int = 42,
) -> int:
    """Generate synthetic tx_raw + tx_clean rows for every card in card_master.

    Log-price is generated as a linear combo of features with known coefficients
    (see ``PLANTED_COEFFS``) plus gaussian noise. Also seeds ``pop_snapshots``
    with plausible PSA 9 / PSA 10 counts at two dates per card so the
    asof-join logic in the features module has interesting input.

    Args:
        session: open SQLAlchemy session (caller controls commit/rollback).
        n_per_card: number of synthetic tx rows per card.
        noise_sigma: stddev of gaussian noise added to log-price.
        seed: RNG seed for reproducibility.

    Returns:
        Number of ``tx_clean`` (== ``tx_raw``) rows written.
    """
    rng = random.Random(seed)
    now = datetime.now(tz=UTC)

    cards: list[Card] = list(session.scalars(select(Card)).all())
    if not cards:
        return 0

    rows_written = 0
    # Sequential external_id counter for synthetic source.
    ext_counter = 0

    for card in cards:
        # Build a per-card cert pool. ~30% of cert numbers appear twice so the
        # repeat-sales regression sees actual resales; the remaining ~40% are
        # unique. Deterministic per card+seed.
        card_rng = random.Random(seed + card.card_id)
        n_resold = max(1, int(n_per_card * 0.3))
        resold_certs = [f"SYN-{card.card_id:06d}-R{i:03d}" for i in range(n_resold)]
        fresh_certs = [
            f"SYN-{card.card_id:06d}-U{i:03d}" for i in range(n_per_card - n_resold)
        ]
        cert_pool = resold_certs * 2 + fresh_certs
        card_rng.shuffle(cert_pool)
        cert_pool = cert_pool[:n_per_card]

        tier = parallel_tier(card)
        is_rookie = 1.0 if card.is_rookie else 0.0
        has_auto = 1.0 if card.has_auto else 0.0
        has_patch = 1.0 if card.has_patch else 0.0
        is_one_of_one = 1.0 if card.is_one_of_one else 0.0
        era_modern = 1.0 if card.year >= 2010 else 0.0

        # ---- Seed pop_snapshots: PSA 9 + 10 at ~1y before now and ~6m after now.
        # ("after now" is fine — these are dated relative to the synthetic
        # sales window, which spans the last 3 years, so the later snapshot
        # still post-dates many sales.)
        early_date = now - timedelta(days=365)
        late_date = now + timedelta(days=180)
        for snap_date in (early_date, late_date):
            for grade in (9, 10):
                pop = _pop_for_card(card, grade, rng)
                # Late snapshot trends slightly higher (more cards graded over time).
                if snap_date == late_date:
                    pop = int(pop * rng.uniform(1.1, 1.4))
                session.add(
                    PopSnapshot(
                        snapshot_date=snap_date,
                        card_id=card.card_id,
                        grader="PSA",
                        grade=Decimal(str(grade)),
                        pop_count=pop,
                    )
                )

        # Use the early-date PSA 10 pop as the feature value (the asof-join
        # logic in features module will pick the latest snapshot ≤ sold_at).
        psa10_pop_at_sale = _pop_for_card(card, 10, rng)
        log_pop_psa10 = math.log(max(1, psa10_pop_at_sale))

        for tx_idx in range(n_per_card):
            # Random grade: 70% PSA 10, 30% PSA 9
            grade = 10 if rng.random() < 0.7 else 9
            grade_10_premium = 1.0 if grade == 10 else 0.0

            # Planted log-price
            log_price = (
                _INTERCEPT
                + PLANTED_COEFFS["log_pop_psa10"] * log_pop_psa10
                + PLANTED_COEFFS["parallel_tier"] * tier
                + PLANTED_COEFFS["is_rookie"] * is_rookie
                + PLANTED_COEFFS["has_auto"] * has_auto
                + PLANTED_COEFFS["has_patch"] * has_patch
                + PLANTED_COEFFS["is_one_of_one"] * is_one_of_one
                + PLANTED_COEFFS["grade_10_premium"] * grade_10_premium
                + PLANTED_COEFFS["era_modern"] * era_modern
                + rng.gauss(0.0, noise_sigma)
            )
            price = max(1.0, math.exp(log_price))

            # Uniform sale date over the last 3 years.
            days_back = rng.uniform(0, 3 * 365)
            sold_at = now - timedelta(days=days_back)

            ext_counter += 1
            external_id = f"synthetic-{ext_counter:08d}"

            raw = TxRaw(
                source="synthetic",
                raw_title=(
                    f"{card.year} {card.manufacturer} {card.set_name} "
                    f"#{card.card_number} {card.parallel} PSA {grade}"
                ),
                raw_price=Decimal(f"{price:.2f}"),
                raw_currency="USD",
                sold_at=sold_at,
                external_id=external_id,
                raw_json={"synthetic": True, "card_id": card.card_id},
            )
            session.add(raw)
            session.flush()  # populate raw.raw_id

            clean = TxClean(
                raw_id=raw.raw_id,
                card_id=card.card_id,
                slab_grader="PSA",
                slab_grade=Decimal(str(grade)),
                cert_number=cert_pool[tx_idx],
                price_usd=Decimal(f"{price:.2f}"),
                sold_at=sold_at,
                fee_estimate=Decimal(f"{price * 0.13:.2f}"),
                parser_confidence=Decimal("1.000"),
                parser_method="synthetic",
            )
            session.add(clean)
            rows_written += 1

    session.flush()
    return rows_written


# ---------------------------------------------------------------------------
# Phase 2A: in-memory repeat-sales fixture
# ---------------------------------------------------------------------------

WEEK = pd.Timedelta(weeks=1)


def generate_synthetic_tx(
    n_certs: int = 500,
    weeks: int = 300,
    sales_per_cert: tuple[int, int] = (2, 4),
    noise_sigma: float = 0.10,
    drift: float = 0.002,  # weekly log drift (~10%/yr)
    shock_sigma: float = 0.03,  # weekly index innovation
    base_price: float = 100.0,
    seed: int = 0,
    start: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate cert-tagged repeat sales driven by a known latent index.

    Returns
    -------
    tx : DataFrame
        Columns: cert_number, sold_at (UTC), price_usd, slab_grader, slab_grade.
        Each cert appears 2-N times. All grades default to PSA 10.
    truth : DataFrame
        Columns: period_start, truth_index. The weekly latent series I_t that
        prices were generated from. ``period_start`` matches what the estimator
        emits (Monday-anchored UTC week start).
    """
    rng = np.random.default_rng(seed)
    if start is None:
        start = pd.Timestamp("2018-01-01", tz="UTC").normalize()
    # Anchor to a Monday so synthetic weeks align with estimator buckets.
    start = start - pd.Timedelta(days=start.weekday())

    # Latent index as random walk with drift in log space.
    shocks = rng.normal(drift, shock_sigma, size=weeks)
    log_index = np.cumsum(shocks)
    log_index -= log_index[0]  # base 0
    index = np.exp(log_index)

    week_starts = [start + i * WEEK for i in range(weeks)]
    truth = pd.DataFrame({"period_start": week_starts, "truth_index": index})

    lo, hi = sales_per_cert
    rows: list[dict[str, Any]] = []
    for c in range(n_certs):
        n_sales = int(rng.integers(lo, hi + 1))
        # Pick distinct sale weeks for this cert.
        sale_weeks = np.sort(rng.choice(weeks, size=n_sales, replace=False))
        # Per-cert quality multiplier (level effect that the estimator differences out).
        cert_level = float(np.exp(rng.normal(0, 0.4))) * base_price
        for w in sale_weeks:
            # Jitter sale day within the week.
            day_offset = int(rng.integers(0, 7))
            sold_at = week_starts[w] + pd.Timedelta(days=day_offset)
            price = cert_level * index[w] * float(np.exp(rng.normal(0, noise_sigma)))
            rows.append(
                {
                    "cert_number": f"C{c:05d}",
                    "sold_at": sold_at,
                    "price_usd": float(price),
                    "slab_grader": "PSA",
                    "slab_grade": 10.0,
                }
            )

    tx = pd.DataFrame(rows)
    return tx, truth
