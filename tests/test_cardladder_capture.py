from sportscards.ingest.cardladder_capture import capture_links_to_sales, capture_links_to_text


def test_capture_links_to_text_extracts_descriptions_and_sale_ids():
    links = [
        {
            "description": (
                "EBAY - SELLER 2018-19 Panini Prizm Luka Doncic #280 PSA 10 "
                "Price $4,000.00 Auction Jun 1, 2026"
            ),
            "value": "app.cardladder.com/sales-history?q=Luka&saleId=ebay-123",
        },
        {
            "description": (
                "FANATICS WEEKLY 2013 Panini Prizm Giannis Antetokounmpo "
                "ROOKIE #290 PSA 10 Price $720.00 verified Auction Jun 1, 2026"
            ),
            "value": "app.cardladder.com/sales-history?q=Giannis&saleId=fanatics-weekly-456",
        },
        {
            "description": "launch",
            "value": "cardladder.zendesk.com",
        },
    ]

    text, sale_ids = capture_links_to_text(links)

    assert "Luka Doncic" in text
    assert "Giannis Antetokounmpo" in text
    assert "launch" not in text
    assert sale_ids == ["ebay-123", "fanatics-weekly-456"]


def test_capture_links_to_sales_attaches_query_and_sale_id():
    links = [
        {
            "description": (
                "EBAY - SELLER 2018-19 Panini Prizm Luka Doncic #280 PSA 10 "
                "Price $4,000.00 Auction Jun 1, 2026"
            ),
            "value": "app.cardladder.com/sales-history?q=Luka&saleId=ebay-123",
        },
        {
            "description": (
                "PRISTINE AUCTION 2014 Panini Prizm Nikola Jokic #253 PSA 10 "
                "Price $1,763.99 verified Auction Jun 1, 2026"
            ),
            "value": "app.cardladder.com/sales-history?q=Jokic&saleId=pristine-789",
        },
    ]

    sales = capture_links_to_sales(links, search_query="Luka Doncic Prizm PSA 10")

    assert [sale.external_sale_id for sale in sales] == ["ebay-123", "pristine-789"]
    assert {sale.search_query for sale in sales} == {"Luka Doncic Prizm PSA 10"}
    assert sales[0].raw_title == "2018-19 Panini Prizm Luka Doncic #280 PSA 10"
    assert sales[1].platform == "PRISTINE AUCTION"
