"""Audit log writer for the Trader Console UI."""

from __future__ import annotations

from typing import Any

from sportscards.db.models import AuditLog
from sportscards.db.session import session_scope


def write_audit(action: str, payload: dict[str, Any], actor: str = "ui") -> None:
    """Persist a single audit event. Caller-side metadata only — no side effects."""
    with session_scope() as s:
        s.add(AuditLog(action=action, payload_json=payload, actor=actor))
