"""Daily PSA pop snapshot flow. Bounded by 100 calls/day free tier.

Reads a priority list of (card_id, psa_spec_id) pairs and refreshes pop
counts for as many as the quota allows.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from prefect import flow, get_run_logger

from sportscards.ingest.psa_api import snapshot_pop

PRIORITY_FILE = Path(__file__).parent.parent / "master" / "seed_data" / "psa_priority.yaml"


@flow(name="daily-psa-pop")
def daily_psa_pop_flow() -> int:
    log = get_run_logger()
    if not PRIORITY_FILE.exists():
        log.warning("PSA priority file missing at %s; nothing to snapshot", PRIORITY_FILE)
        return 0
    pairs_raw: list[dict[str, str | int]] = yaml.safe_load(PRIORITY_FILE.read_text()) or []
    pairs = [
        (int(p["card_id"]), str(p["psa_spec_id"]))
        for p in pairs_raw
        if str(p.get("psa_spec_id", "TBD")) != "TBD"
    ]
    if not pairs:
        log.warning("No PSA spec IDs filled in %s; run `sportscards psa lookup-cert`", PRIORITY_FILE)
        return 0
    return snapshot_pop(pairs)


if __name__ == "__main__":
    daily_psa_pop_flow()
