"""Tests for the internal Basketball-Reference scraper.

Fixtures live under ``tests/fixtures/bref/``. The scraper module's
``_rate_limited_get`` (and ``_resolve_player_html`` for player pages) is
monkey-patched so no test touches the network.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import httpx
import pandas as pd
import pytest

from sportscards.scouting.nba import _bref_scraper

FIXTURES = Path(__file__).parent / "fixtures" / "bref"


@pytest.fixture
def draft_2018_html() -> str:
    return (FIXTURES / "draft_2018.html").read_text()


@pytest.fixture
def luka_html() -> str:
    return (FIXTURES / "player_doncilu01.html").read_text()


@pytest.fixture
def bagley_html() -> str:
    return (FIXTURES / "player_baglema01.html").read_text()


def test_fetch_draft_class_returns_drafted_rows(draft_2018_html: str) -> None:
    with patch.object(_bref_scraper, "_rate_limited_get", return_value=draft_2018_html):
        df = _bref_scraper.fetch_draft_class(2018)

    assert len(df) >= 30, f"expected ≥30 drafted players, got {len(df)}"
    assert (df["draft_year"] == 2018).all()

    luka = df[df["name"].str.contains("Luka", na=False)]
    assert not luka.empty, "Luka Doncic should be in the 2018 draft class"
    assert int(luka.iloc[0]["draft_pick"]) == 3
    assert luka.iloc[0]["br_slug"] == "doncilu01"

    # br_slug should be the canonical BR slug, not a fallback to name
    deandre = df[df["name"].str.contains("Deandre Ayton", na=False)]
    assert deandre.iloc[0]["br_slug"] == "aytonde01"
    assert int(deandre.iloc[0]["draft_pick"]) == 1


def test_fetch_draft_class_strips_html_comment_wrappers() -> None:
    """If BR wraps the table in <!-- ... -->, we still parse it.

    The 2018 fixture is NOT comment-wrapped in the wild, but the scraper
    must tolerate the wrapping so a future BR format change doesn't break
    ingest silently.
    """
    raw = (FIXTURES / "draft_2018.html").read_text()
    wrapped = raw.replace("<table", "<!--<table", 1).replace("</table>", "</table>-->", 1)
    with patch.object(_bref_scraper, "_rate_limited_get", return_value=wrapped):
        df = _bref_scraper.fetch_draft_class(2018)
    assert len(df) >= 30


def test_fetch_player_career_advanced_luka(luka_html: str) -> None:
    with patch.object(
        _bref_scraper,
        "_resolve_player_html",
        return_value=("doncilu01", luka_html),
    ):
        df = _bref_scraper.fetch_player_career_advanced("Luka Doncic", max_seasons=5)

    assert len(df) <= 5
    assert {"BPM", "WS", "VORP"}.issubset(df.columns)
    # Luka was a +EV player from day one
    assert float(df["BPM"].iloc[0]) > 0
    assert float(df["WS"].sum()) > 0
    # Should be 5 distinct season-summary rows (no team-split duplicates)
    assert df["YEAR_ID"].nunique() == len(df)


def test_fetch_player_career_advanced_skips_partial_table_rows(bagley_html: str) -> None:
    """BR emits one row per team when a player was traded mid-season,
    plus a 2TM summary row. We must keep the summary and drop the splits
    or _aggregate_outcome would double-count.
    """
    with patch.object(
        _bref_scraper,
        "_resolve_player_html",
        return_value=("baglema01", bagley_html),
    ):
        df = _bref_scraper.fetch_player_career_advanced("Marvin Bagley III", max_seasons=5)

    assert df["YEAR_ID"].nunique() == len(df), "duplicate season rows leaked through"
    # Bagley was a sub-replacement player — career BPM should be negative
    assert float(df["BPM"].sum()) < 0


def test_fetch_player_career_advanced_respects_max_seasons(luka_html: str) -> None:
    with patch.object(
        _bref_scraper,
        "_resolve_player_html",
        return_value=("doncilu01", luka_html),
    ):
        df = _bref_scraper.fetch_player_career_advanced("Luka Doncic", max_seasons=3)
    assert len(df) == 3


# ---- _rate_limited_get cache & refresh behavior ---------------------------


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(_bref_scraper, "_CACHE_DIR", tmp_path)
    # Force the rate limiter to think no recent request occurred.
    monkeypatch.setattr(_bref_scraper, "_last_request_ts", 0.0)
    # Don't actually sleep during tests.
    monkeypatch.setattr(_bref_scraper.time, "sleep", lambda _s: None)
    yield tmp_path


def test_rate_limited_get_uses_cache_when_present(isolated_cache, monkeypatch) -> None:
    cache_dir = isolated_cache
    (cache_dir / "draft_2018.html").write_text("CACHED", encoding="utf-8")

    def _boom(self, url, *a, **kw):
        raise AssertionError("network should not be touched when cache exists")

    monkeypatch.setattr(httpx.Client, "get", _boom)
    text = _bref_scraper._rate_limited_get(
        "https://www.basketball-reference.com/draft/NBA_2018.html"
    )
    assert text == "CACHED"


def test_rate_limited_get_writes_cache_on_fetch(isolated_cache, monkeypatch) -> None:
    cache_dir = isolated_cache

    class FakeResp:
        status_code = 200
        text = "FRESH"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, *a, **kw: FakeResp())
    text = _bref_scraper._rate_limited_get(
        "https://www.basketball-reference.com/draft/NBA_2018.html"
    )
    assert text == "FRESH"
    assert (cache_dir / "draft_2018.html").read_text() == "FRESH"


def test_rate_limited_get_refresh_bypasses_cache(isolated_cache, monkeypatch) -> None:
    cache_dir = isolated_cache
    (cache_dir / "draft_2018.html").write_text("STALE", encoding="utf-8")

    class FakeResp:
        status_code = 200
        text = "FRESH"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx.Client, "get", lambda self, url, *a, **kw: FakeResp())
    text = _bref_scraper._rate_limited_get(
        "https://www.basketball-reference.com/draft/NBA_2018.html",
        refresh=True,
    )
    assert text == "FRESH"
    assert (cache_dir / "draft_2018.html").read_text() == "FRESH"


def test_cache_key_for_url_player_page() -> None:
    assert (
        _bref_scraper._cache_key_for_url(
            "https://www.basketball-reference.com/players/d/doncilu01.html"
        )
        == "player_doncilu01.html"
    )


def test_cache_key_for_url_draft_page() -> None:
    assert (
        _bref_scraper._cache_key_for_url(
            "https://www.basketball-reference.com/draft/NBA_2018.html"
        )
        == "draft_2018.html"
    )
