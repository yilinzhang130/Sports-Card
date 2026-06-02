"""Batch helpers for agent-operated Card Ladder capture."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from sqlalchemy.engine import Engine

from sportscards.ingest.cardladder_capture import capture_links_to_sales
from sportscards.ingest.cardladder_manual import import_cardladder_sales, parse_cardladder_text
from sportscards.ingest.cardladder_queue import next_searches
from sportscards.reports import queries


def search_url(query: str) -> str:
    encoded = quote(query, safe="")
    return f"https://app.cardladder.com/sales-history?sort=date&direction=desc&q={encoded}"


def next_capture_plan(limit: int = 10, engine: Engine | None = None) -> list[dict[str, Any]]:
    coverage = queries.cardladder_coverage_summary(engine=engine)
    coverage_by_query: dict[str, int] = {}
    if not coverage.empty:
        for search_query, rows in zip(
            coverage["search_query"].astype(str).tolist(),
            coverage["rows"].astype(int).tolist(),
            strict=False,
        ):
            coverage_by_query[search_query] = rows
    plan: list[dict[str, Any]] = []
    for row in next_searches(coverage_by_query, limit=limit):
        current_rows = coverage_by_query.get(row.query, 0)
        plan.append(
            {
                "tier": row.tier,
                "query": row.query,
                "current_rows": current_rows,
                "target_rows": row.target_rows,
                "remaining_rows": max(row.target_rows - current_rows, 0),
                "cadence": row.cadence,
                "url": search_url(row.query),
            }
        )
    return plan


def captured_links_to_import_summary(
    query: str,
    links: list[dict[str, str]],
    *,
    engine: Engine | None = None,
) -> dict[str, Any]:
    sales = capture_links_to_sales(links, search_query=query)
    result = import_cardladder_sales(sales, engine=engine)
    return {
        "query": query,
        "captured": len(sales),
        "missing_external_ids": sum(1 for sale in sales if not sale.external_sale_id),
        "inserted_raw": result.inserted_raw,
        "inserted_clean": result.inserted_clean,
        "skipped_duplicates": result.skipped_duplicates,
        "failed_clean": result.failed_clean,
        "errors": list(result.errors),
    }


def captured_text_to_import_summary(
    query: str,
    text: str,
    *,
    engine: Engine | None = None,
) -> dict[str, Any]:
    sales = [sale.with_metadata(search_query=query) for sale in parse_cardladder_text(text)]
    result = import_cardladder_sales(sales, engine=engine)
    return {
        "query": query,
        "captured": len(sales),
        "missing_external_ids": sum(1 for sale in sales if not sale.external_sale_id),
        "inserted_raw": result.inserted_raw,
        "inserted_clean": result.inserted_clean,
        "skipped_duplicates": result.skipped_duplicates,
        "failed_clean": result.failed_clean,
        "errors": list(result.errors),
    }
