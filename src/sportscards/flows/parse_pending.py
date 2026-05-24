"""Parse unprocessed tx_raw rows into tx_clean (with LLM fallback)."""

from __future__ import annotations

from decimal import Decimal

from prefect import flow, get_run_logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from sportscards.db.models import Card, ParseFailure, Player, TxClean, TxRaw
from sportscards.db.session import session_scope
from sportscards.parse.router import parse_title
from sportscards.parse.schema import ParsedTitle


def _resolve_card_id(s: Session, parsed: ParsedTitle) -> int | None:
    """Best-effort lookup of card_id from parsed fields."""
    if not all([parsed.year, parsed.manufacturer, parsed.set_name, parsed.card_number]):
        return None
    q = select(Card.card_id).where(
        Card.year == parsed.year,
        Card.manufacturer == parsed.manufacturer,
        Card.set_name == parsed.set_name,
        Card.card_number == parsed.card_number,
        Card.parallel == parsed.parallel,
    )
    if parsed.player_name:
        q = q.join(Player).where(Player.name == parsed.player_name)
    result: int | None = s.execute(q).scalar_one_or_none()
    return result


@flow(name="parse-pending")
def parse_pending_flow(batch_size: int = 1000, allow_llm: bool = True) -> dict[str, int]:
    log = get_run_logger()
    stats = {"parsed": 0, "failed": 0}

    with session_scope() as s:
        # Find raw rows that have no tx_clean and no parse_failures entry.
        sub_clean = select(TxClean.raw_id)
        sub_fail = select(ParseFailure.raw_id)
        q = (
            select(TxRaw)
            .where(TxRaw.source == "ebay")
            .where(TxRaw.raw_id.notin_(sub_clean))
            .where(TxRaw.raw_id.notin_(sub_fail))
            .limit(batch_size)
        )
        rows = s.execute(q).scalars().all()

        for raw in rows:
            parsed = parse_title(raw.raw_title, allow_llm=allow_llm)
            if parsed.confidence < Decimal("0.5") or raw.raw_price is None:
                s.execute(
                    pg_insert(ParseFailure)
                    .values(raw_id=raw.raw_id, reason=f"low confidence {parsed.confidence}")
                    .on_conflict_do_nothing()
                )
                stats["failed"] += 1
                continue

            card_id = _resolve_card_id(s, parsed)
            s.execute(
                pg_insert(TxClean)
                .values(
                    raw_id=raw.raw_id,
                    card_id=card_id,
                    slab_grader=parsed.slab_grader,
                    slab_grade=parsed.slab_grade,
                    cert_number=parsed.cert_number,
                    price_usd=raw.raw_price,
                    sold_at=raw.sold_at,
                    parser_confidence=parsed.confidence,
                    parser_method=parsed.method,
                )
                .on_conflict_do_nothing(index_elements=["raw_id"])
            )
            stats["parsed"] += 1

    log.info("parse_pending stats: %s", stats)
    return stats


if __name__ == "__main__":
    parse_pending_flow()
