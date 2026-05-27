"""Exit-rule evaluator over open ``portfolio_holdings``.

Five rules, evaluated independently per (holding, as_of_date). Multiple
rules can fire for the same holding; each writes its own ``exit_signal``
row. Re-running on the same as_of_date is idempotent via the
``uq_exit_signal_holding_rule_day`` unique constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pandas as pd
from sqlalchemy import select
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

        # Rule 1: target hit
        if price is not None and tt is not None and price >= float(tt.sell_target):
            fired.append(ExitSignalDTO(
                h.holding_id, "target_hit", "sell_50pct", as_of,
                f"last_sold={price:.2f} >= sell_target={tt.sell_target}",
            ))

        # Rule 2: factor reversal
        if h.entry_factor_decile == 10:
            rank_pct = factor_decile_rank.get(h.card_id)
            if rank_pct is not None and rank_pct < (1.0 - FACTOR_LONG_DECILE_TOP_PCT):
                fired.append(ExitSignalDTO(
                    h.holding_id, "factor_reversal", "sell_100pct", as_of,
                    f"factor_rank_pct={rank_pct:.2f} (dropped from top decile)",
                ))

        # Rule 3: time stop
        acquired = h.acquired_at if h.acquired_at.tzinfo else h.acquired_at.replace(tzinfo=UTC)
        held_days = (datetime.combine(as_of, datetime.min.time(), tzinfo=UTC) - acquired).days
        cost_threshold = TIME_STOP_PRICE_RATIO * float(h.acquired_cost_usd)
        if held_days > TIME_STOP_DAYS and price is not None and price < cost_threshold:
            fired.append(ExitSignalDTO(
                h.holding_id, "time_stop", "sell_100pct", as_of,
                f"held {held_days}d, price {price:.2f} < {TIME_STOP_PRICE_RATIO}×cost",
            ))

        # Rule 4: price stop
        if price is not None and tt is not None and price < float(tt.stop_loss):
            fired.append(ExitSignalDTO(
                h.holding_id, "price_stop", "sell_100pct", as_of,
                f"last_sold={price:.2f} < stop_loss={tt.stop_loss}",
            ))

        # Rule 5: liquidity degrade
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

    _persist_signals_dedup(session, fired)
    return fired


def _persist_signals_dedup(session: Session, signals: list[ExitSignalDTO]) -> None:
    """Insert each signal; skip rows that violate the unique constraint
    (rerun-safe). Portable across SQLite and Postgres."""
    if not signals:
        return
    for s in signals:
        # Pre-check existence to avoid abort-on-error in SQLite
        exists = session.execute(
            select(ExitSignal.id).where(
                ExitSignal.holding_id == s.holding_id,
                ExitSignal.rule_triggered == s.rule_triggered,
                ExitSignal.as_of_date == s.as_of_date,
            )
        ).first()
        if exists:
            continue
        session.add(ExitSignal(
            holding_id=s.holding_id,
            rule_triggered=s.rule_triggered,
            recommended_action=s.recommended_action,
            as_of_date=s.as_of_date,
            notes=s.notes,
        ))
        session.flush()


def _last_sold_lookup(
    session: Session, card_ids: list[int], as_of: date
) -> dict[int, float]:
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
