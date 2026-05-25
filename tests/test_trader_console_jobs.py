import time

from reports.app._components.jobs import submit_job, get_status, wait_for, has_running_jobs


def _quick(x):
    return {"answer": x * 2}


def _boom():
    raise RuntimeError("nope")


def test_submit_succeeds(migrated_db):
    run_id = submit_job("test_quick", _quick, params={"x": 3}, kwargs={"x": 3})
    summary = wait_for(run_id, timeout=5)
    assert summary["status"] == "succeeded"
    assert summary["summary_json"] == {"answer": 6}
    assert summary["finished_at"] is not None
    assert summary["error"] is None


def test_submit_failure_recorded(migrated_db):
    run_id = submit_job("test_boom", _boom, params={})
    summary = wait_for(run_id, timeout=5)
    assert summary["status"] == "failed"
    assert "nope" in summary["error"]


def test_single_worker_serializes(migrated_db):
    started: list[tuple[str, float]] = []

    def slow(tag):
        started.append((tag, time.monotonic()))
        time.sleep(0.2)
        return {"tag": tag}

    a = submit_job("test_a", slow, params={"tag": "a"}, kwargs={"tag": "a"})
    b = submit_job("test_b", slow, params={"tag": "b"}, kwargs={"tag": "b"})
    wait_for(a, timeout=5)
    wait_for(b, timeout=5)
    assert len(started) == 2
    # b should have started after a finished (~0.2s gap)
    assert started[1][1] - started[0][1] >= 0.15


def test_has_running_jobs(migrated_db):
    assert has_running_jobs() is False

    def slow():
        time.sleep(0.3)
        return {}

    run_id = submit_job("slow_one", slow, params={})
    # Give worker a moment to pick it up
    time.sleep(0.05)
    assert has_running_jobs() is True
    wait_for(run_id, timeout=5)
    assert has_running_jobs() is False
