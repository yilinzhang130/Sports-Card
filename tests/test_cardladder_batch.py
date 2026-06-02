from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sportscards.db.models import TxRaw
from sportscards.ingest.cardladder_batch import (
    captured_links_to_import_summary,
    next_capture_plan,
    search_url,
)


def _raw(query: str, external_id: str) -> TxRaw:
    return TxRaw(
        source="cardladder_manual",
        raw_title=query,
        raw_price=Decimal("100.00"),
        raw_currency="USD",
        sold_at=datetime(2026, 6, 1, tzinfo=UTC),
        external_id=external_id,
        raw_json={"search_query": query},
    )


def test_search_url_encodes_query_for_card_ladder_sales_history():
    url = search_url("Shai Gilgeous-Alexander Prizm PSA 10")

    assert url == (
        "https://app.cardladder.com/sales-history"
        "?sort=date&direction=desc&q=Shai%20Gilgeous-Alexander%20Prizm%20PSA%2010"
    )


def test_next_capture_plan_includes_url_and_coverage(migrated_db):
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        session.add(_raw("Michael Jordan Fleer PSA 10", "mj-1"))
        session.commit()

    plan = next_capture_plan(limit=2, engine=engine)

    assert len(plan) == 2
    assert plan[0]["query"] == "Giannis Antetokounmpo Prizm PSA 10"
    assert plan[0]["current_rows"] == 0
    assert plan[0]["target_rows"] == 100
    assert plan[0]["url"].startswith("https://app.cardladder.com/sales-history?")
    assert plan[1]["query"] == "Kevin Durant Topps Chrome PSA 10"


def test_captured_links_to_import_summary_imports_visible_sales(migrated_db):
    engine = create_engine(migrated_db)
    links = [
        {
            "description": (
                "EBAY - SELLER 2018-19 Panini Prizm Luka Doncic #280 PSA 10 "
                "Price $4,000.00 Auction Jun 1, 2026"
            ),
            "value": "ebay.com/itm/12345",
        },
        {
            "description": "launch",
            "value": "cardladder.zendesk.com",
        },
    ]

    summary = captured_links_to_import_summary(
        "Luka Doncic Prizm PSA 10",
        links,
        engine=engine,
    )

    assert summary == {
        "query": "Luka Doncic Prizm PSA 10",
        "captured": 1,
        "missing_external_ids": 0,
        "inserted_raw": 1,
        "inserted_clean": 1,
        "skipped_duplicates": 0,
        "failed_clean": 0,
        "errors": [],
    }
