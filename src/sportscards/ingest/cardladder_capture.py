"""Helpers for Card Ladder browser-accessibility capture.

These helpers operate on visible link descriptions collected from the browser
accessibility tree. They do not call Card Ladder private APIs.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sportscards.ingest.cardladder_manual import CardLadderSale, parse_cardladder_text


def _sale_id_from_url(value: str) -> str | None:
    sale_id = parse_qs(urlparse(value).query).get("saleId", [""])[0].strip()
    return sale_id or None


def _visible_sale_links(links: list[dict[str, str]]) -> list[tuple[str, str | None]]:
    rows: list[tuple[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()
    for link in links:
        description = str(link.get("description", "")).strip()
        value = str(link.get("value", "")).strip()
        if " Price $" not in description:
            continue
        row = (description, _sale_id_from_url(value))
        if row in seen:
            continue
        seen.add(row)
        rows.append(row)
    return rows


def capture_links_to_text(links: list[dict[str, str]]) -> tuple[str, list[str]]:
    rows = _visible_sale_links(links)
    descriptions = [description for description, _sale_id in rows]
    sale_ids = [sale_id for _description, sale_id in rows if sale_id]
    return "\n".join(descriptions), sale_ids


def capture_links_to_sales(
    links: list[dict[str, str]],
    *,
    search_query: str | None = None,
) -> list[CardLadderSale]:
    sales: list[CardLadderSale] = []
    for description, sale_id in _visible_sale_links(links):
        parsed = parse_cardladder_text(description)
        if not parsed:
            continue
        sales.append(parsed[0].with_metadata(search_query=search_query, external_sale_id=sale_id))
    return sales
