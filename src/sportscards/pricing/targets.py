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

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from sportscards.db.models import FactorPanel, TradeTargets, TxClean
from sportscards.pricing.fair_value import compute_fair_value
from sportscards.pricing.implicit_spread import estimate_half_spread

LIQUIDITY_MARGIN: dict[str, float] = {"A": 0.03, "B": 0.05, "C": 0.10, "D": 0.20}
K_SELL_TARGET = 0.15


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
    # Tier-anchored stop, capped strictly below bid_max for wide-spread cards
    # so the invariant stop_loss < bid_max holds even when half_spread > margin.
    tier_anchored_stop = fair_value * (1.0 - 2.0 * margin)
    stop_cap = bid_max - margin * fair_value
    stop_loss = min(tier_anchored_stop, stop_cap)
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
    from datetime import datetime
    as_of_ts = datetime.combine(as_of, datetime.min.time())
    rows = session.execute(
        select(FactorPanel.card_id, FactorPanel.cs_momentum_pct).where(
            FactorPanel.as_of_date == as_of_ts
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
    from datetime import datetime
    as_of_ts = datetime.combine(as_of, datetime.min.time())
    row = session.execute(
        select(FactorPanel.liquidity_tier).where(
            FactorPanel.card_id == card_id,
            FactorPanel.as_of_date == as_of_ts,
        )
    ).first()
    return str(row[0]) if row else "C"


def _hedonic_predicted_lookup(session: Session, as_of: date) -> dict[int, float]:
    """Hook for hedonic predictions per card. Returns {} today; the factor
    pipeline writes residuals to FactorPanel, but per-card point predictions
    are a follow-up. compute_fair_value handles the missing case."""
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
    from datetime import datetime
    as_of_ts = datetime.combine(as_of, datetime.min.time())
    card_rows = session.execute(
        select(FactorPanel.card_id).where(FactorPanel.as_of_date == as_of_ts)
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
