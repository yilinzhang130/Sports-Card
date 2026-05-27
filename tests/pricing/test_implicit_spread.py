from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from sportscards.pricing.implicit_spread import (
    TIER_DEFAULT_HALF_SPREAD,
    estimate_half_spread,
)


def _sold(prices, t0=None):
    """Helper: build a sold-history DataFrame with daily timestamps."""
    t0 = t0 or datetime(2026, 1, 1, tzinfo=UTC)
    return pd.DataFrame(
        {
            "sold_at": [t0 + timedelta(days=i) for i in range(len(prices))],
            "price_usd": prices,
        }
    )


def test_returns_none_when_too_few_trades_uses_tier_fallback():
    df = _sold([100, 102, 101])  # 3 trades, < 6
    out = estimate_half_spread(df, liquidity_tier="C")
    assert out == TIER_DEFAULT_HALF_SPREAD["C"]


def test_zero_spread_when_prices_flat():
    df = _sold([100] * 20)
    out = estimate_half_spread(df, liquidity_tier="A")
    assert out == pytest.approx(0.0, abs=1e-6)


def test_positive_spread_with_noisy_prices():
    rng = np.random.default_rng(42)
    base = 100.0
    # 3 trades per day × 30 days → real intra-day H/L variation.
    prices = []
    dates = []
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    for day in range(30):
        for _ in range(3):
            prices.append(base + rng.normal(0, 5))
            dates.append(t0 + timedelta(days=day))
    df = pd.DataFrame({"sold_at": dates, "price_usd": prices})
    out = estimate_half_spread(df, liquidity_tier="A")
    assert 0.0 < out < 0.5


def test_negative_alpha_clipped_to_zero():
    # Construct prices so that for several pairs γ > 2β (yields negative α).
    # Estimator must clip to 0 rather than producing imaginary spread.
    prices = [
        100,
        100,
        100,
        100,
        100,
        200,
        200,
        100,
        100,
        100,
        100,
        100,
        200,
        100,
        100,
        100,
        100,
        100,
        100,
        100,
    ]
    df = _sold(prices)
    out = estimate_half_spread(df, liquidity_tier="A")
    assert out >= 0.0
