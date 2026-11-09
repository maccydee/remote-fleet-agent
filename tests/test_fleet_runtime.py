from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.config import FleetConfig, FleetHost
from remote_fleet_agent.runtime import FleetRuntime
from remote_fleet_agent.server import build_server


def config(tmp_path):
    known = tmp_path / "known_hosts"
    known.write_text("verified-fixture")
    return FleetConfig(
        "example", known, (FleetHost("pc", "Admin@192.0.2.1", ("office",)),)
    )


@pytest.mark.asyncio
async def test_scope_and_sections_are_rejected_before_connection(tmp_path):
    runtime = FleetRuntime(config(tmp_path), "/fake-key", tmp_path)
    with patch.object(runtime.pool, "get", AsyncMock()) as get:
        with pytest.raises(ValueError, match="approved scope"):
            await runtime.collect("outside", "inventory")
        with pytest.raises(ValueError):
            await runtime.collect("pc", "inventory", ["arbitrary-script"])
        with pytest.raises(ValueError):
            runtime.start("inventory", hosts=["pc", "outside"])
        get.assert_not_called()


@pytest.mark.asyncio
async def test_connector_receives_pinned_host_file(tmp_path):
    runtime = FleetRuntime(config(tmp_path), "/fake-key", tmp_path)
    with patch(
        "remote_fleet_agent.runtime.connect", AsyncMock(return_value="conn")
    ) as connect:
        assert await runtime.connection("pc") == "conn"
    assert connect.call_args.args == ("Admin@192.0.2.1", "/fake-key")
    assert connect.call_args.kwargs["known_hosts"] == str(tmp_path / "known_hosts")


@pytest.mark.asyncio
async def test_retry_rechecks_approval(tmp_path):
    runtime = FleetRuntime(config(tmp_path), "/fake-key", tmp_path)
    runtime.jobs._save(
        {
            "id": "b" * 32,
            "client": "example",
            "operation": "inventory",
            "sections": ["os"],
            "status": "failed",
            "hosts": {"removed-pc": {"status": "failed"}},
        }
    )
    with pytest.raises(ValueError, match="approved scope"):
        runtime.retry("b" * 32)


@pytest.mark.asyncio
async def test_server_cannot_opt_back_into_file_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "0")
    monkeypatch.delenv("REMOTE_FLEET_AGENT_CONFIG", raising=False)
    server = build_server("/fake-key", job_dir=tmp_path)
    names = {tool.name for tool in await server.list_tools()}
    assert not names & {"quarantine_move", "dry_run_copy", "gdpr_scan"}
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError, match="REMOTE_FLEET_AGENT_CONFIG"):
        await server.call_tool("inventory", {"host": "anywhere"})
