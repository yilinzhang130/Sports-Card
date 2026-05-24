"""Orchestration: load tx_clean → run repeat_sales → persist into repeat_sales_index.

Also exposes a synthetic-data seeder so the full pipeline can be exercised
end-to-end before real eBay data is flowing.
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sportscards.db.models import Card, RepeatSalesIndex, TxClean, TxRaw
from sportscards.db.session import session_scope
from sportscards.factors.repeat_sales import estimate_index
from sportscards.factors.synthetic_data import generate_synthetic_tx

ERA_BOUNDARY = 2010


def _load_tx(sport: str, era: str) -> pd.DataFrame:
    """Pull tx_clean joined with card_master, filtered to the (sport, era) slice.

    Only rows with a non-null cert_number are usable for the repeat-sales fit.
    """
    # sport is reserved for when card_master gains a sport column. For NBA-only,
    # all rows match; for other sports the join would need that column.
    _ = sport
    with session_scope() as s:
        stmt = (
            select(
                TxClean.cert_number,
                TxClean.sold_at,
                TxClean.price_usd,
                TxClean.slab_grader,
                TxClean.slab_grade,
                Card.year,
            )
            .join(Card, TxClean.card_id == Card.card_id, isouter=False)
            .where(TxClean.cert_number.is_not(None))
        )
        if era == "modern":
            stmt = stmt.where(Card.year >= ERA_BOUNDARY)
        elif era == "vintage":
            stmt = stmt.where(Card.year < ERA_BOUNDARY)
        rows = s.execute(stmt).all()

    if not rows:
        return pd.DataFrame(
            columns=["cert_number", "sold_at", "price_usd",
                     "slab_grader", "slab_grade", "year"]
        )
    return pd.DataFrame(
        [
            {
                "cert_number": r.cert_number,
                "sold_at": r.sold_at,
                "price_usd": float(r.price_usd),
                "slab_grader": r.slab_grader,
                "slab_grade": float(r.slab_grade) if r.slab_grade is not None else None,
                "year": r.year,
            }
            for r in rows
        ]
    )


def _upsert(rows: list[dict], replace_key: dict | None = None) -> int:
    if not rows and not replace_key:
        return 0
    with session_scope() as s:
        if replace_key is not None:
            conds = [getattr(RepeatSalesIndex, k) == v for k, v in replace_key.items()]
            s.execute(delete(RepeatSalesIndex).where(and_(*conds)))
        if not rows:
            return 0
        stmt = pg_insert(RepeatSalesIndex).values(rows)
        update_cols = {
            c.name: stmt.excluded[c.name]
            for c in RepeatSalesIndex.__table__.columns
            if not c.primary_key
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                "period_start", "sport", "bucket", "grade_tier", "era",
            ],
            set_=update_cols,
        )
        s.execute(stmt)
    return len(rows)


def build_and_persist(
    sport: str = "NBA",
    bucket: str = "weekly",
    grade_tiers: list[str] | None = None,
    eras: list[str] | None = None,
    replace: bool = False,
) -> dict[str, int]:
    """Fit the repeat-sales regression for every (era, grade_tier) partition
    and persist results into repeat_sales_index. Returns per-partition row counts.
    """
    grade_tiers = grade_tiers or ["PSA10", "PSA9", "PSA8", "lower"]
    eras = eras or ["modern", "vintage"]
    stats: dict[str, int] = {}

    for era in eras:
        tx = _load_tx(sport, era)
        if tx.empty:
            for tier in grade_tiers:
                stats[f"{sport}/{era}/{tier}/{bucket}"] = 0
            continue
        for tier in grade_tiers:
            idx = estimate_index(tx, bucket=bucket, grade_tier=tier)
            rows = [
                {
                    "period_start": row.period_start.to_pydatetime(),
                    "sport": sport,
                    "bucket": bucket,
                    "grade_tier": tier,
                    "era": era,
                    "index_value": (
                        None if pd.isna(row.index_value)
                        else Decimal(f"{row.index_value:.4f}")
                    ),
                    "n_pairs": int(row.n_pairs),
                    "se": (
                        None if pd.isna(row.se) else Decimal(f"{row.se:.5f}")
                    ),
                }
                for row in idx.itertuples(index=False)
            ]
            replace_key = (
                {"sport": sport, "bucket": bucket, "grade_tier": tier, "era": era}
                if replace else None
            )
            n = _upsert(rows, replace_key=replace_key)
            stats[f"{sport}/{era}/{tier}/{bucket}"] = n

    return stats


def seed_synthetic_tx(
    n_certs: int = 2000,
    weeks: int = 300,
    seed: int = 42,
    card_id: int = 1,
) -> int:
    """Insert synthetic tx_raw + tx_clean rows. For local dev / E2E only."""
    tx, _ = generate_synthetic_tx(n_certs=n_certs, weeks=weeks, seed=seed)
    inserted = 0
    with session_scope() as s:
        # One tx_raw per synthetic transaction (cert+sold_at uniquely identifies it).
        for i, row in enumerate(tx.itertuples(index=False)):
            external_id = f"synth-{seed}-{i}"
            raw = TxRaw(
                source="synthetic",
                raw_title=f"synthetic cert={row.cert_number}",
                raw_price=Decimal(f"{row.price_usd:.2f}"),
                raw_currency="USD",
                sold_at=row.sold_at.to_pydatetime(),
                external_id=external_id,
                raw_json={"cert_number": row.cert_number},
            )
            s.add(raw)
            s.flush()
            clean = TxClean(
                raw_id=raw.raw_id,
                card_id=card_id,
                slab_grader=row.slab_grader,
                slab_grade=Decimal(f"{row.slab_grade:.1f}"),
                cert_number=row.cert_number,
                price_usd=Decimal(f"{row.price_usd:.2f}"),
                sold_at=row.sold_at.to_pydatetime(),
                parser_confidence=Decimal("1.000"),
                parser_method="synthetic",
            )
            s.add(clean)
            inserted += 1
    return inserted
