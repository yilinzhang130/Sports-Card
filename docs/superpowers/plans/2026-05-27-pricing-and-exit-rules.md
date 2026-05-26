# Pricing & Exit Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `src/sportscards/pricing/` package that turns factor-panel
recommendations into actionable `bid_max` / `fair_value` / `sell_target` /
`stop_loss` per card, plus a five-rule exit evaluator over open holdings.

**Architecture:** Index-anchored fair value (Card Ladder "CL Value"
style) blended with the hedonic prediction by a recency-confidence
weight; Corwin-Schultz (2012) implicit half-spread from rolling
high-low of sold transactions; liquidity-tier margin of safety on top.
Output persisted to a new `trade_targets` hypertable. Exit rules
evaluated weekly against `portfolio_holdings` rows; signals queued in
`exit_signal` for trader confirmation.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, pandas, NumPy,
PostgreSQL + TimescaleDB, Prefect, Streamlit (Trader Console), pytest.

**Spec:** [docs/superpowers/specs/2026-05-27-pricing-and-exit-rules-design.md](../specs/2026-05-27-pricing-and-exit-rules-design.md)

---

## File Map

**Create:**
- `sql/migrations/versions/0011_pricing_and_exit_signal.py`
- `src/sportscards/pricing/__init__.py`
- `src/sportscards/pricing/implicit_spread.py`
- `src/sportscards/pricing/fair_value.py`
- `src/sportscards/pricing/targets.py`
- `src/sportscards/pricing/exit_rules.py`
- `src/sportscards/pricing/cert_lookup.py`
- `src/sportscards/flows/pricing_refresh.py`
- `tests/pricing/__init__.py`
- `tests/pricing/test_implicit_spread.py`
- `tests/pricing/test_fair_value.py`
- `tests/pricing/test_targets.py`
- `tests/pricing/test_exit_rules.py`
- `tests/pricing/test_cert_lookup.py`

**Modify:**
- `src/sportscards/db/models.py` — add `TradeTargets`, `ExitSignal`; extend `PortfolioHolding` (if a model exists; otherwise add minimal model)
- `src/sportscards/reports/queries.py` — add `get_trade_targets`, `get_open_exit_signals`
- `src/sportscards/reports/render.py` — pricing panel + exit-signals tab
- `src/sportscards/cli/__main__.py` — remove `cardladder import` subcommand
- `docs/trader_console.md` — remove CSV-import references

**Delete:**
- `src/sportscards/ingest/cardladder.py`

---

## Task 1: DB migration + models

**Files:**
- Create: `sql/migrations/versions/0011_pricing_and_exit_signal.py`
- Modify: `src/sportscards/db/models.py`
- Test: `tests/pricing/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/pricing/__init__.py` (empty) and `tests/pricing/test_models.py`:

```python
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from sportscards.db.models import ExitSignal, PortfolioHolding, TradeTargets
from sportscards.db.session import session_scope


@pytest.mark.usefixtures("clean_db")
def test_trade_targets_roundtrip():
    with session_scope() as s:
        s.add(
            TradeTargets(
                card_id=1,
                as_of_date=date(2026, 5, 27),
                fair_value=Decimal("100.00"),
                bid_max=Decimal("90.00"),
                sell_target=Decimal("115.00"),
                stop_loss=Decimal("80.00"),
                confidence=Decimal("0.850"),
                half_spread_pct=Decimal("0.0250"),
                liquidity_margin_pct=Decimal("0.0500"),
            )
        )
    with session_scope() as s:
        got = s.execute(select(TradeTargets)).scalar_one()
        assert got.fair_value == Decimal("100.00")


@pytest.mark.usefixtures("clean_db")
def test_portfolio_holding_new_columns_default_null():
    with session_scope() as s:
        h = PortfolioHolding(
            card_id=1,
            acquired_at="2026-01-01T00:00:00+00:00",
            acquired_cost_usd=Decimal("100"),
            channel="ebay",
        )
        s.add(h)
        s.flush()
        assert h.entry_factor_decile is None
        assert h.entry_liquidity_tier is None


@pytest.mark.usefixtures("clean_db")
def test_exit_signal_unique_per_rule_per_day():
    with session_scope() as s:
        h = PortfolioHolding(
            card_id=1,
            acquired_at="2026-01-01T00:00:00+00:00",
            acquired_cost_usd=Decimal("100"),
            channel="ebay",
        )
        s.add(h)
        s.flush()
        s.add(
            ExitSignal(
                holding_id=h.holding_id,
                rule_triggered="target_hit",
                recommended_action="sell_50pct",
                as_of_date=date(2026, 5, 27),
            )
        )
        s.flush()
        s.add(
            ExitSignal(
                holding_id=h.holding_id,
                rule_triggered="target_hit",
                recommended_action="sell_50pct",
                as_of_date=date(2026, 5, 27),
            )
        )
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            s.flush()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'TradeTargets'` (or similar).

- [ ] **Step 3: Write the migration**

Create `sql/migrations/versions/0011_pricing_and_exit_signal.py`:

```python
"""pricing: trade_targets, exit_signal, portfolio_holdings extensions"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "portfolio_holdings",
        sa.Column("entry_factor_decile", sa.SmallInteger, nullable=True),
    )
    op.add_column(
        "portfolio_holdings",
        sa.Column("entry_liquidity_tier", sa.CHAR(1), nullable=True),
    )

    op.create_table(
        "trade_targets",
        sa.Column("card_id", sa.Integer, sa.ForeignKey("card_master.card_id"), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("fair_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("bid_max", sa.Numeric(12, 2), nullable=False),
        sa.Column("sell_target", sa.Numeric(12, 2), nullable=False),
        sa.Column("stop_loss", sa.Numeric(12, 2), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("half_spread_pct", sa.Numeric(6, 4), nullable=False),
        sa.Column("liquidity_margin_pct", sa.Numeric(6, 4), nullable=False),
        sa.PrimaryKeyConstraint("card_id", "as_of_date"),
    )
    # Timescale hypertable on as_of_date (no-op if Timescale not loaded; we
    # tolerate plain Postgres in CI).
    op.execute(
        "SELECT create_hypertable('trade_targets', 'as_of_date', if_not_exists => TRUE) "
        "WHERE EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb');"
    )

    op.create_table(
        "exit_signal",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "holding_id",
            sa.Integer,
            sa.ForeignKey("portfolio_holdings.holding_id"),
            nullable=False,
        ),
        sa.Column("rule_triggered", sa.Text, nullable=False),
        sa.Column("recommended_action", sa.Text, nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "holding_id", "rule_triggered", "as_of_date",
            name="uq_exit_signal_holding_rule_day",
        ),
    )
    op.create_index(
        "ix_exit_signal_unresolved",
        "exit_signal",
        ["resolved_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_exit_signal_unresolved", table_name="exit_signal")
    op.drop_table("exit_signal")
    op.drop_table("trade_targets")
    op.drop_column("portfolio_holdings", "entry_liquidity_tier")
    op.drop_column("portfolio_holdings", "entry_factor_decile")
```

- [ ] **Step 4: Add models**

Append to `src/sportscards/db/models.py`:

```python
class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    holding_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        ForeignKey("card_master.card_id"), nullable=False, index=True
    )
    cert_number: Mapped[str | None] = mapped_column(String(32))
    slab_grader: Mapped[str | None] = mapped_column(String(16))
    slab_grade: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acquired_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="held")
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sold_proceeds_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    entry_factor_decile: Mapped[int | None] = mapped_column(Integer)
    entry_liquidity_tier: Mapped[str | None] = mapped_column(String(1))


class TradeTargets(Base):
    __tablename__ = "trade_targets"

    card_id: Mapped[int] = mapped_column(
        ForeignKey("card_master.card_id"), primary_key=True
    )
    as_of_date: Mapped[Any] = mapped_column(Date, primary_key=True)
    fair_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    bid_max: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    sell_target: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stop_loss: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    half_spread_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    liquidity_margin_pct: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)


class ExitSignal(Base):
    __tablename__ = "exit_signal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holding_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_holdings.holding_id"), nullable=False
    )
    rule_triggered: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False)
    as_of_date: Mapped[Any] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "holding_id", "rule_triggered", "as_of_date",
            name="uq_exit_signal_holding_rule_day",
        ),
    )
```

- [ ] **Step 5: Run migration & tests**

Run: `alembic upgrade head && pytest tests/pricing/test_models.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add sql/migrations/versions/0011_pricing_and_exit_signal.py \
        src/sportscards/db/models.py \
        tests/pricing/__init__.py \
        tests/pricing/test_models.py
git commit -m "feat(pricing): db schema for trade_targets + exit_signal + holdings ext"
```

---

## Task 2: Corwin-Schultz implicit half-spread

**Files:**
- Create: `src/sportscards/pricing/__init__.py` (empty)
- Create: `src/sportscards/pricing/implicit_spread.py`
- Test: `tests/pricing/test_implicit_spread.py`

The estimator uses the closed-form from Corwin & Schultz (2012). Two-day
high/low pairs are rolled across the window; per-pair α and the implied
spread are computed; the window-median is returned.

- [ ] **Step 1: Write the failing test**

Create `tests/pricing/test_implicit_spread.py`:

```python
from decimal import Decimal
from datetime import datetime, timedelta, UTC

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
    prices = base + rng.normal(0, 5, size=60)
    df = _sold(prices.tolist())
    out = estimate_half_spread(df, liquidity_tier="A")
    # Spread should be positive and bounded; loose bounds keep the test stable.
    assert 0.0 < out < 0.5


def test_negative_alpha_clipped_to_zero():
    # Construct prices so that for several pairs γ > 2β (yields negative α).
    # Estimator must clip to 0 rather than producing imaginary spread.
    prices = [100, 100, 100, 100, 100, 200, 200, 100, 100, 100,
              100, 100, 200, 100, 100, 100, 100, 100, 100, 100]
    df = _sold(prices)
    out = estimate_half_spread(df, liquidity_tier="A")
    assert out >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_implicit_spread.py -v`
Expected: FAIL with `ModuleNotFoundError: sportscards.pricing.implicit_spread`.

- [ ] **Step 3: Implement the estimator**

Create `src/sportscards/pricing/__init__.py` (empty file).

Create `src/sportscards/pricing/implicit_spread.py`:

```python
"""Corwin-Schultz (2012) implicit half-spread from sold history.

Sports cards have no live bid/ask quotes; only completed transactions.
The Corwin-Schultz estimator infers the bid-ask spread from the
high-low range using the insight that high prices tend to be buyer-
initiated (paying the ask) while lows are seller-initiated (hitting
the bid). When discrete trades are sparse, we use the rolling
two-day max/min as a proxy for daily H/L.

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

    # Daily H/L (a single-trade day yields H=L=price).
    df["day"] = df["sold_at"].dt.floor("D")
    daily = df.groupby("day")["price_usd"].agg(["max", "min"]).reset_index()
    daily = daily.rename(columns={"max": "H", "min": "L"})
    if len(daily) < 2:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    # Two-day rolling H/L (paired consecutive days).
    H2 = np.maximum(daily["H"].to_numpy()[:-1], daily["H"].to_numpy()[1:])
    L2 = np.minimum(daily["L"].to_numpy()[:-1], daily["L"].to_numpy()[1:])
    H1 = daily["H"].to_numpy()
    L1 = daily["L"].to_numpy()

    # Safe logs (guard against L == 0).
    with np.errstate(divide="ignore", invalid="ignore"):
        log_hl_sq = np.log(np.where(L1 > 0, H1 / L1, 1.0)) ** 2
        log_hl2_sq = np.log(np.where(L2 > 0, H2 / L2, 1.0)) ** 2

    beta = log_hl_sq[:-1] + log_hl_sq[1:]            # per-pair β
    gamma = log_hl2_sq                                # per-pair γ

    sqrt_2beta = np.sqrt(np.maximum(2.0 * beta, 0.0))
    sqrt_beta = np.sqrt(np.maximum(beta, 0.0))
    alpha_num = (sqrt_2beta - sqrt_beta) / _K - np.sqrt(np.maximum(gamma / _K, 0.0))
    alpha = np.maximum(alpha_num, 0.0)                # clip negatives

    # Spread fraction per pair, then median for robustness.
    spread_pct = 2.0 * (np.exp(alpha) - 1.0) / (1.0 + np.exp(alpha))
    spread_pct = spread_pct[np.isfinite(spread_pct)]
    if spread_pct.size == 0:
        return TIER_DEFAULT_HALF_SPREAD.get(liquidity_tier, 0.10)

    return float(np.median(spread_pct) / 2.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/pricing/test_implicit_spread.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/pricing/__init__.py \
        src/sportscards/pricing/implicit_spread.py \
        tests/pricing/test_implicit_spread.py
git commit -m "feat(pricing): Corwin-Schultz implicit half-spread estimator"
```

---

## Task 3: Index-anchored fair value with recency blend

**Files:**
- Create: `src/sportscards/pricing/fair_value.py`
- Test: `tests/pricing/test_fair_value.py`

`fair_value` is the recency-confidence blend of (last sold reprojected by
the player/parallel index) and (hedonic cross-sectional prediction).
τ = 30 days.

- [ ] **Step 1: Write the failing test**

Create `tests/pricing/test_fair_value.py`:

```python
import math
from datetime import date, datetime, UTC

import pytest

from sportscards.pricing.fair_value import (
    TAU_DAYS,
    FairValue,
    blend,
    recency_confidence,
)


def test_recency_confidence_decays_exponentially():
    assert recency_confidence(0) == pytest.approx(1.0)
    assert recency_confidence(TAU_DAYS) == pytest.approx(math.e ** -1)
    assert recency_confidence(3 * TAU_DAYS) < 0.05


def test_blend_full_confidence_uses_index_projected():
    out = blend(
        index_projected=100.0,
        hedonic_predicted=80.0,
        confidence=1.0,
    )
    assert out == pytest.approx(100.0)


def test_blend_zero_confidence_falls_back_to_hedonic():
    out = blend(
        index_projected=100.0,
        hedonic_predicted=80.0,
        confidence=0.0,
    )
    assert out == pytest.approx(80.0)


def test_blend_midweight():
    out = blend(
        index_projected=100.0,
        hedonic_predicted=80.0,
        confidence=0.5,
    )
    assert out == pytest.approx(90.0)


def test_blend_drops_hedonic_when_missing():
    out = blend(
        index_projected=100.0,
        hedonic_predicted=None,
        confidence=0.3,
    )
    assert out == pytest.approx(100.0)


def test_blend_returns_none_when_both_missing():
    out = blend(
        index_projected=None,
        hedonic_predicted=None,
        confidence=0.5,
    )
    assert out is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_fair_value.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the blend primitives**

Create `src/sportscards/pricing/fair_value.py`:

```python
"""Index-anchored fair value with recency-confidence blend.

Approach (Card Ladder "CL Value" style):

1. Reproject the card's last confirmed sold price forward using the
   relevant player/parallel index:
       index_projected = last_sold * (index_now / index_at_last_sold)
2. Compute a recency-confidence weight that decays exponentially in the
   number of days since the last sale.
3. Blend ``index_projected`` with the hedonic cross-sectional prediction
   using the confidence as the index-side weight.

When ``index_projected`` is unavailable (no prior sold, or stale player
index), confidence drops to 0 and ``fair_value`` falls back entirely to
the hedonic prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, UTC

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import Card, RepeatSalesIndex, TxClean

TAU_DAYS = 30.0
STALE_INDEX_DAYS = 14


@dataclass(frozen=True)
class FairValue:
    card_id: int
    as_of_date: date
    fair_value: float | None
    confidence: float                   # 0..1
    index_projected: float | None
    hedonic_predicted: float | None
    days_since_last_sold: int | None


def recency_confidence(days_since_last_sold: float | None) -> float:
    if days_since_last_sold is None:
        return 0.0
    return float(math.exp(-days_since_last_sold / TAU_DAYS))


def blend(
    *,
    index_projected: float | None,
    hedonic_predicted: float | None,
    confidence: float,
) -> float | None:
    if index_projected is None and hedonic_predicted is None:
        return None
    if index_projected is None:
        return float(hedonic_predicted)
    if hedonic_predicted is None:
        return float(index_projected)
    return float(confidence * index_projected + (1.0 - confidence) * hedonic_predicted)


def _winsorize_last_sold(prices: list[float], pct: float = 0.95) -> float:
    """Take the last sold but cap it at the ``pct`` quantile of recent sales.

    Defends against shill-bid contamination of the most-recent print.
    """
    if not prices:
        raise ValueError("no prices")
    series = pd.Series(prices)
    cap = float(series.quantile(pct))
    return float(min(prices[-1], cap))


def compute_fair_value(
    session: Session,
    card_id: int,
    as_of: date,
    *,
    hedonic_predicted: float | None = None,
) -> FairValue:
    """Compute fair value for a single card.

    ``hedonic_predicted`` is passed in by the caller (the factor pipeline
    already runs hedonic in bulk; we don't want to refit per card).
    """
    as_of_ts = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)

    # 1. Pull the card's last 90 days of sold transactions.
    rows = session.execute(
        select(TxClean.price_usd, TxClean.sold_at)
        .where(TxClean.card_id == card_id)
        .where(TxClean.sold_at <= as_of_ts)
        .where(TxClean.sold_at >= as_of_ts - timedelta(days=90))
        .order_by(TxClean.sold_at)
    ).all()

    if not rows:
        return FairValue(
            card_id=card_id,
            as_of_date=as_of,
            fair_value=hedonic_predicted,
            confidence=0.0,
            index_projected=None,
            hedonic_predicted=hedonic_predicted,
            days_since_last_sold=None,
        )

    prices = [float(p) for p, _ in rows]
    last_sold_at = rows[-1][1]
    days_since = (as_of_ts - last_sold_at).days
    last_sold_winsorized = _winsorize_last_sold(prices)

    # 2. Pull the player/parallel index — for now use the card's
    # repeat_sales index slice (sport+era buckets); future work can split
    # by parallel_tier.
    card = session.get(Card, card_id)
    index_projected: float | None = None
    if card is not None:
        index_now, index_then = _index_pair(session, card, as_of_ts, last_sold_at)
        if index_now is not None and index_then is not None and index_then > 0:
            # Stale-index guard.
            stale = (as_of_ts - _latest_index_ts(session, card)).days > STALE_INDEX_DAYS
            if not stale:
                index_projected = last_sold_winsorized * (index_now / index_then)

    confidence = recency_confidence(days_since) if index_projected is not None else 0.0
    fair = blend(
        index_projected=index_projected,
        hedonic_predicted=hedonic_predicted,
        confidence=confidence,
    )
    return FairValue(
        card_id=card_id,
        as_of_date=as_of,
        fair_value=fair,
        confidence=confidence,
        index_projected=index_projected,
        hedonic_predicted=hedonic_predicted,
        days_since_last_sold=days_since,
    )


def _index_pair(
    session: Session, card: Card, ts_now: datetime, ts_then: datetime
) -> tuple[float | None, float | None]:
    """Return (index_value_now, index_value_at_then) for the card's bucket.

    `RepeatSalesIndex` PK is (period_start, sport, bucket, grade_tier,
    era). We pick the (sport, era) slice for the card and pin
    bucket="weekly", grade_tier="PSA10" as the default index series.
    """
    sport, era = _card_bucket(card)
    rows = session.execute(
        select(RepeatSalesIndex.period_start, RepeatSalesIndex.index_value)
        .where(RepeatSalesIndex.sport == sport)
        .where(RepeatSalesIndex.era == era)
        .where(RepeatSalesIndex.bucket == "weekly")
        .where(RepeatSalesIndex.grade_tier == "PSA10")
        .where(RepeatSalesIndex.period_start <= ts_now)
        .order_by(RepeatSalesIndex.period_start)
    ).all()
    if not rows:
        return None, None
    df = pd.DataFrame(rows, columns=["period_start", "index_value"])
    df["period_start"] = pd.to_datetime(df["period_start"], utc=True)
    now = df[df["period_start"] <= ts_now]["index_value"]
    then = df[df["period_start"] <= ts_then]["index_value"]
    return (
        float(now.iloc[-1]) if len(now) else None,
        float(then.iloc[-1]) if len(then) else None,
    )


def _latest_index_ts(session: Session, card: Card) -> datetime:
    sport, era = _card_bucket(card)
    row = session.execute(
        select(RepeatSalesIndex.period_start)
        .where(RepeatSalesIndex.sport == sport)
        .where(RepeatSalesIndex.era == era)
        .where(RepeatSalesIndex.bucket == "weekly")
        .where(RepeatSalesIndex.grade_tier == "PSA10")
        .order_by(RepeatSalesIndex.period_start.desc())
        .limit(1)
    ).first()
    if row is None:
        return datetime(1970, 1, 1, tzinfo=UTC)
    ts = row[0]
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _card_bucket(card: Card) -> tuple[str, str]:
    # Sport column may be absent in card_master today; default to NBA which
    # is the only sport currently ingested. The era boundary matches
    # factors/index_build.ERA_BOUNDARY.
    sport = getattr(card, "sport", None) or "NBA"
    era = "modern" if card.year >= 2010 else "vintage"
    return sport, era
```

- [ ] **Step 4: Run unit tests**

Run: `pytest tests/pricing/test_fair_value.py -v`
Expected: 6 passed.

- [ ] **Step 5: Write the integration test**

Append to `tests/pricing/test_fair_value.py`:

```python
@pytest.mark.usefixtures("clean_db")
def test_compute_fair_value_uses_index_reprojection(seeded_card_and_index):
    """When index doubles between t_then and t_now, fair value doubles."""
    from sportscards.db.session import session_scope
    from sportscards.pricing.fair_value import compute_fair_value

    card_id, _ = seeded_card_and_index
    with session_scope() as s:
        fv = compute_fair_value(s, card_id, date(2026, 5, 27), hedonic_predicted=None)
    # fixture: last sold $100 when index=100, current index=200 → projected $200
    assert fv.index_projected == pytest.approx(200.0, rel=0.01)
    # Recent sale (1 day ago) → high confidence → fair ≈ index_projected
    assert fv.fair_value == pytest.approx(200.0, rel=0.05)


@pytest.mark.usefixtures("clean_db")
def test_compute_fair_value_stale_index_falls_back_to_hedonic(seeded_stale_index):
    from sportscards.db.session import session_scope
    from sportscards.pricing.fair_value import compute_fair_value

    card_id = seeded_stale_index
    with session_scope() as s:
        fv = compute_fair_value(s, card_id, date(2026, 5, 27), hedonic_predicted=150.0)
    assert fv.index_projected is None
    assert fv.confidence == 0.0
    assert fv.fair_value == pytest.approx(150.0)
```

Add the two fixtures to `tests/pricing/conftest.py` (create the file):

```python
from datetime import datetime, timedelta, UTC
from decimal import Decimal

import pytest

from sportscards.db.models import (
    Card,
    Player,
    RepeatSalesIndex,
    TxClean,
    TxRaw,
)
from sportscards.db.session import session_scope


@pytest.fixture
def seeded_card_and_index():
    """Card with last_sold $100 at t-1d, index 100 then, 200 now."""
    with session_scope() as s:
        p = Player(name="Test Player")
        s.add(p)
        s.flush()
        c = Card(
            year=2018,
            manufacturer="Panini",
            set_name="Prizm",
            player_id=p.player_id,
        )
        s.add(c)
        s.flush()

        now = datetime(2026, 5, 27, tzinfo=UTC)
        # Index: 100 at t-1d, 200 at t (the most recent point).
        s.add_all(
            [
                RepeatSalesIndex(
                    sport="NBA", era="modern", bucket="weekly", grade_tier="PSA10",
                    period_start=now - timedelta(days=1), index_value=Decimal("100"),
                ),
                RepeatSalesIndex(
                    sport="NBA", era="modern", bucket="weekly", grade_tier="PSA10",
                    period_start=now, index_value=Decimal("200"),
                ),
            ]
        )
        # tx_clean needs a tx_raw parent row.
        raw = TxRaw(source="ebay", source_id="test-1", payload_json={}, fetched_at=now)
        s.add(raw)
        s.flush()
        s.add(
            TxClean(
                raw_id=raw.raw_id,
                card_id=c.card_id,
                price_usd=Decimal("100"),
                sold_at=now - timedelta(days=1),
                parser_confidence=Decimal("0.9"),
                parser_method="rule",
            )
        )
        s.flush()
        return c.card_id, p.player_id


@pytest.fixture
def seeded_stale_index():
    """Card with last_sold $100 30d ago, index last updated 30d ago (stale)."""
    with session_scope() as s:
        p = Player(name="Stale Player")
        s.add(p)
        s.flush()
        c = Card(
            year=2018,
            manufacturer="Panini",
            set_name="Prizm",
            player_id=p.player_id,
        )
        s.add(c)
        s.flush()
        now = datetime(2026, 5, 27, tzinfo=UTC)
        s.add(
            RepeatSalesIndex(
                sport="NBA", era="modern", bucket="weekly", grade_tier="PSA10",
                period_start=now - timedelta(days=30), index_value=Decimal("100"),
            )
        )
        raw = TxRaw(source="ebay", source_id="test-2", payload_json={}, fetched_at=now)
        s.add(raw)
        s.flush()
        s.add(
            TxClean(
                raw_id=raw.raw_id,
                card_id=c.card_id,
                price_usd=Decimal("100"),
                sold_at=now - timedelta(days=30),
                parser_confidence=Decimal("0.9"),
                parser_method="rule",
            )
        )
        s.flush()
        return c.card_id
```

Note: the project's existing `tests/conftest.py` is expected to provide
the `clean_db` fixture. If it does not, copy the pattern from
`tests/factors/conftest.py` or equivalent.

- [ ] **Step 6: Run integration tests**

Run: `pytest tests/pricing/test_fair_value.py -v`
Expected: 8 passed.

- [ ] **Step 7: Commit**

```bash
git add src/sportscards/pricing/fair_value.py \
        tests/pricing/conftest.py \
        tests/pricing/test_fair_value.py
git commit -m "feat(pricing): index-anchored fair value with recency blend"
```

---

## Task 4: Trade targets — bid_max / sell_target / stop_loss

**Files:**
- Create: `src/sportscards/pricing/targets.py`
- Test: `tests/pricing/test_targets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/pricing/test_targets.py`:

```python
from datetime import date
from decimal import Decimal

import pytest

from sportscards.pricing.targets import (
    K_SELL_TARGET,
    LIQUIDITY_MARGIN,
    TradeTargetsValues,
    derive_targets,
)


def test_invariant_bid_max_lt_fair_lt_sell_target():
    out = derive_targets(
        fair_value=100.0,
        half_spread=0.025,
        liquidity_tier="A",
        factor_zscore=1.0,
    )
    assert out.bid_max < out.fair_value < out.sell_target


def test_stop_loss_below_bid_max():
    out = derive_targets(
        fair_value=100.0,
        half_spread=0.025,
        liquidity_tier="A",
        factor_zscore=0.0,
    )
    assert out.stop_loss < out.bid_max


def test_tier_D_has_wider_margin_than_tier_A():
    a = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=0.0)
    d = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="D", factor_zscore=0.0)
    assert d.bid_max < a.bid_max
    assert d.stop_loss < a.stop_loss


def test_sell_target_increases_with_factor_zscore():
    low = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=-2.0)
    high = derive_targets(fair_value=100, half_spread=0.0, liquidity_tier="A", factor_zscore=+2.0)
    assert low.sell_target < high.sell_target


def test_exact_arithmetic():
    out = derive_targets(
        fair_value=100.0,
        half_spread=0.05,
        liquidity_tier="B",  # margin = 0.05
        factor_zscore=1.0,   # k = 0.15 → +15%
    )
    assert out.fair_value == pytest.approx(100.0)
    assert out.bid_max == pytest.approx(100 * (1 - 0.05 - 0.05))
    assert out.sell_target == pytest.approx(100 * (1 + 0.05 + 0.15))
    assert out.stop_loss == pytest.approx(100 * (1 - 2 * 0.05))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_targets.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the target derivation**

Create `src/sportscards/pricing/targets.py`:

```python
"""Derive bid_max / fair_value / sell_target / stop_loss per card.

Combines:
- Index-anchored fair value (``pricing.fair_value``)
- Corwin-Schultz implicit half-spread (``pricing.implicit_spread``)
- Liquidity-tier margin of safety
- Cross-sectional factor z-score → sell-target gain multiplier
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sportscards.db.models import FactorPanel, TradeTargets, TxClean
from sportscards.pricing.fair_value import compute_fair_value
from sportscards.pricing.implicit_spread import estimate_half_spread

LIQUIDITY_MARGIN: dict[str, float] = {"A": 0.03, "B": 0.05, "C": 0.10, "D": 0.20}
K_SELL_TARGET = 0.15  # 1-σ factor signal → +15% above fair on sell target


@dataclass(frozen=True)
class TradeTargetsValues:
    card_id: int
    as_of_date: date
    fair_value: float
    bid_max: float
    sell_target: float
    stop_loss: float
    confidence: float
    half_spread: float
    liquidity_margin: float


def derive_targets(
    *,
    fair_value: float,
    half_spread: float,
    liquidity_tier: str,
    factor_zscore: float,
    card_id: int = 0,
    as_of_date: date | None = None,
    confidence: float = 1.0,
) -> TradeTargetsValues:
    margin = LIQUIDITY_MARGIN.get(liquidity_tier, 0.10)
    bid_max = fair_value * (1.0 - half_spread - margin)
    sell_target = fair_value * (1.0 + half_spread + factor_zscore * K_SELL_TARGET)
    stop_loss = fair_value * (1.0 - 2.0 * margin)
    return TradeTargetsValues(
        card_id=card_id,
        as_of_date=as_of_date or date.today(),
        fair_value=fair_value,
        bid_max=bid_max,
        sell_target=sell_target,
        stop_loss=stop_loss,
        confidence=confidence,
        half_spread=half_spread,
        liquidity_margin=margin,
    )


def _factor_zscore_lookup(session: Session, as_of: date) -> dict[int, float]:
    """Cross-sectional z-score of cs_momentum_pct over the as_of panel."""
    rows = session.execute(
        select(FactorPanel.card_id, FactorPanel.cs_momentum_pct).where(
            FactorPanel.as_of_date == as_of
        )
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["card_id", "score"])
    df["score"] = df["score"].astype(float)
    mu, sigma = df["score"].mean(), df["score"].std(ddof=0)
    if not sigma or pd.isna(sigma):
        return {int(r.card_id): 0.0 for r in df.itertuples()}
    df["z"] = (df["score"] - mu) / sigma
    return {int(r.card_id): float(r.z) for r in df.itertuples()}


def _sold_history(session: Session, card_id: int, as_of: date) -> pd.DataFrame:
    rows = session.execute(
        select(TxClean.price_usd, TxClean.sold_at).where(TxClean.card_id == card_id)
    ).all()
    if not rows:
        return pd.DataFrame(columns=["price_usd", "sold_at"])
    df = pd.DataFrame(rows, columns=["price_usd", "sold_at"])
    df["price_usd"] = df["price_usd"].astype(float)
    return df


def _liquidity_tier(session: Session, card_id: int, as_of: date) -> str:
    row = session.execute(
        select(FactorPanel.liquidity_tier).where(
            FactorPanel.card_id == card_id,
            FactorPanel.as_of_date == as_of,
        )
    ).first()
    return str(row[0]) if row else "C"


def _hedonic_predicted_lookup(session: Session, as_of: date) -> dict[int, float]:
    """Hook for hedonic predictions per card. Returns {} if hedonic table
    is unavailable; fair value will then rely solely on index reprojection.
    The factor pipeline already runs hedonic in bulk and writes residuals
    to FactorPanel; this lookup is intentionally a stub today and will be
    filled when hedonic point predictions are persisted in a follow-up."""
    _ = session, as_of
    return {}


def compute_for_card(
    session: Session, card_id: int, as_of: date
) -> TradeTargetsValues | None:
    """End-to-end pricing for one card. Returns None when fair value is
    undefined (no sold history and no hedonic prediction)."""
    hedonic_lookup = _hedonic_predicted_lookup(session, as_of)
    fv = compute_fair_value(
        session, card_id, as_of, hedonic_predicted=hedonic_lookup.get(card_id)
    )
    if fv.fair_value is None:
        return None
    tier = _liquidity_tier(session, card_id, as_of)
    half_spread = estimate_half_spread(
        _sold_history(session, card_id, as_of), liquidity_tier=tier
    )
    z = _factor_zscore_lookup(session, as_of).get(card_id, 0.0)
    return derive_targets(
        fair_value=fv.fair_value,
        half_spread=half_spread,
        liquidity_tier=tier,
        factor_zscore=z,
        card_id=card_id,
        as_of_date=as_of,
        confidence=fv.confidence,
    )


def persist_targets_for_panel(session: Session, as_of: date) -> int:
    """Compute and upsert trade_targets for every card in the as_of factor
    panel. Returns the number of rows written."""
    card_rows = session.execute(
        select(FactorPanel.card_id).where(FactorPanel.as_of_date == as_of)
    ).all()
    card_ids = [int(r[0]) for r in card_rows]
    if not card_ids:
        return 0
    session.execute(delete(TradeTargets).where(TradeTargets.as_of_date == as_of))
    n = 0
    for cid in card_ids:
        out = compute_for_card(session, cid, as_of)
        if out is None:
            continue
        session.add(
            TradeTargets(
                card_id=out.card_id,
                as_of_date=out.as_of_date,
                fair_value=_q(out.fair_value, "0.01"),
                bid_max=_q(out.bid_max, "0.01"),
                sell_target=_q(out.sell_target, "0.01"),
                stop_loss=_q(out.stop_loss, "0.01"),
                confidence=_q(out.confidence, "0.001"),
                half_spread_pct=_q(out.half_spread, "0.0001"),
                liquidity_margin_pct=_q(out.liquidity_margin, "0.0001"),
            )
        )
        n += 1
    session.flush()
    return n


def _q(v: float, q: str) -> Decimal:
    return Decimal(str(round(v, len(q.split(".")[1]))))
```

- [ ] **Step 4: Run unit tests**

Run: `pytest tests/pricing/test_targets.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write the integration test**

Append to `tests/pricing/test_targets.py`:

```python
@pytest.mark.usefixtures("clean_db")
def test_persist_targets_writes_rows_for_panel(seeded_panel_with_pricing_inputs):
    """End-to-end: seeded card with factor panel + sold + index → row in trade_targets."""
    from datetime import date as _date
    from sportscards.db.models import TradeTargets
    from sportscards.db.session import session_scope
    from sportscards.pricing.targets import persist_targets_for_panel

    as_of = _date(2026, 5, 27)
    with session_scope() as s:
        n = persist_targets_for_panel(s, as_of)
        assert n == 1
    with session_scope() as s:
        row = s.execute(select(TradeTargets).where(TradeTargets.as_of_date == as_of)).scalar_one()
        assert row.fair_value > 0
        assert row.bid_max < row.fair_value < row.sell_target
        assert row.stop_loss < row.bid_max
```

Append the fixture to `tests/pricing/conftest.py`:

```python
@pytest.fixture
def seeded_panel_with_pricing_inputs(seeded_card_and_index):
    from sportscards.db.models import FactorPanel

    card_id, _ = seeded_card_and_index
    with session_scope() as s:
        s.add(
            FactorPanel(
                card_id=card_id,
                as_of_date=datetime(2026, 5, 27),
                r30=Decimal("0.05"),
                r90=Decimal("0.10"),
                r365=Decimal("0.30"),
                cs_momentum_pct=Decimal("0.6"),
                is_hyped=False,
                sales_count_90d=10,
                dollar_volume_90d=Decimal("1000"),
                bid_ask_proxy=Decimal("0.05"),
                last_sale_recency_days=1,
                liquidity_tier="A",
            )
        )
    return card_id
```

- [ ] **Step 6: Run integration test**

Run: `pytest tests/pricing/test_targets.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/sportscards/pricing/targets.py tests/pricing/test_targets.py tests/pricing/conftest.py
git commit -m "feat(pricing): trade target derivation and panel-wide persistence"
```

---

## Task 5: Exit rules evaluator

**Files:**
- Create: `src/sportscards/pricing/exit_rules.py`
- Test: `tests/pricing/test_exit_rules.py`

- [ ] **Step 1: Write the failing tests (one per rule)**

Create `tests/pricing/test_exit_rules.py`:

```python
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from sportscards.db.models import (
    ExitSignal,
    FactorPanel,
    PortfolioHolding,
    TradeTargets,
    TxClean,
    TxRaw,
)
from sportscards.db.session import session_scope
from sportscards.pricing.exit_rules import evaluate_open_positions


def _make_holding(s, card_id, **kwargs):
    defaults = dict(
        card_id=card_id,
        acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
        acquired_cost_usd=Decimal("100"),
        channel="ebay",
        status="held",
        entry_factor_decile=10,
        entry_liquidity_tier="A",
    )
    defaults.update(kwargs)
    h = PortfolioHolding(**defaults)
    s.add(h)
    s.flush()
    return h


def _add_last_sold(s, card_id, price, when):
    raw = TxRaw(source="ebay", source_id=f"r-{card_id}-{when.isoformat()}",
                payload_json={}, fetched_at=when)
    s.add(raw)
    s.flush()
    s.add(TxClean(
        raw_id=raw.raw_id, card_id=card_id, price_usd=Decimal(str(price)),
        sold_at=when, parser_confidence=Decimal("0.9"), parser_method="rule",
    ))


def _add_targets(s, card_id, as_of, **prices):
    s.add(TradeTargets(
        card_id=card_id, as_of_date=as_of,
        fair_value=Decimal(str(prices.get("fair", 100))),
        bid_max=Decimal(str(prices.get("bid_max", 90))),
        sell_target=Decimal(str(prices.get("sell_target", 115))),
        stop_loss=Decimal(str(prices.get("stop_loss", 80))),
        confidence=Decimal("0.9"),
        half_spread_pct=Decimal("0.025"),
        liquidity_margin_pct=Decimal("0.05"),
    ))


def _add_panel(s, card_id, as_of, **kwargs):
    defaults = dict(
        card_id=card_id, as_of_date=datetime.combine(as_of, datetime.min.time()),
        r30=Decimal("0"), r90=Decimal("0"), r365=Decimal("0"),
        cs_momentum_pct=Decimal("0.5"), is_hyped=False,
        sales_count_90d=10, dollar_volume_90d=Decimal("1000"),
        bid_ask_proxy=Decimal("0.05"), last_sale_recency_days=5,
        liquidity_tier="A",
    )
    defaults.update(kwargs)
    s.add(FactorPanel(**defaults))


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_rule_target_hit_emits_sell_50pct(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 120, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of, sell_target=115)
        _add_panel(s, card_id, as_of, cs_momentum_pct=Decimal("0.6"))
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("target_hit", "sell_50pct") in rules


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_rule_factor_reversal_when_dropped_out_of_decile(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id, entry_factor_decile=10)
        _add_last_sold(s, card_id, 100, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        # cs_momentum_pct is at percentile 0.05 → bottom decile
        _add_panel(s, card_id, as_of, cs_momentum_pct=Decimal("0.05"))
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("factor_reversal", "sell_100pct") in rules


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_rule_time_stop_after_540_days_no_gain(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(
            s, card_id,
            acquired_at=datetime(2024, 1, 1, tzinfo=UTC),   # >540d before as_of
            acquired_cost_usd=Decimal("100"),
        )
        _add_last_sold(s, card_id, 105, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        _add_panel(s, card_id, as_of)
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("time_stop", "sell_100pct") in rules


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_rule_price_stop_when_below_stop_loss(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 70, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of, stop_loss=80)
        _add_panel(s, card_id, as_of)
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("price_stop", "sell_100pct") in rules


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_rule_liquidity_degrade(seeded_card_and_index):
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id, entry_liquidity_tier="A")
        _add_last_sold(s, card_id, 100, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of)
        _add_panel(s, card_id, as_of, liquidity_tier="C")  # A → C is ≥2 grades
        signals = evaluate_open_positions(s, as_of)
    rules = {(sg.rule_triggered, sg.recommended_action) for sg in signals}
    assert ("liquidity_degrade", "sell_100pct") in rules


@pytest.mark.usefixtures("clean_db", "seeded_card_and_index")
def test_unique_constraint_dedupes_reruns(seeded_card_and_index):
    """Running evaluate twice on the same day must not duplicate signals."""
    card_id, _ = seeded_card_and_index
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        _make_holding(s, card_id)
        _add_last_sold(s, card_id, 120, datetime(2026, 5, 26, tzinfo=UTC))
        _add_targets(s, card_id, as_of, sell_target=115)
        _add_panel(s, card_id, as_of)
        evaluate_open_positions(s, as_of)
        evaluate_open_positions(s, as_of)
        rows = s.execute(
            select(ExitSignal).where(ExitSignal.rule_triggered == "target_hit")
        ).scalars().all()
    assert len(rows) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_exit_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: sportscards.pricing.exit_rules`.

- [ ] **Step 3: Implement the evaluator**

Create `src/sportscards/pricing/exit_rules.py`:

```python
"""Exit-rule evaluator over open ``portfolio_holdings``.

Five rules, evaluated independently per (holding, as_of_date). Multiple
rules can fire for the same holding; each writes its own ``exit_signal``
row. The Trader Console surfaces them for trader confirmation. No
automated order placement here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal

import pandas as pd
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from sportscards.db.models import (
    ExitSignal,
    FactorPanel,
    PortfolioHolding,
    TradeTargets,
    TxClean,
)

ExitRule = Literal[
    "target_hit", "factor_reversal", "time_stop", "price_stop", "liquidity_degrade"
]

TIME_STOP_DAYS = 540
TIME_STOP_PRICE_RATIO = 1.10
LIQUIDITY_TIERS = ["A", "B", "C", "D"]
LIQUIDITY_DEGRADE_STEPS = 2
FACTOR_LONG_DECILE_TOP_PCT = 0.10


@dataclass(frozen=True)
class ExitSignalDTO:
    holding_id: int
    rule_triggered: ExitRule
    recommended_action: Literal["sell_50pct", "sell_100pct"]
    as_of_date: date
    notes: str


def evaluate_open_positions(session: Session, as_of: date) -> list[ExitSignalDTO]:
    """Evaluate all five rules for every open holding. Persists fired
    signals to ``exit_signal`` (ON CONFLICT DO NOTHING via unique
    constraint) and returns the in-memory DTOs that fired this run."""
    holdings = session.execute(
        select(PortfolioHolding).where(PortfolioHolding.status == "held")
    ).scalars().all()
    if not holdings:
        return []

    last_sold = _last_sold_lookup(session, [h.card_id for h in holdings], as_of)
    targets = _targets_lookup(session, as_of)
    panel = _panel_lookup(session, as_of)
    factor_decile_rank = _factor_decile_rank(session, as_of)

    fired: list[ExitSignalDTO] = []
    for h in holdings:
        price = last_sold.get(h.card_id)
        tt = targets.get(h.card_id)
        pn = panel.get(h.card_id)

        # Rule 1: target hit (needs price + targets).
        if price is not None and tt is not None and price >= float(tt.sell_target):
            fired.append(ExitSignalDTO(
                h.holding_id, "target_hit", "sell_50pct", as_of,
                f"last_sold={price:.2f} >= sell_target={tt.sell_target}",
            ))

        # Rule 2: factor reversal (entered in top decile, now outside).
        if h.entry_factor_decile == 10:
            rank_pct = factor_decile_rank.get(h.card_id)
            if rank_pct is not None and rank_pct < (1.0 - FACTOR_LONG_DECILE_TOP_PCT):
                fired.append(ExitSignalDTO(
                    h.holding_id, "factor_reversal", "sell_100pct", as_of,
                    f"factor_rank_pct={rank_pct:.2f} (dropped from top decile)",
                ))

        # Rule 3: time stop.
        held_days = (datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)
                     - h.acquired_at).days
        if held_days > TIME_STOP_DAYS and price is not None:
            if price < TIME_STOP_PRICE_RATIO * float(h.acquired_cost_usd):
                fired.append(ExitSignalDTO(
                    h.holding_id, "time_stop", "sell_100pct", as_of,
                    f"held {held_days}d, price {price:.2f} < {TIME_STOP_PRICE_RATIO}×cost",
                ))

        # Rule 4: price stop.
        if price is not None and tt is not None and price < float(tt.stop_loss):
            fired.append(ExitSignalDTO(
                h.holding_id, "price_stop", "sell_100pct", as_of,
                f"last_sold={price:.2f} < stop_loss={tt.stop_loss}",
            ))

        # Rule 5: liquidity degrade.
        if h.entry_liquidity_tier and pn is not None:
            try:
                entry_ix = LIQUIDITY_TIERS.index(h.entry_liquidity_tier)
                now_ix = LIQUIDITY_TIERS.index(pn.liquidity_tier)
                if now_ix - entry_ix >= LIQUIDITY_DEGRADE_STEPS:
                    fired.append(ExitSignalDTO(
                        h.holding_id, "liquidity_degrade", "sell_100pct", as_of,
                        f"tier {h.entry_liquidity_tier} → {pn.liquidity_tier}",
                    ))
            except ValueError:
                pass

    _persist_signals(session, fired)
    return fired


def _last_sold_lookup(session: Session, card_ids: list[int], as_of: date) -> dict[int, float]:
    as_of_ts = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    rows = session.execute(
        select(TxClean.card_id, TxClean.price_usd, TxClean.sold_at)
        .where(TxClean.card_id.in_(card_ids))
        .where(TxClean.sold_at < as_of_ts)
        .order_by(TxClean.card_id, TxClean.sold_at.desc())
    ).all()
    out: dict[int, float] = {}
    for cid, price, _ in rows:
        if cid not in out:
            out[int(cid)] = float(price)
    return out


def _targets_lookup(session: Session, as_of: date) -> dict[int, TradeTargets]:
    rows = session.execute(
        select(TradeTargets).where(TradeTargets.as_of_date == as_of)
    ).scalars().all()
    return {int(r.card_id): r for r in rows}


def _panel_lookup(session: Session, as_of: date) -> dict[int, FactorPanel]:
    as_of_ts = datetime.combine(as_of, datetime.min.time())
    rows = session.execute(
        select(FactorPanel).where(FactorPanel.as_of_date == as_of_ts)
    ).scalars().all()
    return {int(r.card_id): r for r in rows}


def _factor_decile_rank(session: Session, as_of: date) -> dict[int, float]:
    """Returns the per-card percentile rank (0..1) of cs_momentum_pct."""
    as_of_ts = datetime.combine(as_of, datetime.min.time())
    rows = session.execute(
        select(FactorPanel.card_id, FactorPanel.cs_momentum_pct)
        .where(FactorPanel.as_of_date == as_of_ts)
    ).all()
    if not rows:
        return {}
    df = pd.DataFrame(rows, columns=["card_id", "score"])
    df["score"] = df["score"].astype(float)
    df["rank_pct"] = df["score"].rank(pct=True)
    return {int(r.card_id): float(r.rank_pct) for r in df.itertuples()}


def _persist_signals(session: Session, signals: list[ExitSignalDTO]) -> None:
    if not signals:
        return
    payload = [
        {
            "holding_id": s.holding_id,
            "rule_triggered": s.rule_triggered,
            "recommended_action": s.recommended_action,
            "as_of_date": s.as_of_date,
            "notes": s.notes,
        }
        for s in signals
    ]
    stmt = pg_insert(ExitSignal).values(payload).on_conflict_do_nothing(
        constraint="uq_exit_signal_holding_rule_day"
    )
    session.execute(stmt)
    session.flush()
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/pricing/test_exit_rules.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/pricing/exit_rules.py tests/pricing/test_exit_rules.py
git commit -m "feat(pricing): five-rule exit evaluator over portfolio_holdings"
```

---

## Task 6: Card Ladder cert lookup + delete CSV importer

**Files:**
- Create: `src/sportscards/pricing/cert_lookup.py`
- Modify: `src/sportscards/cli/__main__.py` (remove cardladder subcommand)
- Delete: `src/sportscards/ingest/cardladder.py`
- Test: `tests/pricing/test_cert_lookup.py`

The current `ingest/cardladder.py` already has a `CardLadderApiClient`
skeleton; this task moves and fleshes it out to handle cert-based
lookup, then removes the dead CSV path.

- [ ] **Step 1: Inspect existing skeleton**

Run: `cat src/sportscards/ingest/cardladder.py | tail -60`

Confirm: a class `CardLadderApiClient` exists with a `get_cert` (or
similar) method stub. If not, the new module will introduce it from
scratch.

- [ ] **Step 2: Write the failing test**

Create `tests/pricing/test_cert_lookup.py`:

```python
from unittest.mock import patch

import pytest

from sportscards.pricing.cert_lookup import (
    CardLadderCertLookup,
    CertLookupResult,
    CertNotFoundError,
)


def _fake_response(status_code=200, json_data=None):
    class R:
        def __init__(self):
            self.status_code = status_code
            self._json = json_data or {}
        def json(self):
            return self._json
        def raise_for_status(self):
            if self.status_code >= 400:
                from requests import HTTPError
                raise HTTPError(response=self)
    return R()


def test_get_cert_returns_parsed_result():
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    payload = {
        "cert_number": "12345678",
        "grader": "PSA",
        "grade": 10,
        "last_sold": {"price": 500.0, "date": "2026-05-01"},
        "card_ladder_value": 525.0,
    }
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(200, payload)) as mock_get:
        result = client.get_cert("12345678")
    assert isinstance(result, CertLookupResult)
    assert result.grader == "PSA"
    assert result.grade == 10.0
    assert result.last_sold_price == 500.0
    assert result.card_ladder_value == 525.0
    assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer k"


def test_get_cert_404_raises():
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(404, {})):
        with pytest.raises(CertNotFoundError):
            client.get_cert("nope")


def test_get_cert_rate_limit_raises_retryable():
    from sportscards.pricing.cert_lookup import RateLimitedError
    client = CardLadderCertLookup(api_base="https://example/api", api_key="k")
    with patch("sportscards.pricing.cert_lookup.requests.get",
               return_value=_fake_response(429, {})):
        with pytest.raises(RateLimitedError):
            client.get_cert("x")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/pricing/test_cert_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Implement the client**

Create `src/sportscards/pricing/cert_lookup.py`:

```python
"""Card Ladder cert-based lookup.

Replaces the dead ``ingest/cardladder.py`` CSV import path. Card Ladder
Pro does not expose a market-data CSV export — only a collection-upload
CSV. The one programmatic surface that *is* viable is the cert-based
lookup endpoint, used by the grading-EV sleeve to confirm grade-ratio
premia for a specific slabbed card.

This module is intentionally narrow: a single GET-per-cert client with
404 and 429 error classes. No persistence; the caller decides what to
do with the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


class CertNotFoundError(LookupError):
    """The cert number is not in Card Ladder's database."""


class RateLimitedError(RuntimeError):
    """Card Ladder returned 429; caller should back off and retry."""


@dataclass(frozen=True)
class CertLookupResult:
    cert_number: str
    grader: str
    grade: float
    last_sold_price: float | None
    last_sold_date: str | None
    card_ladder_value: float | None


class CardLadderCertLookup:
    def __init__(self, *, api_base: str, api_key: str, timeout: float = 10.0):
        self._base = api_base.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    def get_cert(self, cert_number: str) -> CertLookupResult:
        url = f"{self._base}/cert/{cert_number}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            raise CertNotFoundError(cert_number)
        if resp.status_code == 429:
            raise RateLimitedError(cert_number)
        resp.raise_for_status()
        data = resp.json()
        ls = data.get("last_sold") or {}
        return CertLookupResult(
            cert_number=str(data.get("cert_number", cert_number)),
            grader=str(data.get("grader", "")),
            grade=float(data.get("grade", 0)),
            last_sold_price=(float(ls["price"]) if "price" in ls else None),
            last_sold_date=(str(ls["date"]) if "date" in ls else None),
            card_ladder_value=(
                float(data["card_ladder_value"])
                if data.get("card_ladder_value") is not None
                else None
            ),
        )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/pricing/test_cert_lookup.py -v`
Expected: 3 passed.

- [ ] **Step 6: Remove the CSV importer + CLI subcommand**

Delete: `src/sportscards/ingest/cardladder.py`

```bash
git rm src/sportscards/ingest/cardladder.py
```

Edit `src/sportscards/cli/__main__.py` to remove the `cardladder import`
subcommand. Search for `cardladder` in that file and remove:
- the `cardladder = subparsers.add_parser(...)` block
- the `args.command == "cardladder"` branch
- the `from sportscards.ingest.cardladder import import_sales_csv` line

Run: `grep -rn "cardladder\|from sportscards.ingest.cardladder" src/ tests/ docs/ 2>/dev/null`

Expected: only references to `pricing/cert_lookup.py` remain.

If any reference to the old module remains in `docs/trader_console.md`,
remove the corresponding paragraph.

- [ ] **Step 7: Verify nothing breaks**

Run: `pytest -q` (full suite).
Expected: all tests pass; no `ImportError` for `cardladder`.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(pricing): Card Ladder cert lookup; remove dead CSV importer"
```

---

## Task 7: Prefect flow — pricing_refresh

**Files:**
- Create: `src/sportscards/flows/pricing_refresh.py`
- Test: `tests/pricing/test_flow.py`

- [ ] **Step 1: Write the failing test**

Create `tests/pricing/test_flow.py`:

```python
from datetime import date

import pytest

from sportscards.flows.pricing_refresh import pricing_refresh_flow


@pytest.mark.usefixtures("clean_db", "seeded_panel_with_pricing_inputs", "seeded_holding_for_flow")
def test_pricing_refresh_flow_writes_targets_and_signals(seeded_holding_for_flow):
    as_of = date(2026, 5, 27)
    result = pricing_refresh_flow(as_of=as_of)
    assert result["targets_written"] >= 1
    assert "signals_written" in result
```

Append fixture to `tests/pricing/conftest.py`:

```python
@pytest.fixture
def seeded_holding_for_flow(seeded_panel_with_pricing_inputs):
    from datetime import UTC, datetime
    from decimal import Decimal
    from sportscards.db.models import PortfolioHolding
    from sportscards.db.session import session_scope

    card_id = seeded_panel_with_pricing_inputs
    with session_scope() as s:
        s.add(PortfolioHolding(
            card_id=card_id,
            acquired_at=datetime(2026, 1, 1, tzinfo=UTC),
            acquired_cost_usd=Decimal("80"),
            channel="ebay",
            status="held",
            entry_factor_decile=10,
            entry_liquidity_tier="A",
        ))
    return card_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/pricing/test_flow.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the flow**

Create `src/sportscards/flows/pricing_refresh.py`:

```python
"""Weekly pricing refresh: trade_targets + exit_signal.

Runs immediately after ``flows/daily_ebay`` and the factor-panel build.
Idempotent for a given ``as_of`` date.
"""

from __future__ import annotations

import logging
from datetime import date

from prefect import flow, task

from sportscards.db.session import session_scope
from sportscards.pricing.exit_rules import evaluate_open_positions
from sportscards.pricing.targets import persist_targets_for_panel

log = logging.getLogger(__name__)


@task
def refresh_targets(as_of: date) -> int:
    with session_scope() as s:
        n = persist_targets_for_panel(s, as_of)
    log.info("trade_targets: wrote %d rows for as_of=%s", n, as_of)
    return n


@task
def refresh_exit_signals(as_of: date) -> int:
    with session_scope() as s:
        fired = evaluate_open_positions(s, as_of)
    log.info("exit_signal: fired %d signals for as_of=%s", len(fired), as_of)
    return len(fired)


@flow(name="pricing-refresh")
def pricing_refresh_flow(as_of: date) -> dict[str, int]:
    targets_written = refresh_targets(as_of)
    signals_written = refresh_exit_signals(as_of)
    return {
        "targets_written": targets_written,
        "signals_written": signals_written,
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/pricing/test_flow.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add src/sportscards/flows/pricing_refresh.py \
        tests/pricing/test_flow.py tests/pricing/conftest.py
git commit -m "feat(pricing): pricing_refresh_flow chaining targets + exit signals"
```

---

## Task 8: Trader Console — pricing panel + exit signals tab

**Files:**
- Modify: `src/sportscards/reports/queries.py`
- Modify: `src/sportscards/reports/render.py`
- Test: `tests/pricing/test_reports_queries.py`

- [ ] **Step 1: Inspect existing queries.py shape**

Run: `head -40 src/sportscards/reports/queries.py`

Note the conventions in use (cursor vs ORM, return type — DataFrame vs
list of dataclasses). Match them in the new functions below.

- [ ] **Step 2: Write the failing test**

Create `tests/pricing/test_reports_queries.py`:

```python
from datetime import date

import pytest

from sportscards.db.session import session_scope
from sportscards.reports.queries import get_open_exit_signals, get_trade_targets


@pytest.mark.usefixtures("clean_db", "seeded_panel_with_pricing_inputs")
def test_get_trade_targets_returns_rows(seeded_panel_with_pricing_inputs):
    from sportscards.pricing.targets import persist_targets_for_panel
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        persist_targets_for_panel(s, as_of)
    df = get_trade_targets(card_ids=[seeded_panel_with_pricing_inputs], as_of=as_of)
    assert len(df) == 1
    assert df.iloc[0]["bid_max"] < df.iloc[0]["fair_value"] < df.iloc[0]["sell_target"]


@pytest.mark.usefixtures("clean_db", "seeded_holding_for_flow")
def test_get_open_exit_signals_only_returns_unresolved(seeded_holding_for_flow):
    from datetime import UTC, datetime
    from sportscards.db.models import ExitSignal
    as_of = date(2026, 5, 27)
    with session_scope() as s:
        s.add(ExitSignal(
            holding_id=1,
            rule_triggered="target_hit",
            recommended_action="sell_50pct",
            as_of_date=as_of,
            resolved_at=None,
        ))
        s.add(ExitSignal(
            holding_id=1,
            rule_triggered="price_stop",
            recommended_action="sell_100pct",
            as_of_date=as_of,
            resolved_at=datetime(2026, 5, 27, tzinfo=UTC),
        ))
    df = get_open_exit_signals()
    assert (df["resolved_at"].isna()).all()
    assert len(df) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/pricing/test_reports_queries.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 4: Add queries**

Append to `src/sportscards/reports/queries.py`:

```python
from datetime import date
from collections.abc import Iterable

import pandas as pd
from sqlalchemy import select

from sportscards.db.models import ExitSignal, TradeTargets
from sportscards.db.session import session_scope


def get_trade_targets(
    *, card_ids: Iterable[int], as_of: date
) -> pd.DataFrame:
    ids = list(card_ids)
    if not ids:
        return pd.DataFrame(
            columns=[
                "card_id", "as_of_date", "fair_value", "bid_max",
                "sell_target", "stop_loss", "confidence",
                "half_spread_pct", "liquidity_margin_pct",
            ]
        )
    with session_scope() as s:
        rows = s.execute(
            select(TradeTargets)
            .where(TradeTargets.card_id.in_(ids))
            .where(TradeTargets.as_of_date == as_of)
        ).scalars().all()
        return pd.DataFrame(
            [
                {
                    "card_id": r.card_id,
                    "as_of_date": r.as_of_date,
                    "fair_value": float(r.fair_value),
                    "bid_max": float(r.bid_max),
                    "sell_target": float(r.sell_target),
                    "stop_loss": float(r.stop_loss),
                    "confidence": float(r.confidence),
                    "half_spread_pct": float(r.half_spread_pct),
                    "liquidity_margin_pct": float(r.liquidity_margin_pct),
                }
                for r in rows
            ]
        )


def get_open_exit_signals() -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(
            select(ExitSignal).where(ExitSignal.resolved_at.is_(None))
            .order_by(ExitSignal.as_of_date.desc(), ExitSignal.id.desc())
        ).scalars().all()
        return pd.DataFrame(
            [
                {
                    "id": r.id,
                    "holding_id": r.holding_id,
                    "rule_triggered": r.rule_triggered,
                    "recommended_action": r.recommended_action,
                    "as_of_date": r.as_of_date,
                    "notes": r.notes,
                    "resolved_at": r.resolved_at,
                }
                for r in rows
            ]
        )


def resolve_exit_signal(signal_id: int) -> None:
    from datetime import UTC, datetime
    with session_scope() as s:
        sig = s.get(ExitSignal, signal_id)
        if sig is None:
            return
        sig.resolved_at = datetime.now(tz=UTC)
```

- [ ] **Step 5: Add render panels**

Append to `src/sportscards/reports/render.py`:

```python
import streamlit as st

from sportscards.reports.queries import (
    get_open_exit_signals,
    get_trade_targets,
    resolve_exit_signal,
)


def render_pricing_panel(card_ids: list[int], as_of):
    df = get_trade_targets(card_ids=card_ids, as_of=as_of)
    if df.empty:
        st.info("No trade targets for the selected cards.")
        return
    st.subheader("Trade Targets")
    st.dataframe(
        df[["card_id", "bid_max", "fair_value", "sell_target", "stop_loss", "confidence"]],
        use_container_width=True,
        hide_index=True,
    )


def render_exit_signals_tab():
    df = get_open_exit_signals()
    st.subheader("Open Exit Signals")
    if df.empty:
        st.success("No unresolved exit signals.")
        return
    for _, row in df.iterrows():
        cols = st.columns([1, 2, 2, 1])
        cols[0].write(f"#{row['id']}")
        cols[1].write(f"holding {row['holding_id']} · {row['rule_triggered']}")
        cols[2].write(row["notes"] or "")
        if cols[3].button("Resolve", key=f"resolve-{row['id']}"):
            resolve_exit_signal(int(row["id"]))
            st.rerun()
```

Wire the tab into the main render entrypoint in `render.py`. Find the
existing tab list (e.g. `tabs = st.tabs([...])`) and add `"Exit Signals"`
to it, then call `render_exit_signals_tab()` in the corresponding
branch. If no tab layout exists yet, append the panel at the bottom of
the dashboard for now.

- [ ] **Step 6: Run tests**

Run: `pytest tests/pricing/test_reports_queries.py -v`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add src/sportscards/reports/queries.py \
        src/sportscards/reports/render.py \
        tests/pricing/test_reports_queries.py
git commit -m "feat(pricing): trader console pricing panel + exit-signals tab"
```

---

## Task 9: Wire-up + docs + final verification

**Files:**
- Modify: `docs/trader_console.md`
- Modify: `prefect.yaml` (deployment for `pricing-refresh`)

- [ ] **Step 1: Add Prefect deployment**

Open `prefect.yaml` and locate the deployments list (sibling format to
the existing `daily_ebay` deployment). Append:

```yaml
- name: pricing-refresh
  entrypoint: src/sportscards/flows/pricing_refresh.py:pricing_refresh_flow
  schedules:
    - cron: "0 6 * * 1"            # Mondays 06:00, after weekly factor panel
      timezone: "America/New_York"
  parameters: {}
```

Adapt key names to match whatever convention the file already uses
(`work_pool`, `tags`, etc.) — copy from the closest existing
deployment in the same file.

- [ ] **Step 2: Update docs**

Edit `docs/trader_console.md`. Remove any paragraph that mentions
`sportscards cardladder import` or CSV import. Add a section:

```markdown
## Pricing & Exit Signals

The pricing module turns factor recommendations into actionable trade
targets. After each factor-panel refresh, the `pricing-refresh` flow
writes one row per recommended card to `trade_targets` and emits
`exit_signal` rows for any open holding that trips an exit rule.

- **Trade targets** are surfaced per card in the recommendation table:
  `bid_max | fair_value | sell_target | stop_loss`, plus a confidence
  score (recency of last comp).
- **Exit signals** appear in the "Exit Signals" tab. Each signal has a
  one-click Resolve action.

Card Ladder is no longer ingested as a CSV. The browser remains the
trader's manual sanity check; programmatic queries go through the cert
lookup client (`pricing/cert_lookup.py`) used by the grading-EV sleeve.
```

- [ ] **Step 3: Full-suite verification**

Run: `pytest -q && ruff check src/sportscards/pricing src/sportscards/flows/pricing_refresh.py tests/pricing && mypy src/sportscards/pricing 2>/dev/null || true`

Expected: all tests pass; ruff clean; mypy (if configured) clean.

- [ ] **Step 4: Smoke-test the Streamlit app**

Run: `make dashboard` (or the project's equivalent command — look in
`Makefile`). Open the URL it prints. Confirm the "Exit Signals" tab
renders and the per-card pricing panel shows values.

If `make dashboard` is unavailable, run directly:
`PYTHONPATH=$(pwd)/src streamlit run src/sportscards/reports/render.py`

- [ ] **Step 5: Commit**

```bash
git add prefect.yaml docs/trader_console.md
git commit -m "chore(pricing): schedule pricing-refresh flow + update docs"
```

- [ ] **Step 6: Open PR**

```bash
gh pr create --title "feat: pricing & exit-rules layer" --body "$(cat <<'EOF'
## Summary
- New `src/sportscards/pricing/` package: index-anchored fair value, Corwin-Schultz half-spread, liquidity margin, target derivation, five-rule exit evaluator, Card Ladder cert lookup
- Two new tables (`trade_targets`, `exit_signal`); `portfolio_holdings` gains `entry_factor_decile` and `entry_liquidity_tier`
- New Prefect flow `pricing-refresh` chains after the factor panel
- Trader Console gets a pricing panel and an "Exit Signals" tab
- Removed dead `ingest/cardladder.py` CSV importer

## Test plan
- [ ] `pytest tests/pricing -v` — all green
- [ ] `alembic upgrade head` then `alembic downgrade -1` and back up — migration round-trips cleanly
- [ ] Smoke-test Streamlit Trader Console: pricing panel renders, exit signals tab lists at least one fired rule
- [ ] Trigger `prefect deployment run pricing-refresh` once manually; verify row counts in `trade_targets` and `exit_signal`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Spec coverage check

| Spec section | Task(s) |
|---|---|
| `pricing/fair_value.py` (Steps 1-2) | Task 3 |
| `pricing/implicit_spread.py` (Step 3) | Task 2 |
| `pricing/targets.py` (Steps 4-5) | Task 4 |
| `pricing/exit_rules.py` (5 rules) | Task 5 |
| `pricing/cert_lookup.py` | Task 6 |
| `trade_targets` table | Task 1 |
| `exit_signal` table | Task 1 |
| `portfolio_holdings` columns | Task 1 |
| Prefect `pricing_refresh_flow` | Task 7, scheduling in Task 9 |
| Trader Console pricing panel & exit-signal tab | Task 8 |
| Deprecate `ingest/cardladder.py` | Task 6 |
| Docs update | Task 9 |
| Tests for every module | Tasks 2-8 |
