"""Tests for sportscards.master.seed rowcount reporting.

psycopg returns rowcount=-1 for ``ON CONFLICT DO NOTHING`` even when a row
was actually inserted, so the old ``max(res.rowcount or 0, 0)`` always
reported 0 inserts. The fix uses ``.returning(<pk>)`` + ``len(res.all())``.

Postgres-only: ``ON CONFLICT DO NOTHING ... RETURNING`` semantics differ
across dialects, and ``pg_insert`` doesn't compile on SQLite. Gated on
``RUN_INTEGRATION=1``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="requires Postgres (set RUN_INTEGRATION=1)",
)


@pytest.fixture
def pg_db(monkeypatch):
    url = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://sportscards:sportscards@localhost:5433/sportscards"
    )
    monkeypatch.setenv("DATABASE_URL", url)
    import sportscards.db.session as _sess
    from sportscards.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(_sess, "_engine", None)
    monkeypatch.setattr(_sess, "_SessionLocal", None)
    # Clear test rows from prior runs
    from sqlalchemy import delete

    from sportscards.db.models import Card, Player

    with _sess.session_scope() as s:
        s.execute(delete(Card).where(Card.set_name == "TEST_SEED_FIXTURE"))
        s.execute(delete(Player).where(Player.br_slug.like("test-seed-%")))
    yield url
    with _sess.session_scope() as s:
        s.execute(delete(Card).where(Card.set_name == "TEST_SEED_FIXTURE"))
        s.execute(delete(Player).where(Player.br_slug.like("test-seed-%")))


def _write_yaml(path: Path, rows: list[dict]) -> Path:
    import yaml

    path.write_text(yaml.safe_dump(rows))
    return path


def test_seed_players_reports_inserted_count(pg_db, tmp_path):
    from sportscards.master.seed import seed_players

    yaml_path = _write_yaml(
        tmp_path / "players.yaml",
        [
            {"name": "Test SeedA", "br_slug": "test-seed-a", "position": "G"},
            {"name": "Test SeedB", "br_slug": "test-seed-b", "position": "F"},
        ],
    )
    first = seed_players(path=yaml_path)
    assert first == 2, f"expected 2, got {first}"
    second = seed_players(path=yaml_path)
    assert second == 0, f"expected 0 (all conflict), got {second}"


def test_seed_cards_reports_inserted_count(pg_db, tmp_path):
    from sportscards.master.seed import seed_cards, seed_players

    players_yaml = _write_yaml(
        tmp_path / "players.yaml",
        [{"name": "Test SeedC", "br_slug": "test-seed-c", "position": "G"}],
    )
    assert seed_players(path=players_yaml) == 1
    cards_yaml = _write_yaml(
        tmp_path / "cards.yaml",
        [
            {
                "year": 2020,
                "manufacturer": "Panini",
                "set": "TEST_SEED_FIXTURE",
                "card_number": "1",
                "parallel": "Base",
                "player": "Test SeedC",
                "is_rookie": True,
            }
        ],
    )
    first = seed_cards(path=cards_yaml)
    assert first == 1
    second = seed_cards(path=cards_yaml)
    assert second == 0
