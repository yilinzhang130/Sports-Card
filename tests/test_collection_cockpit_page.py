from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_collection_cockpit_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/12_🧭_Collection_Cockpit.py").run()

    assert not at.exception, f"unexpected exception: {at.exception}"
    assert at.title[0].value == "🧭 Collection Cockpit"
