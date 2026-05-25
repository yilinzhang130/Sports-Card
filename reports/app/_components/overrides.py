"""portfolio_overrides read/write helpers."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sportscards.db.models import PortfolioOverride
from sportscards.db.session import session_scope

from reports.app._components.audit import write_audit


def set_override(card_id: int, weight: Decimal, *, reason: str, actor: str = "ui") -> None:
    with session_scope() as s:
        existing = s.get(PortfolioOverride, card_id)
        if existing:
            existing.target_weight_pct = weight
            existing.reason = reason
            existing.set_by = actor
        else:
            s.add(
                PortfolioOverride(
                    card_id=card_id,
                    target_weight_pct=weight,
                    reason=reason,
                    set_by=actor,
                )
            )
    write_audit(
        "portfolio_override_set",
        {"card_id": card_id, "weight": str(weight), "reason": reason},
        actor=actor,
    )


def clear_override(card_id: int, *, actor: str = "ui") -> None:
    with session_scope() as s:
        row = s.get(PortfolioOverride, card_id)
        if row is not None:
            s.delete(row)
    write_audit("portfolio_override_clear", {"card_id": card_id}, actor=actor)


def list_overrides() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [
            {
                "card_id": r.card_id,
                "target_weight_pct": r.target_weight_pct,
                "reason": r.reason,
                "set_by": r.set_by,
                "set_at": r.set_at,
            }
            for r in s.query(PortfolioOverride).all()
        ]
