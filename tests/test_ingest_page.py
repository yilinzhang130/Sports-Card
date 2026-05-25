from streamlit.testing.v1 import AppTest


def test_ingest_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/3_📥_Ingest.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
