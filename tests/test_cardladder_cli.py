from __future__ import annotations

import json

from click.testing import CliRunner

from sportscards.cli.__main__ import cli


def test_cardladder_next_cli_prints_capture_plan(migrated_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", migrated_db)

    result = CliRunner().invoke(cli, ["cardladder", "next", "--limit", "1"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload[0]["query"] == "Giannis Antetokounmpo Prizm PSA 10"
    assert payload[0]["url"].startswith("https://app.cardladder.com/sales-history?")


def test_cardladder_import_links_cli_imports_json_file(migrated_db, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            [
                {
                    "description": (
                        "EBAY - SELLER 2018-19 Panini Prizm Luka Doncic #280 PSA 10 "
                        "Price $4,000.00 Auction Jun 1, 2026"
                    ),
                    "value": "ebay.com/itm/12345",
                }
            ]
        )
    )

    result = CliRunner().invoke(
        cli,
        [
            "cardladder",
            "import-links",
            "--query",
            "Luka Doncic Prizm PSA 10",
            str(path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["captured"] == 1
    assert payload["inserted_raw"] == 1


def test_cardladder_import_text_cli_imports_visible_body_text(migrated_db, monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    path = tmp_path / "body.txt"
    path.write_text(
        """
        EBAY - INFINITY CARDS AND BEYOND
        2023-24 Donruss Optic - Express Lane Ausar Thompson #18 Purple Prizm (RC) PSA 10
        Price
        $20.00
        Best Offer
        Jun 1, 2026
        """,
    )

    result = CliRunner().invoke(
        cli,
        [
            "cardladder",
            "import-text",
            "--query",
            "Ausar Thompson Prizm PSA 10",
            str(path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["captured"] == 1
    assert payload["inserted_raw"] == 1
