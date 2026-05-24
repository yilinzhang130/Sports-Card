"""Tests for the reporting layer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sportscards.reports import queries


def test_table_missing_is_raised_when_table_absent():
    """If the target table does not exist, queries raise TableMissing."""
    fake_engine = MagicMock()
    with patch("sportscards.reports.queries.inspect") as mock_inspect:
        mock_inspect.return_value.has_table.return_value = False
        with pytest.raises(queries.TableMissing):
            queries.repeat_sales_index(engine=fake_engine)


# --- Renderer tests ----------------------------------------------------------

from pathlib import Path

import pandas as pd

from sportscards.reports import render


def test_render_monthly_letter_with_mocked_metrics(tmp_path, monkeypatch):
    """Renderer fills in tables and writes letters/YYYY-MM.md."""
    top = pd.DataFrame(
        [{"player": "LeBron James", "year": 2003, "set_name": "Topps Chrome",
          "parallel": "Refractor", "residual": 412.5}]
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
    monkeypatch.setattr(
        render, "collect_letter_metrics", lambda month, engine=None: fake_metrics
    )

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
        month="2024-12", index_returns=None, top_mispricings=None,
        rebalance_trades=None, fee_drag_ytd=None, sleeve_allocation=None,
    )
    monkeypatch.setattr(
        render, "collect_letter_metrics", lambda month, engine=None: metrics
    )
    p1 = render.render_monthly_letter("2024-12", out_dir=tmp_path)
    p2 = render.render_monthly_letter("2024-12", out_dir=tmp_path)
    assert p1 == p2
    assert p1.exists()
