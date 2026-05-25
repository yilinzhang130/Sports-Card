from streamlit.testing.v1 import AppTest


def test_market_page_renders_against_empty_db(migrated_db):
    at = AppTest.from_file("reports/app/pages/1_📊_Market.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
