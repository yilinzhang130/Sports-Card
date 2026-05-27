"""Corwin-Schultz (2012) implicit half-spread from sold history.

Sports cards have no live bid/ask quotes; only completed transactions.
The Corwin-Schultz estimator infers the bid-ask spread from the
high-low range using the insight that high prices tend to be buyer-
initiated (paying the ask) while lows are seller-initiated (hitting
the bid).

Reference: Corwin, S.A. & Schultz, P. (2012) "A Simple Way to Estimate
Bid-Ask Spreads from Daily High and Low Prices", Journal of Finance.
"""

from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
import pandas as pd

MIN_TRADES = 6
DEFAULT_WINDOW_DAYS = 60

TIER_DEFAULT_HALF_SPREAD: dict[str, float] = {
    "A": 0.03,
    "B": 0.05,
    "C": 0.10,
    "D": 0.20,
}

_K = 3.0 - 2.0 * math.sqrt(2.0)  # denominator constant in C-S formula


def estimate_half_spread(
    sold_history: pd.DataFrame,
    *,
    liquidity_tier: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> float:
    """Return the half-spread as a fraction of price (e.g. 0.025 = 2.5%).

    Falls back to ``TIER_DEFAULT_HALF_SPREAD[liquidity_tier]`` when there
    are fewer than ``MIN_TRADES`` trades in the window, or when the
    estimator produces a degenerate value.
    """
    if sold_history.empty or len(sold_history) < MIN_TRADES:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    df = sold_history.copy()
    df["sold_at"] = pd.to_datetime(df["sold_at"], utc=True)
    cutoff = df["sold_at"].max() - timedelta(days=window_days)
    df = df[df["sold_at"] >= cutoff].sort_values("sold_at")
    if len(df) < MIN_TRADES:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    df["day"] = df["sold_at"].dt.floor("D")
    daily = df.groupby("day")["price_usd"].agg(["max", "min"]).reset_index()
    daily = daily.rename(columns={"max": "H", "min": "L"})
    if len(daily) < 2:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    H2 = np.maximum(daily["H"].to_numpy()[:-1], daily["H"].to_numpy()[1:])
    L2 = np.minimum(daily["L"].to_numpy()[:-1], daily["L"].to_numpy()[1:])
    H1 = daily["H"].to_numpy()
    L1 = daily["L"].to_numpy()

    with np.errstate(divide="ignore", invalid="ignore"):
        log_hl_sq = np.log(np.where(L1 > 0, H1 / L1, 1.0)) ** 2
        log_hl2_sq = np.log(np.where(L2 > 0, H2 / L2, 1.0)) ** 2

    beta = log_hl_sq[:-1] + log_hl_sq[1:]            # per-pair β
    gamma = log_hl2_sq                                # per-pair γ

    sqrt_2beta = np.sqrt(np.maximum(2.0 * beta, 0.0))
    sqrt_beta = np.sqrt(np.maximum(beta, 0.0))
    alpha_num = (sqrt_2beta - sqrt_beta) / _K - np.sqrt(np.maximum(gamma / _K, 0.0))
    alpha = np.maximum(alpha_num, 0.0)                # clip negatives

    spread_pct = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    spread_pct = spread_pct[np.isfinite(spread_pct)]
    if spread_pct.size == 0:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    return float(np.median(spread_pct) / 2.0)
