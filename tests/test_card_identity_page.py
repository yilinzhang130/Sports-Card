from __future__ import annotations

from streamlit.testing.v1 import AppTest


def test_card_identity_page_renders_against_empty_db(migrated_db):
    at = AppTest.from_file("reports/app/pages/11_🧬_Card_Identity.py").run()

    assert not at.exception, f"unexpected exception: {at.exception}"
