"""Tests for cert-based repeat-sales index estimator."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sportscards.factors.repeat_sales import build_pairs, estimate_index
from sportscards.factors.synthetic_data import generate_synthetic_tx


def test_recovers_latent_index_from_synthetic_data() -> None:
    """Estimator should recover a known underlying index trajectory with high correlation."""
    tx, truth = generate_synthetic_tx(
        n_certs=3000,
        weeks=300,
        sales_per_cert=(2, 4),
        noise_sigma=0.10,
        seed=42,
    )

    idx = estimate_index(tx, bucket="weekly", grade_tier="PSA10")

    assert {"period_start", "index_value", "n_pairs", "se"}.issubset(idx.columns)
    assert len(idx) > 50  # most periods should be covered

    merged = (
        idx.dropna(subset=["index_value"])
        .merge(truth, on="period_start", how="inner")
    )
    assert len(merged) >= 50
    log_recovered = np.log(merged["index_value"].to_numpy())
    log_truth = np.log(merged["truth_index"].to_numpy())
    # De-mean (level is unidentified — only shape matters)
    corr = np.corrcoef(log_recovered - log_recovered.mean(),
                       log_truth - log_truth.mean())[0, 1]
    assert corr > 0.95, f"expected corr>0.95, got {corr:.3f}"


def test_chain_stability_adding_new_pairs_does_not_change_history() -> None:
    """Adding strictly-future transactions should leave the *shape* of the
    historical index nearly unchanged (BMN re-estimation drifts the level but
    not the period-to-period log returns of identified buckets)."""
    tx_full, _ = generate_synthetic_tx(n_certs=5000, weeks=150, seed=7)

    cutoff = tx_full["sold_at"].quantile(0.6)
    tx_early = tx_full[tx_full["sold_at"] <= cutoff].copy()

    idx_early = estimate_index(tx_early, bucket="weekly", grade_tier="PSA10")
    idx_full = estimate_index(tx_full, bucket="weekly", grade_tier="PSA10")

    common = idx_early.merge(
        idx_full, on="period_start", suffixes=("_early", "_full")
    ).dropna(subset=["index_value_early", "index_value_full"])
    assert len(common) >= 40

    # Smooth out per-week beta estimation noise (which dominates raw weekly
    # returns once n_pairs per week is high) by aggregating to a 4-week
    # window before comparing period-over-period log returns.
    def smooth_log(s: pd.Series, w: int = 4) -> np.ndarray:
        return np.log(s.rolling(w, min_periods=w).mean().dropna().to_numpy())

    r_early = np.diff(smooth_log(common["index_value_early"]))
    r_full = np.diff(smooth_log(common["index_value_full"]))
    corr = float(np.corrcoef(r_early, r_full)[0, 1])
    assert corr > 0.97, f"historical-return correlation {corr:.3f} below 0.97"


def test_same_period_pairs_are_filtered() -> None:
    """Pairs whose buy & sell fall in the same bucket must be dropped."""
    base = pd.Timestamp("2024-01-01", tz="UTC")
    tx = pd.DataFrame(
        [
            # Cert A: both sales within the same week → filtered.
            {"cert_number": "A", "sold_at": base, "price_usd": 100.0,
             "slab_grader": "PSA", "slab_grade": 10.0},
            {"cert_number": "A", "sold_at": base + pd.Timedelta(days=2),
             "price_usd": 110.0, "slab_grader": "PSA", "slab_grade": 10.0},
            # Cert B: sales 30 weeks apart → kept.
            {"cert_number": "B", "sold_at": base, "price_usd": 200.0,
             "slab_grader": "PSA", "slab_grade": 10.0},
            {"cert_number": "B", "sold_at": base + pd.Timedelta(weeks=30),
             "price_usd": 260.0, "slab_grader": "PSA", "slab_grade": 10.0},
        ]
    )
    pairs = build_pairs(tx, bucket="weekly")
    assert (pairs["cert_number"] == "A").sum() == 0
    assert (pairs["cert_number"] == "B").sum() == 1


def test_grade_tier_partitions_regression() -> None:
    """Estimator should only use rows matching the requested grade_tier."""
    tx, _ = generate_synthetic_tx(n_certs=200, weeks=100, seed=3)
    # Mark half the certs as PSA 9 to ensure they're excluded.
    psa9_certs = tx["cert_number"].unique()[:100]
    tx.loc[tx["cert_number"].isin(psa9_certs), "slab_grade"] = 9.0

    idx10 = estimate_index(tx, bucket="weekly", grade_tier="PSA10")
    pairs10 = build_pairs(
        tx[(tx["slab_grader"] == "PSA") & (tx["slab_grade"] == 10.0)],
        bucket="weekly",
    )
    # Each pair contributes one endpoint to each of its two buckets, so
    # the per-period n_pairs column sums to 2 × (number of pairs).
    assert idx10["n_pairs"].sum() == 2 * len(pairs10)


def test_outlier_filter_drops_extreme_log_returns() -> None:
    """A single extreme pair (|log return| > 3σ) should be dropped."""
    rng = np.random.default_rng(0)
    base = pd.Timestamp("2024-01-01", tz="UTC")
    rows = []
    for i in range(50):
        cert = f"C{i}"
        rows.append({"cert_number": cert, "sold_at": base,
                     "price_usd": 100.0, "slab_grader": "PSA", "slab_grade": 10.0})
        rows.append({"cert_number": cert,
                     "sold_at": base + pd.Timedelta(weeks=10 + i),
                     "price_usd": 100.0 * float(np.exp(rng.normal(0, 0.05))),
                     "slab_grader": "PSA", "slab_grade": 10.0})
    # Inject one extreme outlier (10x return).
    rows.append({"cert_number": "OUTLIER", "sold_at": base,
                 "price_usd": 100.0, "slab_grader": "PSA", "slab_grade": 10.0})
    rows.append({"cert_number": "OUTLIER",
                 "sold_at": base + pd.Timedelta(weeks=20),
                 "price_usd": 1000.0, "slab_grader": "PSA", "slab_grade": 10.0})
    tx = pd.DataFrame(rows)

    pairs = build_pairs(tx, bucket="weekly", outlier_sigma=3.0)
    assert (pairs["cert_number"] == "OUTLIER").sum() == 0
