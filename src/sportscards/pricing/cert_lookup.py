"""Card Ladder cert-based lookup.

Replaces the dead ``ingest/cardladder.py`` CSV import path. Card Ladder
Pro does not expose a market-data CSV export — only a collection-upload
CSV. The one programmatic surface that *is* viable is the cert-based
lookup endpoint, used by the grading-EV sleeve to confirm grade-ratio
premia for a specific slabbed card.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests


class CertNotFoundError(LookupError):
    """The cert number is not in Card Ladder's database."""


class RateLimitedError(RuntimeError):
    """Card Ladder returned 429; caller should back off and retry."""


@dataclass(frozen=True)
class CertLookupResult:
    cert_number: str
    grader: str
    grade: float
    last_sold_price: float | None
    last_sold_date: str | None
    card_ladder_value: float | None


class CardLadderCertLookup:
    def __init__(self, *, api_base: str, api_key: str, timeout: float = 10.0):
        self._base = api_base.rstrip("/")
        self._key = api_key
        self._timeout = timeout

    def get_cert(self, cert_number: str) -> CertLookupResult:
        url = f"{self._base}/cert/{cert_number}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {self._key}"},
            timeout=self._timeout,
        )
        if resp.status_code == 404:
            raise CertNotFoundError(cert_number)
        if resp.status_code == 429:
            raise RateLimitedError(cert_number)
        resp.raise_for_status()
        data = resp.json()
        ls = data.get("last_sold") or {}
        return CertLookupResult(
            cert_number=str(data.get("cert_number", cert_number)),
            grader=str(data.get("grader", "")),
            grade=float(data.get("grade", 0)),
            last_sold_price=(float(ls["price"]) if ls.get("price") is not None else None),
            last_sold_date=(str(ls["date"]) if ls.get("date") is not None else None),
            card_ladder_value=(
                float(data["card_ladder_value"])
                if data.get("card_ladder_value") is not None
                else None
            ),
        )
