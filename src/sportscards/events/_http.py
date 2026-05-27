"""Shared HTTP utilities for the live event scrapers.

ESPN and basketball-reference.com both serve plain HTML to a desktop
browser User-Agent. The default httpx UA gets challenged or rate-limited,
so every live client routes through :func:`fetch_html` which sets a
real-browser UA and retries transient failures with tenacity.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DEFAULT_TIMEOUT = 30.0


def build_client(*, timeout: float = _DEFAULT_TIMEOUT) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, max=30),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def fetch_html(url: str, *, client: httpx.Client | None = None) -> str:
    """GET ``url`` and return the response body, retrying on transient errors."""
    owns_client = client is None
    c = client or build_client()
    try:
        resp = c.get(url)
        resp.raise_for_status()
        return resp.text
    finally:
        if owns_client:
            c.close()
