from streamlit.testing.v1 import AppTest


def test_models_page_renders(migrated_db):
    at = AppTest.from_file("reports/app/pages/6_🧪_Models.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
    # Page should render at least the title
    assert any("Models" in h.value for h in at.title)
