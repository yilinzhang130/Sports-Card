from __future__ import annotations

from decimal import Decimal

from sportscards.ingest.cardladder_manual import (
    build_quick_sale,
    import_cardladder_sales,
    parse_cardladder_text,
    stable_external_id,
)

SAMPLES = [
    (
        "FANATICS BUY NOW 2025 Topps Now Tyrese Maxey VJ Edgecombe ROOKIE #32 "
        "PSA 10 GEM MINT Price $105.00 Best Offer Jun 1, 2026"
    ),
    (
        "GOLDIN 2018-19 Panini National Treasures Rookie Patch Autograph (RPA) "
        "#132 Jalen Brunson PSA 10 Price $13,420.00 verified Auction Apr 10, 2026"
    ),
    (
        "EBAY 2018-19 Panini Prizm Luka Doncic #280 Silver PSA 10 Rookie RC "
        "Price $5,954.02 Auction Nov 30, 2025"
    ),
    (
        "ALT 2003-04 Topps Chrome Refractor LeBron James #111 PSA 9 "
        "Price $7,200.00 Fixed Price May 2, 2026"
    ),
    (
        "HERITAGE 1996-97 Topps Chrome Kobe Bryant #138 PSA 10 Price $9,500.00 "
        "verified Auction Jan 15, 2026"
    ),
    (
        "MY SLABS 2023-24 Panini Prizm Victor Wembanyama #136 Silver PSA 10 RC "
        "Price $1,875.00 Buy Now Mar 3, 2026"
    ),
    (
        "CARD HOBBY 2020-21 Panini Prizm Anthony Edwards #258 PSA 10 新秀 "
        "Price $520.00 Auction Feb 12, 2026"
    ),
    (
        "FANATICS WEEKLY 2019-20 Panini Prizm Zion Williamson #248 Silver "
        "BGS 9.5 Price $1,250.00 verified Best Offer Dec 8, 2025"
    ),
    (
        "EBAY 2021-22 Panini Prizm Cade Cunningham #282 Red Ice PSA 10 "
        "Price $180.50 Best Offer Sep 9, 2025"
    ),
    ("GOLDIN 2009-10 Panini Stephen Curry #307 PSA 10 Rookie Price $8,100.00 Auction Oct 21, 2025"),
]


def test_parse_cardladder_text_extracts_core_fields():
    rows = parse_cardladder_text("\n".join(SAMPLES))

    assert len(rows) == len(SAMPLES)
    assert rows[0].platform == "FANATICS"
    assert rows[0].raw_title == (
        "2025 Topps Now Tyrese Maxey VJ Edgecombe ROOKIE #32 PSA 10 GEM MINT"
    )
    assert rows[0].price_usd == Decimal("105.00")
    assert rows[0].sold_at.date().isoformat() == "2026-06-01"
    assert rows[0].listing_type == "Best Offer"
    assert rows[1].verified is True
    assert rows[1].listing_type == "Auction"
    assert rows[1].price_usd == Decimal("13420.00")
    assert rows[6].platform == "CARD HOBBY"
    assert "新秀" in rows[6].raw_title
    assert rows[7].platform == "FANATICS WEEKLY"
    assert rows[7].listing_type == "Best Offer"


def test_parse_cardladder_text_joins_wrapped_rows():
    text = """
    GOLDIN
    2018-19 Panini National Treasures Rookie Patch Autograph (RPA)
    #132 Jalen Brunson PSA 10
    Price $13,420.00 verified Auction Apr 10, 2026
    EBAY 2018-19 Panini Prizm Luka Doncic #280 PSA 10 Price $4,000.00 Auction Apr 11, 2026
    """

    rows = parse_cardladder_text(text)

    assert len(rows) == 2
    assert rows[0].platform == "GOLDIN"
    assert rows[0].raw_title.startswith("2018-19 Panini National Treasures")
    assert rows[0].verified is True
    assert rows[1].platform == "EBAY"


def test_parse_cardladder_text_skips_unparseable_chunks():
    rows = parse_cardladder_text(
        """
        Sales History
        Something without price
        EBAY 2018-19 Panini Prizm Luka Doncic #280 PSA 10 Price $4,000.00 Auction Apr 11, 2026
        """
    )

    assert len(rows) == 1
    assert rows[0].platform == "EBAY"


def test_parse_cardladder_text_strips_ebay_seller_prefix():
    rows = parse_cardladder_text(
        "EBAY - PERFECT EDGES 2019 Donruss Optic Luka Doncic #16 FANATICS Prizm - "
        "PSA 10 Price $102.97 Best Offer Jun 1, 2026"
    )

    assert len(rows) == 1
    assert rows[0].raw_title == "2019 Donruss Optic Luka Doncic #16 FANATICS Prizm - PSA 10"


def test_quick_sale_builds_same_shape():
    sale = build_quick_sale(
        platform="ebay",
        raw_title="2018-19 Panini Prizm Luka Doncic #280 Silver PSA 10 Rookie RC",
        price_usd=Decimal("5954.02"),
        sold_date="2025-11-30",
        listing_type="Auction",
        verified=True,
    )

    assert sale.platform == "EBAY"
    assert sale.price_usd == Decimal("5954.02")
    assert sale.sold_at.date().isoformat() == "2025-11-30"
    assert sale.verified is True
    assert "Price $5,954.02" in sale.raw_text


def test_sale_dict_round_trip():
    sale = parse_cardladder_text(SAMPLES[2])[0]
    sale = sale.with_metadata(
        search_query="Luka Doncic Prizm PSA 10",
        external_sale_id="ebay-123",
    )

    restored = sale.from_dict(sale.to_dict())

    assert restored == sale
    assert stable_external_id(restored) == "ebay-123"


def test_import_cardladder_sales_dedupes_and_writes_clean_best_effort(migrated_db):
    sale = parse_cardladder_text(SAMPLES[2])[0]

    first = import_cardladder_sales([sale])
    second = import_cardladder_sales([sale])

    assert first.inserted_raw == 1
    assert first.inserted_clean == 1
    assert second.inserted_raw == 0
    assert second.skipped_duplicates == 1


def test_import_cardladder_sales_persists_search_query_and_sale_id(migrated_db):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from sportscards.db.models import TxRaw

    sale = parse_cardladder_text(SAMPLES[2])[0].with_metadata(
        search_query="Luka Doncic Prizm PSA 10",
        external_sale_id="ebay-123",
    )

    result = import_cardladder_sales([sale])

    assert result.inserted_raw == 1
    engine = create_engine(migrated_db)
    with Session(engine) as session:
        raw = session.execute(select(TxRaw)).scalar_one()

    assert raw.external_id == "ebay-123"
    assert raw.raw_json["search_query"] == "Luka Doncic Prizm PSA 10"
    assert raw.raw_json["external_sale_id"] == "ebay-123"
