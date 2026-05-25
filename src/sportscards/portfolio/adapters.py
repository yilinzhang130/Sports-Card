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
        return bool(inspect(session.get_bind()).has_table(name))
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

    rows = (
        session.execute(
            text(
                "SELECT card_id, mispricing_residual, computed_at, sport, parallel_tier "
                "FROM tx_mispricing WHERE computed_at < :as_of"
            ),
            {"as_of": as_of},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if "computed_at" in df.columns and (df["computed_at"] >= as_of).any():
        raise RuntimeError("look-ahead detected in tx_mispricing")
    return df


def load_stardom(session: Any, as_of: datetime) -> pd.DataFrame | None:
    """Phase 3 output (``player_stardom_score``). Returns None if absent.

    Joins ``player_stardom_score`` to ``card_master`` so the prospect sleeve
    can be expressed in card_id units. Stardom is keyed by (player_id,
    model_version); we pick each player's latest ``fit_at`` < as_of and
    expand to their rookie cards.
    """
    if not _has_table(session, "player_stardom_score"):
        return None
    from sqlalchemy import text

    rows = (
        session.execute(
            text(
                "SELECT c.card_id, s.premium AS stardom_score, s.fit_at AS computed_at "
                "FROM player_stardom_score s "
                "JOIN card_master c ON c.player_id = s.player_id "
                "WHERE s.fit_at < :as_of AND c.is_rookie = true"
            ),
            {"as_of": as_of},
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # Attach last_price from tx_clean for sizing
    from sqlalchemy import func

    from sportscards.db.models import TxClean

    price_rows = session.execute(
        select(TxClean.card_id, func.avg(TxClean.price_usd).label("last_price"))
        .where(TxClean.card_id.in_(df["card_id"].tolist()))
        .where(TxClean.sold_at < as_of)
        .group_by(TxClean.card_id)
    ).all()
    price_map = {r.card_id: float(r.last_price) for r in price_rows}
    df["last_price"] = df["card_id"].map(price_map)
    return df


def load_catalyst_scores(
    session: Any, card_ids: list[int], as_of: datetime
) -> dict[int, float]:
    """Map ``card_id → catalyst_score`` for the card's player as of date.

    Joins ``card_master → player_master → catalyst.compute_catalyst_scores_bulk``.
    Returns 0.0 for cards whose player has no recent events, or whose
    ``card_id`` has no ``player_id``.
    """
    if not card_ids:
        return {}
    from sportscards.db.models import Card
    from sportscards.factors.catalyst import compute_catalyst_scores_bulk

    rows = session.execute(
        select(Card.card_id, Card.player_id).where(Card.card_id.in_(card_ids))
    ).all()
    card_to_player: dict[int, int] = {
        r.card_id: r.player_id for r in rows if r.player_id is not None
    }
    player_ids = list({pid for pid in card_to_player.values()})
    if not player_ids:
        return {cid: 0.0 for cid in card_ids}
    bulk = compute_catalyst_scores_bulk(session, player_ids, as_of)
    out: dict[int, float] = {}
    for cid in card_ids:
        pid = card_to_player.get(cid)
        if pid is None:
            out[cid] = 0.0
        else:
            out[cid] = float(bulk.get(pid, 0))
    return out


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
