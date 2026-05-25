from streamlit.testing.v1 import AppTest


def test_backtest_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/8_📈_Backtest.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"


def test_catalysts_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/7_📅_Catalysts.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
