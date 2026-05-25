"""Safe read/write helpers for psa_priority.yaml."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

_YAML_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "sportscards"
    / "master"
    / "seed_data"
    / "psa_priority.yaml"
)


def yaml_path() -> Path:
    return _YAML_PATH


def load_priority() -> list[dict[str, Any]]:
    if not _YAML_PATH.exists():
        return []
    with _YAML_PATH.open() as f:
        data = yaml.safe_load(f) or []
    return data


def save_priority(rows: list[dict[str, Any]]) -> None:
    """Atomic write."""
    _YAML_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — manual close needed for atomic os.replace
        "w", dir=_YAML_PATH.parent, prefix=".psa_priority_", suffix=".tmp", delete=False
    )
    try:
        yaml.safe_dump(rows, tmp, default_flow_style=True, sort_keys=False)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, _YAML_PATH)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp.name)
        raise


def cards_with_tbd() -> list[int]:
    return [int(r["card_id"]) for r in load_priority() if str(r.get("psa_spec_id", "")) == "TBD"]


def set_spec_id(card_id: int, spec_id: str) -> None:
    rows = load_priority()
    found = False
    for r in rows:
        if int(r.get("card_id", -1)) == card_id:
            r["psa_spec_id"] = spec_id
            found = True
            break
    if not found:
        rows.append({"card_id": card_id, "psa_spec_id": spec_id})
    save_priority(rows)
