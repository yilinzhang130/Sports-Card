"""PSA Public API client.

Free tier: 100 calls / day. We track usage in-process and persist it via a
simple file on disk so the daily flow can stop before exceeding quota.

Endpoints used:
- /publicapi/cert/GetByCertNumber/{cert}
- /publicapi/pop/GetPSAPopulation/{specId}   (population by grade for a spec)

Docs: https://www.psacard.com/publicapi/documentation
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from tenacity import retry, stop_after_attempt, wait_exponential

from sportscards.config import Settings, get_settings
from sportscards.db.models import PopSnapshot
from sportscards.db.session import session_scope

log = logging.getLogger(__name__)

BASE = "https://api.psacard.com/publicapi"
QUOTA_PATH = Path.home() / ".sportscards" / "psa_quota.json"
DAILY_LIMIT = 100


class PsaQuotaExceeded(RuntimeError):
    pass


class PsaClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.psa_token:
            raise RuntimeError("PSA_TOKEN not configured")
        self._http = httpx.Client(
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {self.settings.psa_token}",
                "Accept": "application/json",
            },
        )
        QUOTA_PATH.parent.mkdir(parents=True, exist_ok=True)

    def _load_quota(self) -> dict[str, Any]:
        if not QUOTA_PATH.exists():
            return {"date": date.today().isoformat(), "count": 0}
        data = json.loads(QUOTA_PATH.read_text())
        if data.get("date") != date.today().isoformat():
            return {"date": date.today().isoformat(), "count": 0}
        return data

    def _save_quota(self, data: dict[str, Any]) -> None:
        QUOTA_PATH.write_text(json.dumps(data))

    def quota_remaining(self) -> int:
        return DAILY_LIMIT - self._load_quota()["count"]

    def _tick(self) -> None:
        q = self._load_quota()
        if q["count"] >= DAILY_LIMIT:
            raise PsaQuotaExceeded(f"PSA daily quota {DAILY_LIMIT} exhausted")
        q["count"] += 1
        self._save_quota(q)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10))
    def _get(self, path: str) -> dict[str, Any]:
        self._tick()
        resp = self._http.get(f"{BASE}{path}")
        resp.raise_for_status()
        return resp.json()

    def get_population_by_spec(self, spec_id: str) -> dict[str, Any]:
        return self._get(f"/pop/GetPSAPopulation/{spec_id}")


def snapshot_pop(card_spec_pairs: list[tuple[int, str]]) -> int:
    """For each (card_id, psa_spec_id) pair, fetch pop and write snapshot rows.

    Stops gracefully when daily quota is exhausted. Returns rows written.
    """
    client = PsaClient()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    written = 0
    with session_scope() as s:
        for card_id, spec_id in card_spec_pairs:
            if client.quota_remaining() <= 0:
                log.warning("PSA quota exhausted; stopping")
                break
            try:
                payload = client.get_population_by_spec(spec_id)
            except PsaQuotaExceeded:
                break
            except Exception as e:
                log.warning("PSA pop fetch failed spec=%s: %s", spec_id, e)
                continue
            # Payload shape varies; assume list of {Grade, Population}
            rows = payload.get("PSAPopulation", payload) if isinstance(payload, dict) else []
            for r in rows if isinstance(rows, list) else []:
                grade = r.get("Grade") or r.get("grade")
                pop = r.get("Population") or r.get("pop")
                if grade is None or pop is None:
                    continue
                stmt = pg_insert(PopSnapshot).values(
                    snapshot_date=today,
                    card_id=card_id,
                    grader="PSA",
                    grade=Decimal(str(grade)),
                    pop_count=int(pop),
                ).on_conflict_do_update(
                    index_elements=["snapshot_date", "card_id", "grader", "grade"],
                    set_={"pop_count": int(pop)},
                )
                s.execute(stmt)
                written += 1
    log.info("wrote %d PSA pop rows", written)
    return written
