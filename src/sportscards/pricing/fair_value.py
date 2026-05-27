"""Index-anchored fair value with recency-confidence blend.

Approach (Card Ladder "CL Value" style):
1. Reproject the card's last confirmed sold price forward using the
   relevant player/parallel index:
       index_projected = last_sold * (index_now / index_at_last_sold)
2. Compute a recency-confidence weight that decays exponentially in the
   number of days since the last sale.
3. Blend ``index_projected`` with the hedonic cross-sectional prediction
   using the confidence as the index-side weight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

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
    confidence: float
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
    """Compute fair value for a single card."""
    as_of_ts = datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)

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
    if last_sold_at.tzinfo is None:
        last_sold_at = last_sold_at.replace(tzinfo=UTC)
    days_since = (as_of_ts - last_sold_at).days
    last_sold_winsorized = _winsorize_last_sold(prices)

    card = session.get(Card, card_id)
    index_projected: float | None = None
    if card is not None:
        index_now, index_then = _index_pair(session, card, as_of_ts, last_sold_at)
        if index_now is not None and index_then is not None and index_then > 0:
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

    `RepeatSalesIndex` PK is (period_start, sport, bucket, grade_tier, era).
    We pick the (sport, era) slice for the card and pin bucket="weekly",
    grade_tier="PSA10" as the default series.
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
    sport = getattr(card, "sport", None) or "NBA"
    era = "modern" if card.year >= 2010 else "vintage"
    return sport, era
