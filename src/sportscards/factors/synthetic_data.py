"""Synthetic repeat-sales fixture generator.

Used for unit-testing the repeat-sales estimator before real eBay data is
flowing. Produces a (tx_clean-shaped) DataFrame together with the ground-truth
latent weekly index that was used to generate prices.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

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
    rows: list[dict] = []
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
