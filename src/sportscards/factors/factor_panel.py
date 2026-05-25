"""Combine momentum + liquidity into the ``factor_panel`` hypertable.

Recompute is intended weekly: the signals are slow.
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sportscards.db.models import FactorPanel
from sportscards.factors.liquidity import compute_liquidity_panel
from sportscards.factors.momentum import compute_cs_momentum


def _as_of_ts(as_of: date | datetime | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(as_of)
    with contextlib.suppress(TypeError, AttributeError):
        ts = ts.tz_localize(None)
    return ts


def _dec(v: object, q: str = "0.000001") -> Decimal | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return Decimal(str(round(float(v), len(q.split(".")[1]))))
    except (ValueError, ArithmeticError):
        return None


def build_panel(
    session: Session,
    as_of: date | datetime,
    *,
    lookback_days: int = 90,
) -> pd.DataFrame:
    """Compute the combined factor panel as a DataFrame (no persistence)."""
    as_of_ts = _as_of_ts(as_of)
    mom = compute_cs_momentum(session, as_of_ts, lookback=lookback_days)
    liq = compute_liquidity_panel(session, as_of_ts, window_days=lookback_days)

    out = liq.merge(mom, on="card_id", how="left")
    out["is_hyped"] = out["is_hyped"].fillna(False).astype(bool)
    return out


def persist_panel(
    session: Session,
    as_of: date | datetime,
    *,
    lookback_days: int = 90,
) -> int:
    """Compute and upsert ``factor_panel`` rows for every card at ``as_of``."""
    as_of_ts = _as_of_ts(as_of)
    df = build_panel(session, as_of_ts, lookback_days=lookback_days)
    if df.empty:
        return 0

    # Replace existing rows for this as_of_date
    session.execute(delete(FactorPanel).where(FactorPanel.as_of_date == as_of_ts.to_pydatetime()))

    n = 0
    for row in df.itertuples(index=False):
        session.add(
            FactorPanel(
                card_id=int(row.card_id),
                as_of_date=as_of_ts.to_pydatetime(),
                r30=_dec(getattr(row, "r30", None)),
                r90=_dec(getattr(row, "r90", None)),
                r365=_dec(getattr(row, "r365", None)),
                cs_momentum_pct=_dec(getattr(row, "cs_momentum_pct", None), q="0.0001"),
                is_hyped=bool(getattr(row, "is_hyped", False)),
                sales_count_90d=int(row.sales_count_90d),
                dollar_volume_90d=_dec(row.dollar_volume_90d, q="0.01"),
                bid_ask_proxy=_dec(row.bid_ask_proxy, q="0.00001"),
                last_sale_recency_days=(
                    None if pd.isna(row.last_sale_recency_days) else int(row.last_sale_recency_days)
                ),
                liquidity_tier=str(row.liquidity_tier),
            )
        )
        n += 1
    session.flush()
    return n
