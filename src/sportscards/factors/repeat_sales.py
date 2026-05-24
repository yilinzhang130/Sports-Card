"""Cert-based repeat-sales index (Bailey-Muth-Nourse time-dummy estimator).

The classical OLS time-dummy form of the Case-Shiller family. For each pair of
sales of the same cert (the cert number persists across slab resales on PSA /
BGS / SGC), we regress

    log(P_t1 / P_t0) = sum_k beta_k * D_k + eps

where D_k = +1 if k == t1, -1 if k == t0, 0 otherwise. The level is identified
by anchoring beta_0 = 0; the resulting index is exp(beta_k) rescaled so that
the first period with sufficient pair coverage equals 100.

# TODO weighted variant — Case-Shiller weights pairs by inverse holding-period
# variance to handle heteroskedasticity from longer holding periods. Implement
# once the simple variant is stable in production.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import numpy as np
import pandas as pd

GRADE_TIERS: dict[str, tuple[str, float]] = {
    "PSA10": ("PSA", 10.0),
    "PSA9": ("PSA", 9.0),
    "PSA8": ("PSA", 8.0),
}

Bucket = Literal["weekly", "monthly"]


def _floor_to_bucket(ts: pd.Series, bucket: Bucket) -> pd.Series:
    """Round timestamps down to the start of their bucket (UTC)."""
    s = pd.to_datetime(ts, utc=True)
    if bucket == "weekly":
        # Anchor to Monday — matches synthetic_data and CL50 weekly anchoring.
        return (s - pd.to_timedelta(s.dt.weekday, unit="D")).dt.floor("D")
    if bucket == "monthly":
        return s.dt.to_period("M").dt.to_timestamp(how="start").dt.tz_localize("UTC")
    raise ValueError(f"unsupported bucket: {bucket}")


def _filter_grade(tx: pd.DataFrame, grade_tier: str | None) -> pd.DataFrame:
    if grade_tier is None or grade_tier == "all":
        return tx
    if grade_tier == "lower":
        return tx[(tx["slab_grader"].notna()) & (tx["slab_grade"] < 8.0)]
    if grade_tier not in GRADE_TIERS:
        raise ValueError(f"unknown grade_tier: {grade_tier}")
    grader, grade = GRADE_TIERS[grade_tier]
    return tx[(tx["slab_grader"] == grader) & (tx["slab_grade"] == grade)]


def build_pairs(
    tx: pd.DataFrame,
    bucket: Bucket = "weekly",
    outlier_sigma: float | None = 3.0,
) -> pd.DataFrame:
    """Form consecutive repeat-sale pairs per cert.

    Required columns: cert_number, sold_at, price_usd.
    Drops same-bucket pairs and (optionally) pairs with |log return| > N·σ.
    """
    required = {"cert_number", "sold_at", "price_usd"}
    missing = required - set(tx.columns)
    if missing:
        raise ValueError(f"tx missing columns: {sorted(missing)}")

    df = tx.dropna(subset=["cert_number"]).copy()
    if df.empty:
        return _empty_pairs()

    df["sold_at"] = pd.to_datetime(df["sold_at"], utc=True)
    df["price_usd"] = df["price_usd"].astype(float)
    df["bucket"] = _floor_to_bucket(df["sold_at"], bucket)
    df = df.sort_values(["cert_number", "sold_at"], kind="stable")

    g = df.groupby("cert_number", sort=False)
    cert = df["cert_number"].to_numpy()
    sold_at = df["sold_at"].to_numpy()
    price = df["price_usd"].to_numpy()
    b = df["bucket"].to_numpy()

    # Form (i, i+1) pairs only when both rows share the same cert.
    is_pair = cert[:-1] == cert[1:]
    if not is_pair.any():
        return _empty_pairs()

    idx0 = np.where(is_pair)[0]
    idx1 = idx0 + 1
    pairs = pd.DataFrame(
        {
            "cert_number": cert[idx0],
            "t0": sold_at[idx0],
            "t1": sold_at[idx1],
            "bucket0": b[idx0],
            "bucket1": b[idx1],
            "price0": price[idx0],
            "price1": price[idx1],
        }
    )
    # Drop pairs in the same bucket (Δt = 0 in regression time).
    pairs = pairs[pairs["bucket0"] != pairs["bucket1"]]
    pairs = pairs[(pairs["price0"] > 0) & (pairs["price1"] > 0)]
    if pairs.empty:
        return _empty_pairs()

    pairs["log_return"] = np.log(pairs["price1"].to_numpy() / pairs["price0"].to_numpy())

    if outlier_sigma is not None and len(pairs) > 1:
        sigma = float(pairs["log_return"].std(ddof=0))
        if sigma > 0:
            mu = float(pairs["log_return"].mean())
            keep = np.abs(pairs["log_return"] - mu) <= outlier_sigma * sigma
            pairs = pairs[keep]

    # Silence the unused grouper.
    del g
    return pairs.reset_index(drop=True)


def _empty_pairs() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "cert_number": pd.Series(dtype=object),
            "t0": pd.Series(dtype="datetime64[ns, UTC]"),
            "t1": pd.Series(dtype="datetime64[ns, UTC]"),
            "bucket0": pd.Series(dtype="datetime64[ns, UTC]"),
            "bucket1": pd.Series(dtype="datetime64[ns, UTC]"),
            "price0": pd.Series(dtype=float),
            "price1": pd.Series(dtype=float),
            "log_return": pd.Series(dtype=float),
        }
    )


def estimate_index(
    tx: pd.DataFrame,
    bucket: Bucket = "weekly",
    grade_tier: str | None = "PSA10",
    base_min_pairs: int = 10,
    outlier_sigma: float | None = 3.0,
) -> pd.DataFrame:
    """Run the BMN time-dummy regression and return the level series.

    Returns DataFrame with columns: period_start, index_value, n_pairs, se.
    Periods with zero pair coverage are still present (NaN se, 0 n_pairs).
    """
    filtered = _filter_grade(tx, grade_tier)
    pairs = build_pairs(filtered, bucket=bucket, outlier_sigma=outlier_sigma)
    if pairs.empty:
        return pd.DataFrame(columns=["period_start", "index_value", "n_pairs", "se"])

    # Build the integer time-bucket axis over the full observed range so that
    # downstream joins (truth, CL50) line up even on empty periods.
    periods = pd.DatetimeIndex(
        sorted(set(pairs["bucket0"]).union(set(pairs["bucket1"])))
    ).sort_values()
    # Densify to a regular grid so consumers get a continuous time series.
    if bucket == "weekly":
        full = pd.date_range(periods.min(), periods.max(), freq="7D", tz="UTC")
    else:
        full = pd.date_range(periods.min(), periods.max(), freq="MS", tz="UTC")
    period_to_k = {ts: k for k, ts in enumerate(full)}

    n_pairs = len(pairs)
    n_periods = len(full)
    # Design matrix in sparse form: each row has +1 at k1, -1 at k0.
    # With n_periods up to ~500 and n_pairs up to ~50k this is cheap as dense.
    X = np.zeros((n_pairs, n_periods), dtype=float)
    rows = np.arange(n_pairs)
    k0 = pairs["bucket0"].map(period_to_k).to_numpy()
    k1 = pairs["bucket1"].map(period_to_k).to_numpy()
    X[rows, k0] = -1.0
    X[rows, k1] = +1.0
    # Anchor beta_0 = 0 by dropping the first column.
    X_fit = X[:, 1:]
    y = pairs["log_return"].to_numpy()

    beta_rest, residuals, rank, _ = np.linalg.lstsq(X_fit, y, rcond=None)
    betas = np.concatenate([[0.0], beta_rest])

    # Standard errors via the OLS covariance matrix (σ² (X'X)⁻¹).
    dof = max(n_pairs - rank, 1)
    pred = X_fit @ beta_rest
    sigma2 = float(np.sum((y - pred) ** 2) / dof)
    se_rest = _safe_se(X_fit, sigma2)
    se = np.concatenate([[0.0], se_rest])

    # Per-period pair counts: number of pairs touching each period (a pair
    # contributes one endpoint to each of its two bucket periods).
    counts = np.zeros(n_periods, dtype=int)
    np.add.at(counts, k0, 1)
    np.add.at(counts, k1, 1)

    # Base = first period with >= base_min_pairs pair-endpoints.
    eligible = np.where(counts >= base_min_pairs)[0]
    base_k = int(eligible[0]) if len(eligible) else 0
    levels = 100.0 * np.exp(betas - betas[base_k])

    # Periods with zero pair-endpoints are unidentified — null them out
    # rather than emitting the lstsq zero-fill that would look like a flat line.
    unident = counts == 0
    levels = np.where(unident, np.nan, levels)
    se = np.where(unident, np.nan, se)

    return pd.DataFrame(
        {
            "period_start": full,
            "index_value": levels,
            "n_pairs": counts,
            "se": se,
        }
    )


def _safe_se(X: np.ndarray, sigma2: float) -> np.ndarray:
    """Diagonal of σ² (XᵀX)⁻¹. Returns NaN entries for singular columns."""
    XtX = X.T @ X
    try:
        cov = np.linalg.pinv(XtX) * sigma2
        diag = np.diag(cov)
        diag = np.where(diag < 0, np.nan, diag)
        return np.sqrt(diag)
    except np.linalg.LinAlgError:
        return np.full(X.shape[1], np.nan)


def estimate_for_tiers(
    tx: pd.DataFrame,
    tiers: Iterable[str] = ("PSA10", "PSA9", "PSA8", "lower"),
    bucket: Bucket = "weekly",
) -> dict[str, pd.DataFrame]:
    """Convenience: run the estimator independently for each grade tier."""
    out: dict[str, pd.DataFrame] = {}
    for tier in tiers:
        try:
            out[tier] = estimate_index(tx, bucket=bucket, grade_tier=tier)
        except ValueError:
            continue
    return out
