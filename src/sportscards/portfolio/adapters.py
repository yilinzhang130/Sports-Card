"""Adapter layer between Phase 2/3 outputs and the portfolio engine.

Each loader returns ``None`` when the upstream table is missing/empty so
that downstream code degrades gracefully — anchor-only portfolio and
backtests still run when Phase 2B / Phase 3 aren't merged yet.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import inspect, select


def _has_table(session: Any, name: str) -> bool:
    try:
        return inspect(session.get_bind()).has_table(name)
    except Exception:
        return False


def load_anchors(session: Any, as_of: datetime | None = None) -> pd.DataFrame:
    """Return ``(card_id, last_price)`` for resolved anchors.

    Cards missing from ``card_master`` are skipped (with a warning from
    ``resolve_anchors``). Cards with no recent sales are still returned with
    ``last_price=NaN`` so the caller can decide whether to drop or impute.
    """
    from sqlalchemy import func

    from sportscards.db.models import TxClean
    from sportscards.portfolio.anchors import resolve_anchors

    resolved = [(spec, cid) for spec, cid in resolve_anchors(session) if cid is not None]
    if not resolved:
        return pd.DataFrame(columns=["card_id", "last_price"])
    card_ids = [cid for _, cid in resolved]
    q = select(TxClean.card_id, func.avg(TxClean.price_usd).label("last_price")).where(
        TxClean.card_id.in_(card_ids)
    )
    if as_of is not None:
        q = q.where(TxClean.sold_at < as_of)
    q = q.group_by(TxClean.card_id)
    rows = session.execute(q).all()
    price_map = {r.card_id: float(r.last_price) for r in rows}
    return pd.DataFrame(
        [{"card_id": cid, "last_price": price_map.get(cid, float("nan"))} for cid in card_ids]
    )


def load_mispricing(session: Any, as_of: datetime) -> pd.DataFrame | None:
    """Phase 2B output. Returns None if the table isn't there yet.

    Expected schema: ``card_id, mispricing_residual, computed_at, sport, parallel_tier``.
    Raises if any row has ``computed_at >= as_of`` (look-ahead canary).
    """
    if not _has_table(session, "tx_mispricing"):
        return None
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT card_id, mispricing_residual, computed_at, sport, parallel_tier "
            "FROM tx_mispricing WHERE computed_at < :as_of"
        ),
        {"as_of": as_of},
    ).mappings().all()
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if "computed_at" in df.columns and (df["computed_at"] >= as_of).any():
        raise RuntimeError("look-ahead detected in tx_mispricing")
    return df


def load_stardom(session: Any, as_of: datetime) -> pd.DataFrame | None:
    """Phase 3 output. Returns None if the table isn't there yet.

    Expected schema: ``card_id, stardom_score, computed_at, last_price``.
    """
    if not _has_table(session, "player_stardom"):
        return None
    from sqlalchemy import text

    rows = session.execute(
        text(
            "SELECT card_id, stardom_score, computed_at, last_price "
            "FROM player_stardom WHERE computed_at < :as_of"
        ),
        {"as_of": as_of},
    ).mappings().all()
    if not rows:
        return None
    return pd.DataFrame(rows)


def load_price_panel(
    session: Any,
    card_ids: list[int],
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Daily VWAP per card from tx_clean. Returns long-format frame."""
    from sqlalchemy import func

    from sportscards.db.models import TxClean

    if not card_ids:
        return pd.DataFrame(columns=["date", "card_id", "vwap", "volume"])
    rows = session.execute(
        select(
            func.date(TxClean.sold_at).label("date"),
            TxClean.card_id,
            func.avg(TxClean.price_usd).label("vwap"),
            func.count().label("volume"),
        )
        .where(TxClean.card_id.in_(card_ids))
        .where(TxClean.sold_at >= start)
        .where(TxClean.sold_at < end)
        .group_by("date", TxClean.card_id)
    ).all()
    return pd.DataFrame(
        [
            {"date": r.date, "card_id": r.card_id, "vwap": float(r.vwap), "volume": r.volume}
            for r in rows
        ]
    )
