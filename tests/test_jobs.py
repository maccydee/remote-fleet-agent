import asyncio
import os
import stat

import pytest

from remote_fleet_agent.jobs import JobManager


def evidence(value="same", status="complete", truncated=False):
    return {
        "status": status,
        "sections": {
            "os": {
                "status": "collected" if status == "complete" else "unknown",
                "items": [{"version": value}],
                "truncated": truncated,
            }
        },
    }


@pytest.mark.asyncio
async def test_jobs_bound_concurrency_keep_failures_and_retry_scope(tmp_path):
    active = maximum = 0

    async def worker(host, operation, sections):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(0.005)
            if host == "bad":
                raise OSError("offline")
            return evidence()
        finally:
            active -= 1

    manager = JobManager(tmp_path, "example", worker, concurrency=2)
    a = manager.start(["one", "bad", "two", "three"], "inventory")
    b = manager.start(["four"], "inventory")
    first, second = await asyncio.gather(manager.wait(a["id"]), manager.wait(b["id"]))
    assert maximum <= 2
    assert first["status"] == "partial"
    assert second["status"] == "complete"
    assert manager.retry_hosts(a["id"])[1] == ["bad"]
    assert first["hosts"]["bad"]["error"] == "offline"
    if os.name != "nt":
        assert stat.S_IMODE(manager._file(a["id"]).stat().st_mode) == 0o600
    reloaded = JobManager(tmp_path, "example", worker)
    assert reloaded.get(a["id"])["hosts"]["one"]["result"] == evidence()
    assert "result" not in reloaded.list()[0]["hosts"]["four"]


@pytest.mark.asyncio
async def test_cancellation_does_not_erase_completed_evidence(tmp_path):
    started = asyncio.Event()

    async def worker(host, *_):
        if host == "done":
            return evidence()
        started.set()
        await asyncio.Event().wait()

    manager = JobManager(tmp_path, "example", worker, concurrency=1)
    job = manager.start(["done", "slow", "queued"], "inventory")
    await asyncio.wait_for(started.wait(), 1)
    result = await manager.cancel(job["id"])
    assert result["status"] == "cancelled"
    assert manager.get(job["id"])["hosts"]["done"]["result"] == evidence()
    assert result["hosts"]["queued"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_before_worker_starts_is_saved(tmp_path):
    called = False

    async def worker(*_):
        nonlocal called
        called = True

    manager = JobManager(tmp_path, "example", worker)
    job = manager.start(["pc"], "inventory")
    assert (await manager.cancel(job["id"]))["status"] == "cancelled"
    assert not called


@pytest.mark.asyncio
async def test_dead_owner_marks_only_unfinished_hosts_interrupted(tmp_path):
    manager = JobManager(tmp_path, "example", None)
    job_id = "a" * 32
    manager._save(
        {
            "id": job_id,
            "client": "example",
            "status": "running",
            "owner_pid": 2147483647,
            "hosts": {
                "good": {"status": "complete"},
                "unfinished": {"status": "running"},
            },
        }
    )
    job = manager.get(job_id)
    assert job["status"] == "interrupted"
    assert job["hosts"]["good"]["status"] == "complete"
    assert job["hosts"]["unfinished"]["status"] == "interrupted"
    with pytest.raises(ValueError):
        manager.get("../outside")


@pytest.mark.asyncio
async def test_comparison_excludes_unknown_and_truncated_data(tmp_path):
    value = "old"
    truncated = False

    async def worker(*_):
        return evidence(value, truncated=truncated)

    manager = JobManager(tmp_path, "example", worker)
    a = manager.start(["pc"], "inventory")
    await manager.wait(a["id"])
    value = "new"
    b = manager.start(["pc"], "inventory")
    await manager.wait(b["id"])
    assert manager.compare(a["id"], b["id"])["hosts"]["pc"]["changed_sections"] == [
        "os"
    ]
    truncated = True
    c = manager.start(["pc"], "inventory")
    await manager.wait(c["id"])
    comparison = manager.compare(b["id"], c["id"])["hosts"]["pc"]
    assert comparison["changed_sections"] == []
    assert comparison["unavailable_sections"] == ["os"]
