"""portfolio_holdings ledger CRUD."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func

from reports.app._components.audit import write_audit
from sportscards.db.models import PortfolioHolding
from sportscards.db.session import session_scope


def add_holding(
    *,
    card_id: int,
    cert_number: str | None,
    slab_grader: str | None,
    slab_grade: Decimal | None,
    acquired_at: datetime,
    acquired_cost_usd: Decimal,
    channel: str,
    actor: str = "ui",
) -> int:
    with session_scope() as s:
        row = PortfolioHolding(
            card_id=card_id,
            cert_number=cert_number,
            slab_grader=slab_grader,
            slab_grade=slab_grade,
            acquired_at=acquired_at,
            acquired_cost_usd=acquired_cost_usd,
            channel=channel,
            status="held",
        )
        s.add(row)
        s.flush()
        holding_id = row.holding_id
    write_audit(
        "holding_add",
        {"holding_id": holding_id, "card_id": card_id, "cost": str(acquired_cost_usd)},
        actor=actor,
    )
    return holding_id


def mark_sold(
    holding_id: int,
    *,
    sold_at: datetime,
    sold_proceeds_usd: Decimal,
    actor: str = "ui",
) -> None:
    with session_scope() as s:
        row = s.get(PortfolioHolding, holding_id)
        if row is None:
            raise KeyError(f"holding {holding_id} not found")
        row.status = "sold"
        row.sold_at = sold_at
        row.sold_proceeds_usd = sold_proceeds_usd
    write_audit(
        "holding_mark_sold",
        {"holding_id": holding_id, "proceeds": str(sold_proceeds_usd)},
        actor=actor,
    )


def list_holdings(status: str = "held") -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(PortfolioHolding)
        if status != "all":
            q = q.filter_by(status=status)
        return [
            {
                "holding_id": r.holding_id,
                "card_id": r.card_id,
                "cert_number": r.cert_number,
                "slab_grader": r.slab_grader,
                "slab_grade": r.slab_grade,
                "acquired_at": r.acquired_at,
                "acquired_cost_usd": r.acquired_cost_usd,
                "channel": r.channel,
                "status": r.status,
                "sold_at": r.sold_at,
                "sold_proceeds_usd": r.sold_proceeds_usd,
            }
            for r in q.all()
        ]


def current_weights() -> dict[int, float]:
    """Held cost-basis weight by card_id, normalized to sum=1.0."""
    with session_scope() as s:
        rows = (
            s.query(PortfolioHolding.card_id, func.sum(PortfolioHolding.acquired_cost_usd))
            .filter_by(status="held")
            .group_by(PortfolioHolding.card_id)
            .all()
        )
    if not rows:
        return {}
    total = sum(float(v) for _, v in rows)
    if total <= 0:
        return {}
    return {int(cid): float(v) / total for cid, v in rows}
