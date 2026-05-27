"""Minimal Basketball-Reference HTML scraper.

Replaces the unmaintained ``basketball_reference_scraper`` library, which
broke when modern pandas stopped accepting raw HTML strings in
``read_html``. Used only by ``LiveBRefClient`` in ``ingest_bref.py``.

Three public entry points:

* :func:`fetch_draft_class` — one row per drafted player in a given year,
  including the advanced college stats BR ships on the draft page (often
  wrapped in ``<!-- ... -->`` comments to evade simple scrapers).
* :func:`fetch_player_career_advanced` — up to N seasons of advanced NBA
  stats for a player, looked up by name via BR's search redirect.

Raw HTML is cached to ``data/scouting_cache/bref_html/`` so repeat ingest
runs are free. Pass ``refresh=True`` to bypass the cache for a single call.

Polite to BR: a module-level rate limiter enforces ~17 req/min (under the
documented ~20/min throttle), and ``tenacity`` retries transient errors
with exponential backoff.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.basketball-reference.com"
_USER_AGENT = (
    "sportscards-quant/0.1 (research; +https://github.com/yilinzhang130/Sports-Card)"
)
_CACHE_DIR = Path("data/scouting_cache/bref_html")
_MIN_REQ_INTERVAL_S = 3.5  # BR throttles around 20 req/min
_REQUEST_TIMEOUT_S = 30.0

_last_request_ts: float = 0.0


def _cache_key_for_url(url: str) -> str:
    """Derive a filesystem-safe cache key from a BR URL.

    Examples:
        ``/draft/NBA_2018.html`` → ``draft_NBA_2018.html``
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
        return f"{last}.html"
    return f"{'_'.join(parts[-2:])}.html"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=3, max=30),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError)),
    reraise=True,
)
def _rate_limited_get(url: str, refresh: bool = False) -> str:
    """Fetch a BR URL, honoring cache and the inter-request delay.

    Returns the response text. Raises ``httpx.HTTPStatusError`` on a 4xx/5xx
    that survives retries; transient errors are retried 3x with exponential
    backoff.
    """
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
    """Expose tables BR hides inside ``<!-- ... -->`` comments."""
    return re.sub(r"<!--|-->", "", html)


def fetch_draft_class(year: int, refresh: bool = False) -> pd.DataFrame:
    """Pull the NBA draft class for ``year``.

    Returns one row per drafted player, with columns matching the
    ``BRefClient.get_draft_class`` contract: ``br_slug``, ``name``,
    ``draft_pick``, ``draft_year``, ``position``, ``age_at_draft``,
    ``trb_pct``, ``ast_pct``, ``stl_pct``, ``blk_pct``, ``usg_pct``,
    ``ts_pct``. Missing columns are ``NA``.
    """
    raise NotImplementedError


def fetch_player_career_advanced(
    name: str, max_seasons: int = 5, refresh: bool = False
) -> pd.DataFrame:
    """Pull the advanced career stats for one player, looked up by name.

    Returns up to ``max_seasons`` rows with **uppercase** column names
    (``BPM``, ``WS``, ``VORP``, …), matching the
    ``BRefClient.get_player_career_advanced`` contract.
    """
    raise NotImplementedError
