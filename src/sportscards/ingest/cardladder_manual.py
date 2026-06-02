"""Manual Card Ladder Sales History paste importer.

This module intentionally handles only user-pasted visible text. It does not
scrape Card Ladder, use browser cookies, or call private APIs.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from sportscards.db.models import TxClean, TxRaw
from sportscards.db.session import session_scope
from sportscards.flows.parse_pending import _resolve_card_id
from sportscards.parse.router import parse_title

SOURCE = "cardladder_manual"

PLATFORMS = (
    "PRISTINE AUCTION",
    "FANATICS WEEKLY",
    "FANATICS",
    "CARD HOBBY",
    "MY SLABS",
    "HERITAGE",
    "GOLDIN",
    "EBAY",
    "ALT",
)

LISTING_TYPES = (
    "Best Offer",
    "Auction",
    "Buy Now",
    "Fixed Price",
)

PRICE_RE = re.compile(r"\bPrice\s+\$([0-9][0-9,]*(?:\.[0-9]{2})?)\b", re.I)
DATE_RE = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b")
VERIFIED_RE = re.compile(r"\bverified\b", re.I)
CONFIRMED_PAID_RE = re.compile(r"^\(?CONFIRMED PAID\)?\s+", re.I)
WHITESPACE_RE = re.compile(r"\s+")
TITLE_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2})?\b")


@dataclass(frozen=True)
class CardLadderSale:
    platform: str
    raw_title: str
    price_usd: Decimal
    sold_at: datetime
    listing_type: str | None
    verified: bool
    raw_text: str
    warnings: tuple[str, ...] = ()
    search_query: str | None = None
    external_sale_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "raw_title": self.raw_title,
            "price_usd": str(self.price_usd),
            "sold_at": self.sold_at.isoformat(),
            "listing_type": self.listing_type,
            "verified": self.verified,
            "raw_text": self.raw_text,
            "warnings": list(self.warnings),
            "search_query": self.search_query,
            "external_sale_id": self.external_sale_id,
        }

    def with_metadata(
        self,
        *,
        search_query: str | None = None,
        external_sale_id: str | None = None,
    ) -> CardLadderSale:
        clean_query = search_query.strip() if search_query and search_query.strip() else None
        clean_sale_id = (
            external_sale_id.strip() if external_sale_id and external_sale_id.strip() else None
        )
        return replace(self, search_query=clean_query, external_sale_id=clean_sale_id)

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> CardLadderSale:
        sold_at = row["sold_at"]
        if isinstance(sold_at, datetime):
            sold_at_dt = sold_at
        else:
            sold_at_dt = datetime.fromisoformat(str(sold_at))
        if sold_at_dt.tzinfo is None:
            sold_at_dt = sold_at_dt.replace(tzinfo=UTC)
        return cls(
            platform=str(row["platform"]).upper(),
            raw_title=str(row["raw_title"]),
            price_usd=Decimal(str(row["price_usd"])),
            sold_at=sold_at_dt,
            listing_type=str(row["listing_type"]) if row.get("listing_type") else None,
            verified=bool(row.get("verified", False)),
            raw_text=str(row["raw_text"]),
            warnings=tuple(str(w) for w in row.get("warnings", ())),
            search_query=str(row["search_query"]) if row.get("search_query") else None,
            external_sale_id=str(row["external_sale_id"]) if row.get("external_sale_id") else None,
        )


@dataclass(frozen=True)
class ImportResult:
    inserted_raw: int
    inserted_clean: int
    skipped_duplicates: int
    failed_clean: int
    errors: tuple[str, ...] = ()


def _normalize_text(text: str) -> str:
    return WHITESPACE_RE.sub(" ", text).strip()


def _starts_with_platform(line: str) -> bool:
    upper = line.upper()
    return any(upper.startswith(platform) for platform in PLATFORMS)


def _extract_platform(row_text: str) -> str | None:
    upper = row_text.upper()
    for platform in PLATFORMS:
        if upper.startswith(platform):
            return platform
    return None


def _extract_listing_type(row_text: str, price_end: int, date_start: int) -> str | None:
    suffix = VERIFIED_RE.sub(" ", row_text[price_end:date_start])
    suffix_lower = _normalize_text(suffix).casefold()
    for listing_type in LISTING_TYPES:
        if listing_type.casefold() in suffix_lower:
            return listing_type

    prefix_upper = row_text[:price_end].upper()
    for listing_type in LISTING_TYPES:
        if listing_type.upper() in prefix_upper:
            return listing_type
    if "buy now" in suffix_lower:
        return "Buy Now"
    return None


def _strip_leading_sale_words(title: str) -> str:
    cleaned = title.strip()
    changed = True
    while changed:
        changed = False
        for token in ("BUY NOW", "AUCTION", "FIXED PRICE", "BEST OFFER"):
            if cleaned.upper().startswith(token):
                cleaned = cleaned[len(token) :].strip()
                changed = True
    return cleaned


def _strip_seller_prefix(title: str, platform: str) -> str:
    cleaned = title.strip()
    if platform != "EBAY" or not cleaned.startswith("-"):
        return cleaned

    year_match = TITLE_YEAR_RE.search(cleaned)
    if year_match is None:
        return cleaned.lstrip("- ").strip()
    return cleaned[year_match.start() :].strip()


def _strip_status_prefix(title: str) -> str:
    return CONFIRMED_PAID_RE.sub("", title.strip()).strip()


def _extract_title(row_text: str, platform: str, price_start: int) -> str:
    title = row_text[:price_start].strip()
    if title.upper().startswith(platform):
        title = title[len(platform) :].strip()
    title = _strip_status_prefix(title)
    title = _strip_seller_prefix(title, platform)
    return _strip_leading_sale_words(title)


def _parse_row(row_text: str) -> CardLadderSale | None:
    normalized = _normalize_text(row_text)
    if not normalized:
        return None

    platform = _extract_platform(normalized)
    price_match = PRICE_RE.search(normalized)
    date_matches = list(DATE_RE.finditer(normalized))
    if platform is None or price_match is None or not date_matches:
        return None

    date_match = date_matches[-1]
    price = Decimal(price_match.group(1).replace(",", ""))
    sold_at = datetime.strptime(date_match.group(0), "%b %d, %Y").replace(tzinfo=UTC)
    listing_type = _extract_listing_type(normalized, price_match.end(), date_match.start())
    raw_title = _extract_title(normalized, platform, price_match.start())
    confirmed_paid = "CONFIRMED PAID" in normalized.upper()
    warnings: list[str] = []
    if not raw_title:
        warnings.append("missing_title")
    if listing_type is None:
        warnings.append("missing_listing_type")

    return CardLadderSale(
        platform=platform,
        raw_title=raw_title,
        price_usd=price,
        sold_at=sold_at,
        listing_type=listing_type,
        verified=bool(VERIFIED_RE.search(normalized) or confirmed_paid),
        raw_text=normalized,
        warnings=tuple(warnings),
    )


def parse_cardladder_text(text: str) -> list[CardLadderSale]:
    lines = [_normalize_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    chunks: list[str] = []
    current: list[str] = []

    for line in lines:
        current_text = " ".join(current)
        line_starts_platform = _starts_with_platform(line)
        if current and line_starts_platform and _extract_platform(current_text) is None:
            current = [line]
            continue
        should_start_new = (
            bool(current)
            and line_starts_platform
            and PRICE_RE.search(current_text) is not None
            and DATE_RE.search(current_text) is not None
        )
        if should_start_new:
            chunks.append(" ".join(current))
            current = [line]
        else:
            current.append(line)

    if current:
        chunks.append(" ".join(current))

    rows: list[CardLadderSale] = []
    for chunk in chunks:
        parsed = _parse_row(chunk)
        if parsed is not None:
            rows.append(parsed)
    return rows


def _coerce_sold_date(value: str | date | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, datetime.min.time())
    else:
        dt = datetime.combine(date.fromisoformat(value), datetime.min.time())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def build_quick_sale(
    *,
    platform: str,
    raw_title: str,
    price_usd: Decimal,
    sold_date: str | date | datetime,
    listing_type: str | None,
    verified: bool,
) -> CardLadderSale:
    normalized_platform = platform.strip().upper()
    if normalized_platform not in PLATFORMS:
        raise ValueError(f"unknown Card Ladder platform {platform!r}")
    sold_at = _coerce_sold_date(sold_date)
    price = Decimal(str(price_usd)).quantize(Decimal("0.01"))
    price_text = f"{price:,.2f}"
    pieces = [
        normalized_platform,
        raw_title.strip(),
        f"Price ${price_text}",
    ]
    if verified:
        pieces.append("verified")
    if listing_type:
        pieces.append(listing_type)
    pieces.append(sold_at.strftime("%b %-d, %Y"))
    raw_text = " ".join(pieces)
    return CardLadderSale(
        platform=normalized_platform,
        raw_title=raw_title.strip(),
        price_usd=price,
        sold_at=sold_at,
        listing_type=listing_type,
        verified=verified,
        raw_text=raw_text,
    )


def stable_external_id(sale: CardLadderSale) -> str:
    if sale.external_sale_id:
        return sale.external_sale_id
    payload = "|".join(
        [
            sale.platform.upper(),
            sale.raw_title.casefold(),
            sale.sold_at.date().isoformat(),
            str(sale.price_usd.quantize(Decimal("0.01"))),
            (sale.listing_type or "").casefold(),
            sale.raw_text.casefold(),
        ]
    )
    return "clm-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def import_cardladder_sales(
    sales: Sequence[CardLadderSale],
    *,
    allow_clean: bool = True,
) -> ImportResult:
    inserted_raw = 0
    inserted_clean = 0
    skipped_duplicates = 0
    failed_clean = 0
    errors: list[str] = []

    with session_scope() as s:
        for sale in sales:
            external_id = stable_external_id(sale)
            existing = s.execute(
                select(TxRaw.raw_id).where(
                    TxRaw.source == SOURCE,
                    TxRaw.external_id == external_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                skipped_duplicates += 1
                continue

            raw = TxRaw(
                source=SOURCE,
                raw_title=sale.raw_title,
                raw_price=sale.price_usd,
                raw_currency="USD",
                sold_at=sale.sold_at,
                external_id=external_id,
                raw_json={
                    "platform": sale.platform,
                    "listing_type": sale.listing_type,
                    "verified": sale.verified,
                    "raw_text": sale.raw_text,
                    "warnings": list(sale.warnings),
                    "search_query": sale.search_query,
                    "external_sale_id": sale.external_sale_id,
                },
            )
            s.add(raw)
            s.flush()
            inserted_raw += 1

            if not allow_clean:
                continue

            parsed = parse_title(sale.raw_title, allow_llm=False)
            if parsed.confidence < Decimal("0.5"):
                failed_clean += 1
                continue

            try:
                clean = TxClean(
                    raw_id=raw.raw_id,
                    card_id=_resolve_card_id(s, parsed),
                    slab_grader=parsed.slab_grader,
                    slab_grade=parsed.slab_grade,
                    cert_number=parsed.cert_number,
                    price_usd=sale.price_usd,
                    sold_at=sale.sold_at,
                    parser_confidence=parsed.confidence,
                    parser_method=f"cardladder_{parsed.method}"[:16],
                )
                s.add(clean)
                inserted_clean += 1
            except Exception as exc:  # pragma: no cover - defensive DB boundary
                failed_clean += 1
                errors.append(f"{external_id}: {exc}")

    return ImportResult(
        inserted_raw=inserted_raw,
        inserted_clean=inserted_clean,
        skipped_duplicates=skipped_duplicates,
        failed_clean=failed_clean,
        errors=tuple(errors),
    )
