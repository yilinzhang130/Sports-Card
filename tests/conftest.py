import pytest
from alembic import command
from alembic.config import Config

import sportscards.db.session as _sess
from sportscards.config import get_settings


@pytest.fixture
def migrated_db(tmp_path, monkeypatch):
    """Fresh sqlite DB with all migrations applied; isolated per test."""
    db_path = tmp_path / "test.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    # Clear lru_cache so get_settings() re-reads the env var
    get_settings.cache_clear()
    # Reset cached engine so session_scope picks up the new URL
    monkeypatch.setattr(_sess, "_engine", None)
    monkeypatch.setattr(_sess, "_SessionLocal", None)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    yield url
    # Teardown: reset globals so other tests with their own engines aren't poisoned
    monkeypatch.setattr(_sess, "_engine", None)
    monkeypatch.setattr(_sess, "_SessionLocal", None)
    get_settings.cache_clear()
