"""Single-worker background job runner with model_run_log persistence."""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from sportscards.db.models import ModelRunLog
from sportscards.db.session import session_scope

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trader-console-job")
_lock = threading.Lock()


def submit_job(
    action: str,
    fn: Callable[..., dict[str, Any]],
    *,
    params: dict[str, Any],
    kwargs: dict[str, Any] | None = None,
) -> int:
    """Queue a job. Returns the model_run_log.run_id immediately."""
    with session_scope() as s:
        row = ModelRunLog(action=action, params_json=params, status="running")
        s.add(row)
        s.flush()
        run_id = row.run_id
    _executor.submit(_runner, run_id, fn, kwargs or {})
    return run_id


def _runner(run_id: int, fn: Callable[..., dict[str, Any]], kwargs: dict[str, Any]) -> None:
    try:
        result = fn(**kwargs)
        _finalize(run_id, status="succeeded", summary=result, error=None)
    except Exception as e:  # noqa: BLE001 — record any failure mode
        _finalize(
            run_id,
            status="failed",
            summary=None,
            error=f"{e}\n\n{traceback.format_exc()}",
        )


def _finalize(
    run_id: int, *, status: str, summary: dict[str, Any] | None, error: str | None
) -> None:
    with session_scope() as s:
        row = s.get(ModelRunLog, run_id)
        if row is None:
            return
        row.status = status
        row.summary_json = summary
        row.error = error
        row.finished_at = datetime.now(timezone.utc)


def get_status(run_id: int) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(ModelRunLog, run_id)
        if row is None:
            raise KeyError(f"unknown run_id {run_id}")
        return {
            "run_id": row.run_id,
            "action": row.action,
            "status": row.status,
            "summary_json": row.summary_json,
            "error": row.error,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }


def wait_for(run_id: int, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = get_status(run_id)
        if s["status"] in ("succeeded", "failed"):
            return s
        time.sleep(0.05)
    raise TimeoutError(f"job {run_id} did not complete within {timeout}s")


def has_running_jobs() -> bool:
    with session_scope() as s:
        return s.query(ModelRunLog).filter_by(status="running").first() is not None
