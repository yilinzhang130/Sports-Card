"""Card Ladder importer.

Day-1: Pro tier ($15/mo) only exposes the web app. We import CSV exports
manually via the `sportscards cardladder import` CLI.

The CSV schema is whatever Card Ladder Pro exports; we accept the common
columns and ignore the rest. Required: spec_id, sold_at, price, grade.

A skeleton `CardLadderApiClient` is included for later enterprise-API hookup.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sportscards.db.models import CardLadderSpecMap, TxClean, TxRaw
from sportscards.db.session import session_scope

log = logging.getLogger(__name__)


COLUMN_ALIASES: dict[str, str] = {
    "Spec ID": "spec_id",
    "Card Ladder ID": "spec_id",
    "Sold Date": "sold_at",
    "Sale Date": "sold_at",
    "Price": "price",
    "Sale Price": "price",
    "Grade": "grade",
    "Grading Company": "grader",
    "Grader": "grader",
    "Cert Number": "cert_number",
    "Cert #": "cert_number",
    "Title": "title",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {c: COLUMN_ALIASES[c] for c in df.columns if c in COLUMN_ALIASES}
    df = df.rename(columns=rename)
    required = {"spec_id", "sold_at", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns after normalization: {missing}")
    return df


def import_sales_csv(path: str | Path) -> tuple[int, int]:
    """Import a Card Ladder sales CSV. Returns (raw_added, clean_added)."""
    df = pd.read_csv(path)
    df = _normalize_columns(df)
    raw_added = 0
    clean_added = 0

    with session_scope() as s:
        # Build spec_id -> card_id map upfront
        spec_rows = s.execute(select(CardLadderSpecMap.cl_spec_id, CardLadderSpecMap.card_id)).all()
        spec_to_card: dict[str, int] = {row[0]: row[1] for row in spec_rows}

        for _, row in df.iterrows():
            spec_id = str(row["spec_id"])
            external_id = f"cl:{spec_id}:{row['sold_at']}:{row['price']}"
            sold_at = pd.to_datetime(row["sold_at"], utc=True).to_pydatetime()
            price = Decimal(str(row["price"]))

            raw_stmt = (
                pg_insert(TxRaw)
                .values(
                    source="cardladder",
                    raw_title=str(row.get("title", "")),
                    raw_price=price,
                    raw_currency="USD",
                    sold_at=sold_at,
                    external_id=external_id,
                    raw_json=row.dropna().to_dict(),
                )
                .on_conflict_do_nothing(constraint="uq_tx_raw_source_extid")
                .returning(TxRaw.raw_id)
            )
            result = s.execute(raw_stmt).scalar_one_or_none()
            if result is None:
                continue
            raw_added += 1

            card_id = spec_to_card.get(spec_id)
            grade_val = row.get("grade")
            grade = Decimal(str(grade_val)) if pd.notna(grade_val) else None
            clean_stmt = (
                pg_insert(TxClean)
                .values(
                    raw_id=result,
                    card_id=card_id,
                    slab_grader=str(row.get("grader", "PSA"))
                    if pd.notna(row.get("grader"))
                    else None,
                    slab_grade=grade,
                    cert_number=str(row.get("cert_number"))
                    if pd.notna(row.get("cert_number"))
                    else None,
                    price_usd=price,
                    sold_at=sold_at,
                    parser_confidence=Decimal("1.000"),
                    parser_method="cardladder",
                )
                .on_conflict_do_nothing(index_elements=["raw_id"])
            )
            s.execute(clean_stmt)
            clean_added += 1
    log.info("Card Ladder import: %d raw, %d clean from %s", raw_added, clean_added, path)
    return raw_added, clean_added


class CardLadderApiClient:
    """Skeleton for future enterprise API access.

    Card Ladder's enterprise API is by-request. When credentials are issued,
    fill in the BASE_URL and auth header, then implement these methods.
    """

    BASE_URL = "https://api.cardladder.com/v1"  # placeholder

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        raise NotImplementedError("Enterprise API access not yet provisioned")

    def fetch_sales(self, spec_id: str, since: datetime) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_pop(self, spec_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError
