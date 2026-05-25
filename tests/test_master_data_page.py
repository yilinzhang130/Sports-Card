def test_master_data_page_renders(migrated_db, tmp_path, monkeypatch):
    from reports.app._components import psa_yaml

    monkeypatch.setattr(psa_yaml, "_YAML_PATH", tmp_path / "psa_priority.yaml")
    psa_yaml.save_priority([])

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("reports/app/pages/5_🃏_Master_Data.py").run()
    assert not at.exception, f"unexpected exception: {at.exception}"
