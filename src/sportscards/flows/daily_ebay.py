"""Daily eBay ingestion flow. Runs at 03:00 local."""

from __future__ import annotations

from prefect import flow, get_run_logger, task

from sportscards.ingest.ebay_browse import ingest_sold

# Query terms targeting the NBA card universe we care about.
KEYWORDS: list[str | None] = [
    None,  # category 214 only, broad sweep
    "Panini Prizm Basketball PSA",
    "Panini Select Basketball PSA",
    "Panini National Treasures Basketball",
    "Topps Chrome Basketball PSA",
    "Donruss Optic Basketball PSA",
]


@task(retries=2, retry_delay_seconds=60)
def ingest_query(kw: str | None) -> int:
    return ingest_sold(keywords=kw, page_size=200, max_pages=10)


@flow(name="daily-ebay-ingest")
def daily_ebay_flow() -> int:
    log = get_run_logger()
    total = 0
    for kw in KEYWORDS:
        n = ingest_query.submit(kw).result()
        log.info("kw=%r added=%d", kw, n)
        total += n
    log.info("total new rows: %d", total)
    return total


if __name__ == "__main__":
    daily_ebay_flow()
