"""Manual CSV import for high-end auction houses without public APIs.

Supports Goldin, Heritage, Fanatics Collect post-auction result exports.
All three publish per-lot results on their site; users copy/paste or
scrape responsibly into a flat CSV, then import here.

Expected CSV columns (case-insensitive):
    house, lot_title, sold_at, price, currency (optional), cert_number (optional)
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from sportscards.db.models import TxRaw
from sportscards.db.session import session_scope

log = logging.getLogger(__name__)

SUPPORTED_HOUSES = {"goldin", "heritage", "fanatics_collect"}


def import_auction_csv(path: str | Path, house: str) -> int:
    if house not in SUPPORTED_HOUSES:
        raise ValueError(f"unknown house {house!r}; expected one of {SUPPORTED_HOUSES}")
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]

    added = 0
    with session_scope() as s:
        for _, row in df.iterrows():
            sold_at = pd.to_datetime(row["sold_at"], utc=True).to_pydatetime()
            price = Decimal(str(row["price"]))
            external_id = f"{house}:{row.get('lot_id', f'{sold_at.isoformat()}:{price}')}"
            stmt = pg_insert(TxRaw).values(
                source=house,
                raw_title=str(row.get("lot_title", "")),
                raw_price=price,
                raw_currency=str(row.get("currency", "USD")),
                sold_at=sold_at,
                external_id=external_id,
                raw_json=row.dropna().to_dict(),
            ).on_conflict_do_nothing(constraint="uq_tx_raw_source_extid")
            result = s.execute(stmt)
            added += result.rowcount or 0
    log.info("auction import (%s): %d new rows", house, added)
    return added
