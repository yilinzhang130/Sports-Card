"""Pure helpers for the Parse Triage page — separated for testability."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sportscards.db.models import ParseFailure, TxClean
from sportscards.db.session import session_scope

from reports.app._components.audit import write_audit


def apply_manual_correction(
    raw_id: int,
    *,
    card_id: int,
    slab_grader: str | None,
    slab_grade: Decimal | None,
    price_usd: Decimal,
    sold_at: datetime,
    actor: str = "ui",
) -> None:
    """Create a tx_clean row from a manual operator correction; remove the failure."""
    with session_scope() as s:
        failure = s.get(ParseFailure, raw_id)
        if failure is None:
            raise KeyError(f"parse_failures row for raw_id={raw_id} not found")
        s.add(
            TxClean(
                raw_id=raw_id,
                card_id=card_id,
                slab_grader=slab_grader,
                slab_grade=slab_grade,
                price_usd=price_usd,
                sold_at=sold_at,
                parser_method="manual",
                parser_confidence=Decimal("1.0"),
            )
        )
        s.delete(failure)
    write_audit(
        "parse_manual_correction",
        {
            "raw_id": raw_id,
            "card_id": card_id,
            "grader": slab_grader,
            "grade": str(slab_grade),
        },
        actor=actor,
    )
