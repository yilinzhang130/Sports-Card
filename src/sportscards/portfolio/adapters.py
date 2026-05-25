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
    """For each player with a stardom score whose fit_at <= as_of and whose
    draft_year is within 5 years of as_of's year, return one row per player
    keyed on their flagship rookie card (Prizm Silver RC > Prizm Base RC >
    Topps Chrome RC). Columns: player_id, card_id, stardom_score, draft_year,
    computed_at. Returns None if no eligible scores/cards exist."""
    from sqlalchemy import select as _select

    from sportscards.db.models import Card as _Card
    from sportscards.db.models import PlayerStardomScore as _PSS

    if not _has_table(session, "player_stardom_score"):
        return None

    score_rows = session.execute(
        _select(
            _PSS.player_id,
            _PSS.premium,
            _PSS.draft_year,
            _PSS.fit_at,
        ).where(_PSS.fit_at <= as_of)
    ).all()
    if not score_rows:
        return None
    scores = pd.DataFrame(score_rows, columns=["player_id", "premium", "draft_year", "fit_at"])
    scores = scores.sort_values("fit_at").groupby("player_id", as_index=False).tail(1)
    scores = scores[scores["draft_year"] >= as_of.year - 5]
    if scores.empty:
        return None

    card_rows = session.execute(
        _select(_Card.card_id, _Card.player_id, _Card.set_name, _Card.parallel)
        .where(_Card.is_rookie == True)  # noqa: E712
        .where(_Card.player_id.in_(scores["player_id"].tolist()))
    ).all()
    if not card_rows:
        return None
    cards = pd.DataFrame(card_rows, columns=["card_id", "player_id", "set_name", "parallel"])

    def _rank(row: pd.Series) -> int:
        sn = (row["set_name"] or "").lower()
        par = (row["parallel"] or "").lower()
        if "prizm" in sn and "silver" in par:
            return 0
        if "prizm" in sn and par in ("base", ""):
            return 1
        if "topps chrome" in sn:
            return 2
        return 9

    cards["rank"] = cards.apply(_rank, axis=1)
    cards = cards[cards["rank"] < 9]
    if cards.empty:
        return None
    cards = cards.sort_values(["player_id", "rank"]).groupby("player_id", as_index=False).head(1)

    out = cards.merge(scores, on="player_id", how="inner")
    return out.rename(columns={"premium": "stardom_score", "fit_at": "computed_at"})[
        ["player_id", "card_id", "stardom_score", "draft_year", "computed_at"]
    ]


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
