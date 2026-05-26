"""eBay Browse API ingestor.

The Finding API was decommissioned 2025-02-05. Browse API replaces it but
**only returns active listings** — the `soldItems` filter is invalid Browse
syntax. For sold/realized prices we'd need the limited-release
**Marketplace Insights API**, which requires application + approval.

Until that approval lands, this ingestor pulls active (currently-listed)
items. Prices represent ask, not transaction — useful for ask trend, hyped
flags, and supply count, but **not** as comp prices in the hedonic model.

Category 214 = Basketball Trading Cards.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential

from sportscards.config import Settings, get_settings
from sportscards.db.models import TxRaw
from sportscards.db.session import session_scope

log = logging.getLogger(__name__)

NBA_CATEGORY_ID = "214"


class EbayBrowseClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._token: str | None = None
        self._token_exp: datetime = datetime.min.replace(tzinfo=UTC)
        self._http = httpx.Client(timeout=30.0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=30),
        retry=retry_if_not_exception_type(RuntimeError),
    )
    def _refresh_token(self) -> None:
        s = self.settings
        if not s.ebay_client_id or not s.ebay_client_secret:
            raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not configured")
        creds = base64.b64encode(f"{s.ebay_client_id}:{s.ebay_client_secret}".encode()).decode()
        resp = self._http.post(
            s.ebay_oauth_url,
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "https://api.ebay.com/oauth/api_scope",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._token_exp = datetime.now(UTC) + timedelta(seconds=body["expires_in"] - 60)
        log.info("eBay OAuth token refreshed, expires %s", self._token_exp)

    def _auth_header(self) -> dict[str, str]:
        if self._token is None or datetime.now(UTC) >= self._token_exp:
            self._refresh_token()
        # X-EBAY-C-MARKETPLACE-ID is REQUIRED by Browse API for non-default
        # marketplaces; without it some calls return 400.
        return {
            "Authorization": f"Bearer {self._token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=30),
        retry=retry_if_not_exception_type(RuntimeError),
    )
    def search(
        self,
        *,
        category_id: str = NBA_CATEGORY_ID,
        keywords: str | None = None,
        sold: bool = False,  # Browse API does not support sold filter; ignored
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        del sold  # Browse API returns active listings only; param kept for API compat
        params: dict[str, Any] = {
            "category_ids": category_id,
            "limit": limit,
            "offset": offset,
            "filter": "buyingOptions:{AUCTION|FIXED_PRICE}",
        }
        if keywords:
            params["q"] = keywords

        resp = self._http.get(
            f"{self.settings.ebay_api_base}/buy/browse/v1/item_summary/search",
            headers=self._auth_header(),
            params=params,
        )
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
        return payload

    def iter_sold(
        self,
        *,
        category_id: str = NBA_CATEGORY_ID,
        keywords: str | None = None,
        page_size: int = 200,
        max_pages: int = 50,
    ) -> Iterator[dict[str, Any]]:
        offset = 0
        for _ in range(max_pages):
            page = self.search(
                category_id=category_id,
                keywords=keywords,
                sold=True,
                limit=page_size,
                offset=offset,
            )
            items = page.get("itemSummaries", []) or []
            if not items:
                return
            yield from items
            if offset + page_size >= int(page.get("total", 0)):
                return
            offset += page_size


DEFAULT_KEYWORD_BASKET = [
    "panini prizm basketball",
    "panini select basketball",
    "panini mosaic basketball",
    "panini national treasures basketball",
    "panini donruss optic basketball",
    "topps chrome basketball",
    "panini immaculate basketball",
    "panini flawless basketball",
]


def ingest_sold(
    *,
    keywords: str | None = None,
    page_size: int = 200,
    max_pages: int = 50,
) -> int:
    """Pull active NBA listings and upsert into tx_raw. Returns rows added.

    Browse API requires a non-empty `q` keyword; we either honor the caller's
    explicit `keywords` or sweep a canonical basket of NBA set names.
    """
    if keywords is None:
        added = 0
        for kw in DEFAULT_KEYWORD_BASKET:
            added += ingest_sold(keywords=kw, page_size=page_size, max_pages=max_pages)
        return added
    client = EbayBrowseClient()
    added = 0
    with session_scope() as s:
        for item in client.iter_sold(keywords=keywords, page_size=page_size, max_pages=max_pages):
            external_id = item.get("itemId") or item.get("legacyItemId")
            if not external_id:
                continue
            price = item.get("price") or {}
            try:
                raw_price = Decimal(str(price.get("value"))) if price.get("value") else None
            except Exception:
                raw_price = None
            # Active listings: no sold_at. Use itemCreationDate as a proxy for
            # when we observed the listing. Hedonic model must filter
            # source='ebay_active' before treating these as comps.
            observed_at_str = item.get("itemCreationDate")
            observed_at = (
                datetime.fromisoformat(observed_at_str.replace("Z", "+00:00"))
                if observed_at_str
                else None
            )

            stmt = (
                pg_insert(TxRaw)
                .values(
                    source="ebay_active",
                    raw_title=item.get("title", ""),
                    raw_price=raw_price,
                    raw_currency=price.get("currency", "USD"),
                    sold_at=observed_at,
                    external_id=str(external_id),
                    raw_json=item,
                )
                .on_conflict_do_nothing(constraint="uq_tx_raw_source_extid")
            )
            result = s.execute(stmt)
            # psycopg returns -1 for INSERT ... ON CONFLICT DO NOTHING when the
            # row was skipped; clamp at 0 so the counter is honest.
            added += max(result.rowcount or 0, 0)  # type: ignore[attr-defined]
    log.info("ingested %d new active eBay rows (kw=%r)", added, keywords)
    return added
