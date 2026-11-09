import asyncio
import json
import os
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.config import FleetConfig, FleetHost
from remote_fleet_agent.maintenance import MaintenanceManager, now
from remote_fleet_agent.server import build_server


def manager(tmp_path, actions=("start_print_spooler", "clear_dns_cache")):
    known = tmp_path / "known"
    known.write_text("verified")
    config = FleetConfig(
        "test",
        known,
        (FleetHost("pc", "Engineer@192.0.2.1"),),
        maintenance_actions=actions,
    )
    return MaintenanceManager(config, AsyncMock(return_value="connection"), tmp_path)


def answer(value):
    return SimpleNamespace(exit_code=0, stdout=json.dumps(value), stderr="")


STOPPED = {"status": "Stopped", "start_type": "Manual"}
STARTED = {"status": "Running", "start_type": "Manual", "changed": True}


@pytest.mark.asyncio
async def test_defaults_and_unapproved_scope_never_connect(tmp_path):
    m = manager(tmp_path, ())
    with pytest.raises(ValueError, match="not enabled"):
        await m.preview("pc", "clear_dns_cache")
    m = manager(tmp_path)
    with pytest.raises(ValueError, match="approved scope"):
        await m.preview("outside", "start_print_spooler")
    with pytest.raises(ValueError, match="not enabled"):
        await m.preview("pc", "arbitrary_powershell")
    m.connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_execute_verify_rollback_and_replay(tmp_path):
    m = manager(tmp_path)
    outputs = [STOPPED, STARTED, {**STOPPED, "changed": True}]
    with patch(
        "remote_fleet_agent.maintenance.run_powershell",
        AsyncMock(side_effect=[answer(v) for v in outputs]),
    ) as remote:
        plan = await m.preview("pc", "start_print_spooler")
        assert remote.await_count == 1
        assert "Start-Service" not in remote.call_args.args[1]
        assert m.get(plan["id"]) == plan
        done = await m.execute(plan["id"], plan["digest"])
        assert done["status"] == "complete"
        assert done["before"] == STOPPED
        with pytest.raises(ValueError, match="already used"):
            await m.execute(plan["id"], plan["digest"])
        restored = await m.execute(plan["id"], plan["digest"], rollback=True)
        assert restored["status"] == "rolled_back"
        with pytest.raises(ValueError, match="eligible"):
            await m.execute(plan["id"], plan["digest"], rollback=True)
        assert remote.await_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [
        {"status": "Stopped", "start_type": "Disabled"},
        {"status": "StartPending", "start_type": "Manual"},
        {},
    ],
)
async def test_unstable_or_disabled_services_cannot_be_planned(tmp_path, state):
    m = manager(tmp_path)
    with (
        patch(
            "remote_fleet_agent.maintenance.run_powershell",
            AsyncMock(return_value=answer(state)),
        ),
        pytest.raises(ValueError, match="stable"),
    ):
        await m.preview("pc", "start_print_spooler")
    assert not list(m.folder.glob("*.json"))


@pytest.mark.asyncio
async def test_noop_service_start_cannot_stop_existing_service(tmp_path):
    m = manager(tmp_path)
    running = {"status": "Running", "start_type": "Automatic"}
    with patch.object(
        m, "_run", AsyncMock(side_effect=[running, {**running, "changed": False}])
    ):
        plan = await m.preview("pc", "start_print_spooler")
        assert (await m.execute(plan["id"], plan["digest"]))["status"] == "complete"
        with pytest.raises(ValueError, match="eligible"):
            await m.execute(plan["id"], plan["digest"], rollback=True)


@pytest.mark.asyncio
async def test_dns_clear_records_verification_without_claiming_empty_cache(tmp_path):
    m = manager(tmp_path)
    with patch.object(
        m,
        "_run",
        AsyncMock(
            side_effect=[
                {"available": True},
                {"changed": True, "cache_entries_after": 3},
            ]
        ),
    ):
        p = await m.preview("pc", "clear_dns_cache")
        done = await m.execute(p["id"], p["digest"])
        assert done["status"] == "complete"
        assert done["result"]["cache_entries_after"] == 3
        with pytest.raises(ValueError, match="eligible"):
            await m.execute(p["id"], p["digest"], rollback=True)


@pytest.mark.asyncio
async def test_digest_expiry_and_tampering_fail_without_connection(tmp_path):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")
    with patch.object(m, "_run", AsyncMock()) as remote:
        with pytest.raises(ValueError, match="digest"):
            await m.execute(p["id"], "wrong")
        with (
            patch(
                "remote_fleet_agent.maintenance.now",
                return_value=now() + timedelta(minutes=11),
            ),
            pytest.raises(ValueError, match="expired"),
        ):
            await m.execute(p["id"], p["digest"])
        p["before"]["status"] = "Running"
        m._save(p)
        with pytest.raises(ValueError, match="integrity"):
            await m.execute(p["id"], p["digest"])
        remote.assert_not_called()


@pytest.mark.asyncio
async def test_current_approval_and_target_are_checked_again(tmp_path):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")
    m.config = replace(m.config, maintenance_actions=())
    with pytest.raises(ValueError, match="not enabled"):
        await m.execute(p["id"], p["digest"])
    m.config = replace(
        m.config,
        maintenance_actions=("start_print_spooler",),
        hosts=(FleetHost("pc", "Engineer@192.0.2.2"),),
    )
    with pytest.raises(ValueError, match="target changed"):
        await m.execute(p["id"], p["digest"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), ConnectionError("disconnected"), ValueError("malformed JSON")],
)
async def test_uncertain_outcomes_are_saved_and_never_retried(tmp_path, failure):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")

    async def uncertain(*args):
        assert m.get(p["id"])["status"] == "executing"
        raise failure

    with patch.object(m, "_run", side_effect=uncertain) as remote:
        done = await m.execute(p["id"], p["digest"])
        assert done["status"] == "unknown"
        assert m.get(p["id"])["status"] == "unknown"
        with pytest.raises(ValueError, match="already used"):
            await m.execute(p["id"], p["digest"])
        assert remote.call_count == 1


@pytest.mark.asyncio
async def test_failed_verification_preserves_uncertainty_and_blocks_another_change(
    tmp_path,
):
    m = manager(tmp_path)
    with patch.object(
        m, "_run", AsyncMock(side_effect=[STOPPED, {"status": "Stopped"}])
    ):
        p = await m.preview("pc", "start_print_spooler")
        assert (await m.execute(p["id"], p["digest"]))["status"] == "unknown"
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        another = await m.preview("pc", "start_print_spooler")
        with pytest.raises(ValueError, match="uncertain work"):
            await m.execute(another["id"], another["digest"])


@pytest.mark.asyncio
async def test_cancellation_preserves_uncertainty(tmp_path):
    m = manager(tmp_path)
    with patch.object(
        m, "_run", AsyncMock(side_effect=[STOPPED, asyncio.CancelledError()])
    ):
        p = await m.preview("pc", "start_print_spooler")
        with pytest.raises(asyncio.CancelledError):
            await m.execute(p["id"], p["digest"])
        assert m.get(p["id"])["status"] == "unknown"


@pytest.mark.asyncio
async def test_save_failure_prevents_remote_change_and_lock_blocks_duplicate_execution(
    tmp_path,
):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")
    with (
        patch.object(m, "_save", side_effect=OSError("disk full")),
        patch.object(m, "_run", AsyncMock()) as remote,
    ):
        with pytest.raises(OSError, match="disk full"):
            await m.execute(p["id"], p["digest"])
        remote.assert_not_called()
    m._path(p["id"]).with_suffix(".lock").touch()
    with pytest.raises(ValueError, match="locked"):
        await m.execute(p["id"], p["digest"])
    if os.name != "nt":
        assert m._path(p["id"]).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="invalid"):
        m.get("../../outside")


@pytest.mark.asyncio
async def test_legacy_mcp_cannot_bypass_workspace_human_approval(tmp_path, monkeypatch):
    monkeypatch.delenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", raising=False)
    m = manager(tmp_path)
    server = build_server("/fake", config=m.config, job_dir=tmp_path)
    tools = {t.name: t for t in await server.list_tools()}
    assert "preview_maintenance" in tools
    assert (
        not {
            "execute_maintenance",
            "rollback_maintenance",
            "run_batch_canary",
            "continue_batch",
        }
        & tools.keys()
    )
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "1")
    server = build_server("/fake", config=m.config, job_dir=tmp_path)
    assert "execute_maintenance" not in {t.name for t in await server.list_tools()}


@pytest.mark.parametrize(
    "actions", [True, ["arbitrary"], ["clear_dns_cache", "clear_dns_cache"], [1]]
)
def test_bad_config_action_lists_fail_closed(tmp_path, actions):
    (tmp_path / "known").write_text("verified")
    file = tmp_path / "fleet.json"
    file.write_text(
        json.dumps(
            {"client": "test", "known_hosts": "known", "maintenance_actions": actions}
        )
    )
    with pytest.raises(ValueError, match="maintenance_actions"):
        FleetConfig.load(file)


@pytest.mark.asyncio
async def test_changed_host_keys_invalidate_preview(tmp_path):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")
    m.config.known_hosts.write_text("changed")
    with patch.object(m, "_run", AsyncMock()) as remote:
        with pytest.raises(ValueError, match="host-key"):
            await m.execute(p["id"], p["digest"])
        remote.assert_not_called()


@pytest.mark.asyncio
async def test_two_plans_for_same_host_cannot_execute_concurrently(tmp_path):
    m = manager(tmp_path)
    with patch.object(m, "_run", AsyncMock(return_value=STOPPED)):
        p = await m.preview("pc", "start_print_spooler")
        q = await m.preview("pc", "start_print_spooler")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release(*args):
        entered.set()
        await release.wait()
        return STARTED

    with patch.object(m, "_run", side_effect=wait_for_release):
        task = asyncio.create_task(m.execute(p["id"], p["digest"]))
        await entered.wait()
        with pytest.raises(ValueError, match="maintenance in progress"):
            await m.execute(q["id"], q["digest"])
        release.set()
        assert (await task)["status"] == "complete"
    assert not list(m.folder.glob("*.lock"))


@pytest.mark.asyncio
async def test_manager_rechecks_global_audit_only_before_any_endpoint_use(
    tmp_path, monkeypatch
):
    m = manager(tmp_path)
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "1")
    with pytest.raises(ValueError, match="audit-only"):
        await m.preview("pc", "clear_dns_cache")
    m.connection.assert_not_awaited()
