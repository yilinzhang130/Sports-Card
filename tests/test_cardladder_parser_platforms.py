from decimal import Decimal

from sportscards.ingest.cardladder_manual import parse_cardladder_text


def test_pristine_auction_parses_as_platform():
    rows = parse_cardladder_text(
        "PRISTINE AUCTION 2014 Panini Prizm Nikola Jokic #253 PSA 10 "
        "Price $1,763.99 verified Auction Jun 1, 2026"
    )

    assert len(rows) == 1
    assert rows[0].platform == "PRISTINE AUCTION"
    assert rows[0].price_usd == Decimal("1763.99")
    assert rows[0].verified is True
    assert rows[0].listing_type == "Auction"


def test_alt_confirmed_paid_prefix_is_not_title():
    rows = parse_cardladder_text(
        "ALT (CONFIRMED PAID) 2023-24 Panini Prizm Victor Wembanyama "
        "Silver PSA 10 Price $1,100.00 Fixed Price May 31, 2026"
    )

    assert len(rows) == 1
    assert rows[0].platform == "ALT"
    assert rows[0].verified is True
    assert rows[0].raw_title == "2023-24 Panini Prizm Victor Wembanyama Silver PSA 10"
