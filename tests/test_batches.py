import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remote_fleet_agent.batches import BatchManager
from remote_fleet_agent.config import FleetConfig, FleetHost


def manager(tmp_path, count=6):
    config = FleetConfig(
        "fixture",
        tmp_path / "known",
        tuple(
            FleetHost(f"pc-{n}", f"Admin@192.0.2.{n + 1}", ("fleet",))
            for n in range(count)
        ),
        concurrency=3,
        maintenance_actions=("start_time_service",),
    )
    calls = []

    async def preview(host, action):
        return {
            "id": str(int(host.split("-")[1]) + 1).zfill(32),
            "digest": "digest-" + host,
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "before": {"status": "Stopped", "start_type": "Manual"},
            "effect": "Start Windows Time",
            "recovery": "Stop service; clock effects remain",
        }

    async def execute(ident, digest):
        calls.append(ident)
        return {"id": ident, "status": "complete", "result": {"changed": True}}

    runtime = SimpleNamespace(
        config=config,
        collect=AsyncMock(return_value={"readiness": {"maintenance_ready": True}}),
        maintenance=SimpleNamespace(
            preview=AsyncMock(side_effect=preview),
            execute=AsyncMock(side_effect=execute),
        ),
    )
    return BatchManager(runtime, tmp_path), calls


@pytest.mark.asyncio
async def test_preview_never_mutates_and_canary_requires_separate_continuation(
    tmp_path,
):
    b, calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet", wave_size=2)
    assert calls == []
    with pytest.raises(ValueError, match="canary"):
        await b.execute(p["id"], p["digest"], continuation=True)
    c = await b.execute(p["id"], p["digest"])
    assert c["status"] == "awaiting_continuation" and len(calls) == 1
    with pytest.raises(ValueError, match="canary"):
        await b.execute(p["id"], p["digest"])
    r = await b.execute(p["id"], p["digest"], continuation=True)
    assert r["status"] == "complete" and len(calls) == 6
    with pytest.raises(ValueError):
        await b.execute(p["id"], p["digest"], continuation=True)


@pytest.mark.asyncio
async def test_failure_limit_blocks_later_waves_but_preserves_inflight_results(
    tmp_path,
):
    b, calls = manager(tmp_path)
    p = await b.preview(
        "start_time_service", group="fleet", wave_size=2, max_failures=1
    )
    await b.execute(p["id"], p["digest"])

    async def fail(ident, digest):
        calls.append(ident)
        return {
            "id": ident,
            "status": "unknown" if ident.endswith("2") else "complete",
            "result": {"changed": True},
        }

    b.runtime.maintenance.execute.side_effect = fail
    r = await b.execute(p["id"], p["digest"], continuation=True)
    assert r["status"] == "halted" and len(calls) == 3
    assert r["hosts"]["pc-2"]["status"] == "complete"
    assert all(r["hosts"][f"pc-{n}"]["status"] == "not_run" for n in (3, 4, 5))


@pytest.mark.asyncio
async def test_failed_canary_never_fans_out(tmp_path):
    b, _calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet")
    b.runtime.maintenance.execute.side_effect = ConnectionError("lost connection")
    result = await b.execute(p["id"], p["digest"])
    assert result["status"] == "halted"
    assert sum(h["status"] == "not_run" for h in result["hosts"].values()) == 5
    with pytest.raises(ValueError):
        await b.execute(p["id"], p["digest"], continuation=True)


@pytest.mark.asyncio
async def test_preview_errors_are_explicit_and_unready_canary_blocks(tmp_path):
    b, calls = manager(tmp_path)
    b.runtime.collect.return_value = {"readiness": {"maintenance_ready": False}}
    p = await b.preview("start_time_service", group="fleet")
    assert p["status"] == "blocked" and len(p["spec"]["preview_errors"]) == 6
    assert calls == []


@pytest.mark.asyncio
async def test_batch_scope_digest_expiry_and_revoked_action_fail_before_mutations(
    tmp_path,
):
    b, calls = manager(tmp_path)
    with pytest.raises(ValueError):
        await b.preview("start_time_service", hosts=["pc-0", "not-approved"])
    with pytest.raises(ValueError):
        await b.preview("start_time_service", group="fleet", wave_size=4)
    p = await b.preview("start_time_service", group="fleet")
    with pytest.raises(ValueError, match="digest"):
        await b.execute(p["id"], "wrong")
    b.config = replace(b.config, maintenance_actions=())
    with pytest.raises(ValueError, match="enabled"):
        await b.execute(p["id"], p["digest"])
    assert calls == []


@pytest.mark.asyncio
async def test_cancel_after_canary_blocks_remaining_hosts(tmp_path):
    b, calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet")
    await b.execute(p["id"], p["digest"])
    b.cancel(p["id"])
    assert b.get(p["id"])["status"] == "cancelled" and len(calls) == 1
    with pytest.raises(ValueError):
        await b.execute(p["id"], p["digest"], continuation=True)


@pytest.mark.asyncio
async def test_wave_concurrency_bound_and_cancellation_during_execution(tmp_path):
    b, calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet", wave_size=2)
    await b.execute(p["id"], p["digest"])
    active = peak = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(ident, digest):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        entered.set()
        await release.wait()
        active -= 1
        calls.append(ident)
        return {"id": ident, "status": "complete", "result": {"changed": True}}

    b.runtime.maintenance.execute.side_effect = held
    task = asyncio.create_task(b.execute(p["id"], p["digest"], continuation=True))
    await entered.wait()
    b.cancel(p["id"])
    release.set()
    r = await task
    assert peak <= 2 and r["status"] == "cancelled" and len(calls) <= 3


@pytest.mark.asyncio
async def test_expired_batch_never_executes(tmp_path):
    from remote_fleet_agent.storage import digest

    b, calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet")
    p["spec"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    p["digest"] = digest(p["spec"])
    b.save(p)
    with pytest.raises(ValueError, match="expired"):
        await b.execute(p["id"], p["digest"])
    assert calls == []


@pytest.mark.asyncio
async def test_cancelled_inflight_batch_is_not_replayable(tmp_path):
    b, _calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet")
    entered = asyncio.Event()

    async def held(*args):
        entered.set()
        await asyncio.sleep(120)

    b.runtime.maintenance.execute.side_effect = held
    task = asyncio.create_task(b.execute(p["id"], p["digest"]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = b.get(p["id"])
    assert (
        saved["status"] == "interrupted"
        and saved["hosts"]["pc-0"]["status"] == "unknown"
    )
    assert saved["hosts"]["pc-1"]["status"] == "not_run"
    with pytest.raises(ValueError):
        await b.execute(p["id"], p["digest"])


@pytest.mark.asyncio
async def test_duplicate_batch_execution_cannot_dispatch_twice(tmp_path):
    b, _calls = manager(tmp_path)
    p = await b.preview("start_time_service", group="fleet")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def held(ident, digest):
        entered.set()
        await release.wait()
        return {"id": ident, "status": "complete", "result": {"changed": True}}

    b.runtime.maintenance.execute.side_effect = held
    task = asyncio.create_task(b.execute(p["id"], p["digest"]))
    await entered.wait()
    with pytest.raises(ValueError, match="locked"):
        await b.execute(p["id"], p["digest"])
    release.set()
    assert (await task)["status"] == "awaiting_continuation"
    assert b.runtime.maintenance.execute.await_count == 1


@pytest.mark.asyncio
async def test_partial_preview_scope_is_visible_and_never_retried_implicitly(tmp_path):
    b, calls = manager(tmp_path)

    async def ready(host, operation):
        return {"readiness": {"maintenance_ready": host != "pc-5"}}

    b.runtime.collect.side_effect = ready
    p = await b.preview("start_time_service", group="fleet")
    assert list(p["spec"]["preview_errors"]) == ["pc-5"]
    await b.execute(p["id"], p["digest"])
    assert (await b.execute(p["id"], p["digest"], continuation=True))[
        "status"
    ] == "partial"
    assert len(calls) == 5
