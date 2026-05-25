"""Tests for the reporting layer."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from sportscards.reports import queries, render


def test_table_missing_is_raised_when_table_absent():
    """If the target table does not exist, queries raise TableMissing."""
    fake_engine = MagicMock()
    with patch("sportscards.reports.queries.inspect") as mock_inspect:
        mock_inspect.return_value.has_table.return_value = False
        with pytest.raises(queries.TableMissing):
            queries.repeat_sales_index(engine=fake_engine)


# --- Renderer tests ----------------------------------------------------------


def test_render_monthly_letter_with_mocked_metrics(tmp_path, monkeypatch):
    """Renderer fills in tables and writes letters/YYYY-MM.md."""
    top = pd.DataFrame(
        [
            {
                "player": "LeBron James",
                "year": 2003,
                "set_name": "Topps Chrome",
                "parallel": "Refractor",
                "residual": 412.5,
            }
        ]
    )
    sleeves = pd.DataFrame(
        [{"sleeve": "Modern PSA10", "target_weight": 0.6, "current_weight": 0.55}]
    )
    fake_metrics = render.LetterMetrics(
        month="2024-12",
        index_returns={"1m": 0.012, "3m": 0.034, "12m": 0.21},
        top_mispricings=top,
        rebalance_trades=None,
        fee_drag_ytd=0.087,
        sleeve_allocation=sleeves,
    )
    monkeypatch.setattr(render, "collect_letter_metrics", lambda month, engine=None: fake_metrics)

    out = render.render_monthly_letter("2024-12", out_dir=tmp_path)
    body = Path(out).read_text()

    assert out.name == "2024-12.md"
    assert "1.20%" in body
    assert "LeBron James" in body
    assert "Modern PSA10" in body
    assert "Phase 4 pending" in body


def test_render_monthly_letter_is_idempotent(tmp_path, monkeypatch):
    """Re-rendering the same month overwrites the file without error."""
    metrics = render.LetterMetrics(
        month="2024-12",
        index_returns=None,
        top_mispricings=None,
        rebalance_trades=None,
        fee_drag_ytd=None,
        sleeve_allocation=None,
    )
    monkeypatch.setattr(render, "collect_letter_metrics", lambda month, engine=None: metrics)
    p1 = render.render_monthly_letter("2024-12", out_dir=tmp_path)
    p2 = render.render_monthly_letter("2024-12", out_dir=tmp_path)
    assert p1 == p2
    assert p1.exists()


# --- Catalyst query tests ----------------------------------------------------


@pytest.fixture()
def catalyst_engine():
    from datetime import datetime, timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sportscards.db.models import Base, Player, PlayerEvent

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime.now(UTC)
    with Session(engine) as sess:
        players = [
            Player(name="Alpha", br_slug="alpha"),
            Player(name="Beta", br_slug="beta"),
            Player(name="Gamma", br_slug="gamma"),
        ]
        for p in players:
            sess.add(p)
        sess.flush()
        a_id, b_id, g_id = (p.player_id for p in players)

        # Alpha: recent MVP — high positive score.
        sess.add(
            PlayerEvent(
                player_id=a_id,
                event_type="mvp",
                event_date=now - timedelta(days=5),
                event_payload={},
            )
        )
        # Beta: recent season-ending injury — high negative score.
        sess.add(
            PlayerEvent(
                player_id=b_id,
                event_type="injury_out",
                event_date=now - timedelta(days=2),
                event_payload={"season_ending": True},
            )
        )
        # Gamma: small recent transaction — low score.
        sess.add(
            PlayerEvent(
                player_id=g_id,
                event_type="two_way",
                event_date=now - timedelta(days=3),
                event_payload={},
            )
        )
        # Out-of-window event for Alpha — should not appear in recent_events.
        sess.add(
            PlayerEvent(
                player_id=a_id,
                event_type="all_star",
                event_date=now - timedelta(days=120),
                event_payload={},
            )
        )
        sess.commit()

    return engine


def test_recent_events_window(catalyst_engine):
    df = queries.recent_events(engine=catalyst_engine, days=30)
    assert not df.empty
    # The 120-day-old all_star event must be excluded.
    assert "all_star" not in df["event_type"].tolist()
    # All three recent events present.
    assert set(df["event_type"]) == {"mvp", "injury_out", "two_way"}
    assert set(df.columns) == {
        "event_date",
        "player_name",
        "event_type",
        "event_payload",
    }


def test_recent_events_excludes_outside_window(catalyst_engine):
    # Tight window — only the events within the last 4 days survive.
    df = queries.recent_events(engine=catalyst_engine, days=4)
    assert set(df["event_type"]) == {"injury_out", "two_way"}


def test_top_catalysts_ranks_by_abs_and_respects_limit(catalyst_engine):
    df = queries.top_catalysts(engine=catalyst_engine, days=30, limit=2)
    assert len(df) == 2
    # Alpha (MVP, +) and Beta (season-ending injury, -) should both rank
    # above Gamma (tiny two_way weight) when sorting by |score|.
    names = df["player_name"].tolist()
    assert "Gamma" not in names
    assert set(names) == {"Alpha", "Beta"}
    # And the magnitudes must be non-increasing.
    abs_scores = df["catalyst_score"].abs().tolist()
    assert abs_scores == sorted(abs_scores, reverse=True)


def test_catalyst_queries_raise_when_table_missing():
    """A fresh DB without player_events should raise TableMissing."""
    from sqlalchemy import create_engine

    engine = create_engine("sqlite:///:memory:")
    with pytest.raises(queries.TableMissing):
        queries.recent_events(engine=engine)
    with pytest.raises(queries.TableMissing):
        queries.top_catalysts(engine=engine)
    with pytest.raises(queries.TableMissing):
        queries.player_catalyst_sparkline(player_id=1, engine=engine)


def test_app_components_import_cleanly():
    """The multi-page app components import without executing Streamlit calls."""
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        auth = importlib.import_module("reports.app._components.auth")
        ui = importlib.import_module("reports.app._components.ui")
    finally:
        sys.path.remove(str(repo_root))
    assert hasattr(auth, "guard_localhost")
    assert hasattr(ui, "confirm_toggle")
    assert hasattr(ui, "job_badge")
