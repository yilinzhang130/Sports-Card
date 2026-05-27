"""Minimal Basketball-Reference HTML scraper.

Replaces the unmaintained ``basketball_reference_scraper`` library, which
broke when modern pandas stopped accepting raw HTML strings in
``read_html``. Used only by ``LiveBRefClient`` in ``ingest_bref.py``.

Two public entry points:

* :func:`fetch_draft_class` — one row per drafted player in a given year.
  Pulls the ``#stats`` table from ``/draft/NBA_<year>.html``. The columns
  BR ships here are the player's *eventual* NBA career summary (PTS,
  TRB, AST, WS, BPM, VORP) plus draft slot — not their NCAA advanced
  rates. The downstream ``_normalize_prospects`` helper fills the
  ``trb_pct``/``ast_pct``/… columns it expects with ``NA`` if absent,
  matching the prior library's behavior.

* :func:`fetch_player_career_advanced` — up to N seasons of advanced NBA
  stats for a player, looked up by name via BR's search redirect.
  Returns columns with **uppercase** names (``BPM``, ``WS``, ``VORP``)
  to match the existing ``BRefClient`` contract.

Raw HTML is cached to ``data/scouting_cache/bref_html/`` so repeat ingest
runs are free. Pass ``refresh=True`` to bypass the cache for a single
call. The module-level rate limiter enforces ~17 req/min, comfortably
under BR's documented ~20/min throttle.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup, Tag
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.basketball-reference.com"
_USER_AGENT = "sportscards-quant/0.1 (research; +https://github.com/yilinzhang130/Sports-Card)"
_CACHE_DIR = Path("data/scouting_cache/bref_html")
_MIN_REQ_INTERVAL_S = 3.5  # BR throttles around 20 req/min
_REQUEST_TIMEOUT_S = 30.0

_last_request_ts: float = 0.0

_SLUG_FROM_HREF = re.compile(r"/players/[a-z]/([a-z0-9]+)\.html")


def _cache_key_for_url(url: str) -> str:
    """Filesystem-safe cache key for a BR URL.

    Examples:
        ``/draft/NBA_2018.html`` → ``draft_2018.html``
        ``/players/d/doncilu01.html`` → ``player_doncilu01.html``
    """
    path = urllib.parse.urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "root.html"
    last = parts[-1]
    if last.endswith(".html"):
        last = last[:-5]
    if "players" in parts:
        return f"player_{last}.html"
    if "draft" in parts:
        # /draft/NBA_2018.html → draft_2018.html
        year_match = re.search(r"(\d{4})", last)
        if year_match:
            return f"draft_{year_match.group(1)}.html"
        return f"draft_{last}.html"
    return f"{'_'.join(parts[-2:])}.html"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=3, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    reraise=True,
)
def _rate_limited_get(url: str, refresh: bool = False) -> str:
    """Fetch a BR URL, honoring cache and the inter-request delay."""
    cache_path = _CACHE_DIR / _cache_key_for_url(url)
    if cache_path.exists() and not refresh:
        return cache_path.read_text(encoding="utf-8")

    global _last_request_ts
    now = time.monotonic()
    wait_s = _MIN_REQ_INTERVAL_S - (now - _last_request_ts)
    if wait_s > 0:
        time.sleep(wait_s)

    with httpx.Client(
        headers={"User-Agent": _USER_AGENT},
        timeout=_REQUEST_TIMEOUT_S,
        follow_redirects=True,
    ) as client:
        response = client.get(url)
    _last_request_ts = time.monotonic()
    response.raise_for_status()
    text = response.text

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(cache_path)
    return text


def _strip_html_comments(html: str) -> str:
    """Expose tables BR hides inside ``<!-- ... -->`` to evade scrapers."""
    return re.sub(r"<!--|-->", "", html)


def _cell_text(row: Tag, data_stat: str) -> str | None:
    cell = row.find(attrs={"data-stat": data_stat})
    if cell is None:
        return None
    text = cell.get_text(strip=True)
    return text or None


def _attr_str(tag: Any, key: str, default: str = "") -> str:
    """Coerce a BS4 attribute (which may be ``str | list[str] | None``) to ``str``."""
    val = tag.get(key, default) if tag is not None else default
    if val is None:
        return default
    if isinstance(val, list):
        return " ".join(val)
    return str(val)


def _attr_classes(tag: Any) -> list[str]:
    """BS4 returns ``class`` as ``list[str] | None``; normalize to ``list[str]``."""
    val = tag.get("class") if tag is not None else None
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    return list(val)


def fetch_draft_class(year: int, refresh: bool = False) -> pd.DataFrame:
    """Pull the NBA draft class for ``year``.

    Returns one row per drafted player with at least: ``br_slug``,
    ``name``, ``draft_pick``, ``draft_year``. Additional career-summary
    columns BR ships on the draft page (``ws``, ``bpm``, ``vorp``,
    ``pts_per_g``, …) are included where present.
    """
    url = f"{_BASE_URL}/draft/NBA_{year}.html"
    html = _strip_html_comments(_rate_limited_get(url, refresh=refresh))
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="stats")
    if table is None:
        logger.warning("no #stats table on %s — returning empty frame", url)
        return pd.DataFrame(columns=["br_slug", "name", "draft_pick", "draft_year"])

    tbody = table.find("tbody") if isinstance(table, Tag) else None
    rows: list[Tag] = (
        [r for r in tbody.find_all("tr") if isinstance(r, Tag)] if isinstance(tbody, Tag) else []
    )
    records: list[dict[str, object]] = []
    for row in rows:
        classes = _attr_classes(row)
        if "thead" in classes or "over_header" in classes:
            continue
        player_td = row.find("td", attrs={"data-stat": "player"})
        anchor = player_td.find("a") if isinstance(player_td, Tag) else None
        if not isinstance(anchor, Tag):
            continue
        slug_match = _SLUG_FROM_HREF.search(_attr_str(anchor, "href"))
        if slug_match is None:
            continue

        pick_text = _cell_text(row, "pick_overall")
        rec: dict[str, object] = {
            "br_slug": slug_match.group(1),
            "name": anchor.get_text(strip=True),
            "draft_pick": pd.to_numeric(pick_text, errors="coerce") if pick_text else pd.NA,
            "draft_year": year,
        }
        # Pass through any other cells BR provides on the draft page. The
        # downstream _normalize_prospects helper only reads a fixed
        # subset; extras are harmless.
        for cell in row.find_all(["td", "th"]):
            if not isinstance(cell, Tag):
                continue
            ds = _attr_str(cell, "data-stat")
            if ds in ("", "player", "pick_overall", "ranker") or ds in rec:
                continue
            text = cell.get_text(strip=True) or None
            if text is None:
                rec[ds] = pd.NA
            else:
                # Numeric-looking columns: coerce. String columns
                # (team_id, college_name) survive coercion as NaN, which
                # is wrong, so attempt numeric only when the text parses.
                num = pd.to_numeric(text, errors="coerce")
                rec[ds] = num if pd.notna(num) else text
        records.append(rec)

    df = pd.DataFrame(records)
    if "draft_pick" in df.columns:
        df["draft_pick"] = pd.to_numeric(df["draft_pick"], errors="coerce")
    return df


def _player_url(br_slug: str) -> str:
    """Build the canonical BR player-page URL from a slug.

    BR organizes player pages alphabetically by the first letter of the
    slug: ``/players/d/doncilu01.html``.
    """
    if not br_slug or not br_slug[0].isalpha():
        raise ValueError(f"invalid br_slug: {br_slug!r}")
    return f"{_BASE_URL}/players/{br_slug[0].lower()}/{br_slug}.html"


def _parse_advanced_table(html: str, max_seasons: int) -> pd.DataFrame:
    """Extract up-to-``max_seasons`` season-summary rows from ``#advanced``.

    Drops team-split rows (``class="partial_table"``) so summary stats
    aren't double-counted. Column names are uppercased to match the
    legacy ``BRefClient.get_player_career_advanced`` contract.
    """
    soup = BeautifulSoup(_strip_html_comments(html), "lxml")
    table = soup.find("table", id="advanced")
    tbody = table.find("tbody") if isinstance(table, Tag) else None
    if not isinstance(tbody, Tag):
        return pd.DataFrame(columns=["BPM", "WS", "VORP"])

    records: list[dict[str, object]] = []
    for row in tbody.find_all("tr"):
        if not isinstance(row, Tag):
            continue
        classes = _attr_classes(row)
        if "thead" in classes or "over_header" in classes:
            continue
        if "partial_table" in classes:
            # Team-split row; the summary is the row immediately above.
            continue
        rec: dict[str, object] = {}
        for cell in row.find_all(["td", "th"]):
            if not isinstance(cell, Tag):
                continue
            ds = _attr_str(cell, "data-stat")
            if not ds:
                continue
            text = cell.get_text(strip=True) or None
            if text is None:
                rec[ds] = pd.NA
                continue
            num = pd.to_numeric(text, errors="coerce")
            rec[ds] = num if pd.notna(num) else text
        if rec:
            records.append(rec)
        if len(records) >= max_seasons:
            break

    df = pd.DataFrame(records)
    df.columns = [c.upper() for c in df.columns]
    return df


def fetch_player_career_advanced(
    br_slug: str, max_seasons: int = 5, refresh: bool = False
) -> pd.DataFrame:
    """Pull the advanced career stats for one player, looked up by BR slug.

    Returns up to ``max_seasons`` rows with **uppercase** column names
    (``BPM``, ``WS``, ``VORP``, …), matching the
    ``BRefClient.get_player_career_advanced`` contract.

    Passing the slug instead of the player name avoids BR's brittle
    search endpoint (which returns a 200 results page rather than a
    302 redirect for many names, so slug recovery from the response URL
    is unreliable).
    """
    html = _rate_limited_get(_player_url(br_slug), refresh=refresh)
    return _parse_advanced_table(html, max_seasons=max_seasons)
