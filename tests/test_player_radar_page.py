from streamlit.testing.v1 import AppTest


def test_player_radar_page_renders_against_empty_db(migrated_db):
    at = AppTest.from_file("reports/app/pages/10_🎯_Player_Radar.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
