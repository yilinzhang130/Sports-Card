from reports.app._components import psa_yaml


def test_load_returns_list():
    rows = psa_yaml.load_priority()
    assert isinstance(rows, list)


def test_save_and_load_roundtrip(monkeypatch, tmp_path):
    fake = tmp_path / "psa_priority.yaml"
    monkeypatch.setattr(psa_yaml, "_YAML_PATH", fake)
    psa_yaml.save_priority(
        [{"card_id": 1, "psa_spec_id": "TBD"}, {"card_id": 2, "psa_spec_id": "S123"}]
    )
    rows = psa_yaml.load_priority()
    assert rows == [
        {"card_id": 1, "psa_spec_id": "TBD"},
        {"card_id": 2, "psa_spec_id": "S123"},
    ]


def test_cards_with_tbd(monkeypatch, tmp_path):
    fake = tmp_path / "psa_priority.yaml"
    monkeypatch.setattr(psa_yaml, "_YAML_PATH", fake)
    psa_yaml.save_priority(
        [
            {"card_id": 1, "psa_spec_id": "TBD"},
            {"card_id": 2, "psa_spec_id": "S123"},
            {"card_id": 3, "psa_spec_id": "TBD"},
        ]
    )
    assert sorted(psa_yaml.cards_with_tbd()) == [1, 3]


def test_set_spec_id_updates_existing(monkeypatch, tmp_path):
    fake = tmp_path / "psa_priority.yaml"
    monkeypatch.setattr(psa_yaml, "_YAML_PATH", fake)
    psa_yaml.save_priority([{"card_id": 1, "psa_spec_id": "TBD"}])
    psa_yaml.set_spec_id(1, "S999")
    rows = psa_yaml.load_priority()
    assert rows[0]["psa_spec_id"] == "S999"


def test_set_spec_id_appends_new(monkeypatch, tmp_path):
    fake = tmp_path / "psa_priority.yaml"
    monkeypatch.setattr(psa_yaml, "_YAML_PATH", fake)
    psa_yaml.save_priority([])
    psa_yaml.set_spec_id(42, "S999")
    rows = psa_yaml.load_priority()
    assert rows == [{"card_id": 42, "psa_spec_id": "S999"}]
