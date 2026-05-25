import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_0010_creates_four_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0010")
    eng = sa.create_engine(f"sqlite:///{db_path}")
    insp = sa.inspect(eng)
    for t in ("portfolio_overrides", "portfolio_holdings", "model_run_log", "audit_log"):
        assert insp.has_table(t), f"{t} missing"


def test_0010_downgrade_drops(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "0010")
    command.downgrade(cfg, "0009")
    eng = sa.create_engine(f"sqlite:///{db_path}")
    insp = sa.inspect(eng)
    for t in ("portfolio_overrides", "portfolio_holdings", "model_run_log", "audit_log"):
        assert not insp.has_table(t)
