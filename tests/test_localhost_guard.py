from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_guard_blocks_non_localhost(migrated_db):
    at = AppTest.from_file("reports/app/Home.py")
    with patch("reports.app._components.auth._request_host", return_value="192.168.1.50:8501"):
        at.run()
    # The guard should call st.error and st.stop; check for the error text
    error_messages = [str(e.value) for e in at.error]
    assert any("external access blocked" in m.lower() for m in error_messages), (
        f"expected guard to block; errors: {error_messages}"
    )


def test_guard_allows_localhost(migrated_db):
    at = AppTest.from_file("reports/app/Home.py")
    with patch("reports.app._components.auth._request_host", return_value="localhost:8501"):
        at.run()
    assert not at.exception, f"unexpected exception: {at.exception}"


def test_guard_allows_when_no_host_header(migrated_db):
    """AppTest / direct script runs have no headers — must not block."""
    at = AppTest.from_file("reports/app/Home.py")
    with patch("reports.app._components.auth._request_host", return_value=None):
        at.run()
    assert not at.exception
