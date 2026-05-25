"""Liquidity factors: per-card sales activity, depth, and bid-ask proxy.

Bid-ask proxy uses the (90th - 10th)/median spread of comp prices within
the window as a stand-in for true bid/ask. Ungraded vs graded transactions
are normalized separately by computing the spread only over PSA-graded
sales (which is the universe we trade and the universe with comparable
quality).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from sportscards.db.models import TxClean

TIER_A_MIN = 10  # sales / 90d
TIER_B_MIN = 3
TIER_C_MIN = 1


def _to_naive(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts, errors="coerce")
    try:
        return ts.dt.tz_localize(None)
    except (TypeError, AttributeError):
        return ts


def _as_of_ts(as_of: date | datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(as_of)
    try:
        ts = ts.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    return ts


def _tier(sales_count: int) -> str:
    if sales_count >= TIER_A_MIN:
        return "A"
    if sales_count >= TIER_B_MIN:
        return "B"
    if sales_count >= TIER_C_MIN:
        return "C"
    return "D"


def _load_tx_panel(
    session: Session,
    as_of: pd.Timestamp,
    window_days: int,
    *,
    card_ids: list[int] | None = None,
) -> pd.DataFrame:
    cutoff = as_of - timedelta(days=window_days)
    stmt = (
        select(TxClean.card_id, TxClean.sold_at, TxClean.price_usd, TxClean.slab_grader)
        .where(TxClean.sold_at < as_of)
        .where(TxClean.sold_at >= cutoff)
    )
    if card_ids is not None:
        stmt = stmt.where(TxClean.card_id.in_(card_ids))
    rows = session.execute(stmt).all()
    if not rows:
        return pd.DataFrame(columns=["card_id", "sold_at", "price_usd", "slab_grader"])
    df = pd.DataFrame(rows, columns=["card_id", "sold_at", "price_usd", "slab_grader"])
    df["sold_at"] = _to_naive(df["sold_at"])
    df["price_usd"] = pd.to_numeric(df["price_usd"], errors="coerce")
    return df.dropna(subset=["sold_at", "price_usd"])


def _last_sale_recency(
    session: Session, as_of: pd.Timestamp, card_id: int
) -> int | None:
    """Days since the last sale for ``card_id`` (None if no sales ever)."""
    from sqlalchemy import func

    row = session.execute(
        select(func.max(TxClean.sold_at)).where(
            TxClean.card_id == card_id, TxClean.sold_at < as_of
        )
    ).one()
    last = row[0]
    if last is None:
        return None
    last_ts = pd.Timestamp(last)
    try:
        last_ts = last_ts.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    delta = (as_of - last_ts).days
    return max(0, int(delta))


def liquidity_metrics(
    session: Session,
    card_id: int,
    as_of: date | datetime,
    *,
    window_days: int = 90,
) -> dict[str, object]:
    """Per-card liquidity snapshot at ``as_of`` over a trailing ``window_days``.

    Returns sales count, dollar volume, bid-ask proxy, last-sale recency,
    and a tier bucket. The bid-ask proxy is computed over PSA-graded sales
    only (where quality is comparable).
    """
    as_of_ts = _as_of_ts(as_of)
    df = _load_tx_panel(session, as_of_ts, window_days, card_ids=[card_id])
    sales_count = int(len(df))
    dollar_volume = Decimal(str(round(float(df["price_usd"].sum()), 2))) if sales_count else Decimal(
        "0.00"
    )

    bid_ask: Decimal | None = None
    psa = df[df["slab_grader"] == "PSA"]
    if len(psa) >= 3:
        prices = psa["price_usd"].astype(float)
        med = float(prices.median())
        if med > 0:
            hi = float(np.percentile(prices, 90))
            lo = float(np.percentile(prices, 10))
            bid_ask = Decimal(str(round((hi - lo) / med, 5)))

    recency = _last_sale_recency(session, as_of_ts, card_id)
    tier = _tier(sales_count)

    return {
        "card_id": card_id,
        "sales_count_90d": sales_count,
        "dollar_volume_90d": dollar_volume,
        "bid_ask_proxy": bid_ask,
        "last_sale_recency_days": recency,
        "liquidity_tier": tier,
    }


def compute_liquidity_panel(
    session: Session,
    as_of: date | datetime,
    *,
    window_days: int = 90,
    universe_card_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Vectorized liquidity metrics across the universe.

    Universe defaults to every card that appears in ``card_master``. Cards
    with zero sales in the window still appear (tier 'D'), since the
    portfolio filter needs to know about them.
    """
    from sportscards.db.models import Card

    if universe_card_ids is None:
        universe_card_ids = [
            int(cid) for cid in session.execute(select(Card.card_id)).scalars().all()
        ]
    if not universe_card_ids:
        return pd.DataFrame(
            columns=[
                "card_id",
                "sales_count_90d",
                "dollar_volume_90d",
                "bid_ask_proxy",
                "last_sale_recency_days",
                "liquidity_tier",
            ]
        )

    as_of_ts = _as_of_ts(as_of)
    df = _load_tx_panel(
        session, as_of_ts, window_days, card_ids=universe_card_ids
    )

    # Aggregate sales count + dollar volume per card
    if df.empty:
        agg = pd.DataFrame(
            {"card_id": universe_card_ids, "sales_count_90d": 0, "dollar_volume_90d": 0.0}
        )
    else:
        agg = (
            df.groupby("card_id")
            .agg(
                sales_count_90d=("price_usd", "size"),
                dollar_volume_90d=("price_usd", "sum"),
            )
            .reset_index()
        )

    # Bid-ask over PSA only
    if df.empty:
        ba = pd.DataFrame({"card_id": [], "bid_ask_proxy": []})
    else:
        psa = df[df["slab_grader"] == "PSA"]
        if psa.empty:
            ba = pd.DataFrame({"card_id": [], "bid_ask_proxy": []})
        else:

            def _ba(g: pd.DataFrame) -> float:
                if len(g) < 3:
                    return float("nan")
                p = g["price_usd"].astype(float).to_numpy()
                med = float(np.median(p))
                if med <= 0:
                    return float("nan")
                return (float(np.percentile(p, 90)) - float(np.percentile(p, 10))) / med

            ba = (
                psa.groupby("card_id")
                .apply(_ba, include_groups=False)
                .rename("bid_ask_proxy")
                .reset_index()
            )

    # Last-sale recency (vectorized over the broader history, not just window)
    from sqlalchemy import func

    last_rows = session.execute(
        select(TxClean.card_id, func.max(TxClean.sold_at))
        .where(TxClean.card_id.in_(universe_card_ids))
        .where(TxClean.sold_at < as_of_ts)
        .group_by(TxClean.card_id)
    ).all()
    last_df = pd.DataFrame(last_rows, columns=["card_id", "last_sold_at"])
    if not last_df.empty:
        last_df["last_sold_at"] = _to_naive(last_df["last_sold_at"])
        last_df["last_sale_recency_days"] = (
            (as_of_ts - last_df["last_sold_at"]).dt.days.clip(lower=0).astype("Int64")
        )
        last_df = last_df[["card_id", "last_sale_recency_days"]]
    else:
        last_df = pd.DataFrame(columns=["card_id", "last_sale_recency_days"])

    # Stitch onto universe
    universe = pd.DataFrame({"card_id": universe_card_ids})
    out = (
        universe.merge(agg, on="card_id", how="left")
        .merge(ba, on="card_id", how="left")
        .merge(last_df, on="card_id", how="left")
    )
    out["sales_count_90d"] = out["sales_count_90d"].fillna(0).astype(int)
    out["dollar_volume_90d"] = out["dollar_volume_90d"].fillna(0.0).astype(float)
    out["liquidity_tier"] = out["sales_count_90d"].apply(_tier)
    return out[
        [
            "card_id",
            "sales_count_90d",
            "dollar_volume_90d",
            "bid_ask_proxy",
            "last_sale_recency_days",
            "liquidity_tier",
        ]
    ].reset_index(drop=True)
