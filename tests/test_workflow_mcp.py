import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from remote_fleet_agent import server as server_module


def unpack(result):
    assert not result.is_error
    return (
        result.structured_content
        if result.structured_content is not None
        else json.loads(result.content[0].text)
    )


@pytest.mark.asyncio
async def test_stdio_workflow_tools_without_any_endpoint_connections(tmp_path):
    known = tmp_path / "known"
    known.write_text("fixture-only-no-live-hosts")
    config = tmp_path / "fleet.json"
    config.write_text(
        json.dumps(
            {
                "client": "fixture",
                "known_hosts": str(known),
                "hosts": [],
                "maintenance_actions": ["start_time_service"],
            }
        )
    )
    script = """
import asyncio
import remote_fleet_agent.runtime as runtime
from remote_fleet_agent.server import main
async def forbidden(*args,**kwargs):
 raise RuntimeError('Test forbids all endpoint connections')
runtime.connect=forbidden
asyncio.run(main())
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(server_module.__file__).resolve().parents[1]),
        PYTHONDONTWRITEBYTECODE="1",
        REMOTE_FLEET_AGENT_CONFIG=str(config),
        REMOTE_FLEET_AGENT_KEY_PATH="/not-used",
        REMOTE_FLEET_AGENT_JOB_DIR=str(tmp_path / "jobs"),
    )
    env.pop("REMOTE_FLEET_AGENT_AUDIT_ONLY", None)
    params = StdioServerParameters(command=sys.executable, args=["-c", script], env=env)
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == 26
        empty = unpack(await session.call_tool("fleet_status", {}))
        assert empty["hosts"] == []
        added = unpack(
            await session.call_tool(
                "add_manual_evidence",
                {
                    "asset": "NAS",
                    "category": "nas",
                    "control": "2500",
                    "reviewer": "Test operator",
                },
            )
        )
        assert added["revision"] == 1
        summary = unpack(await session.call_tool("fleet_status", {}))
        assert (
            "note" not in summary["manual"][0] and "history" not in summary["manual"][0]
        )
        export = unpack(
            await session.call_tool(
                "export_evidence", {"kind": "dashboard", "shareable": True}
            )
        )
        assert Path(export["path"]).exists()
        preview = unpack(await session.call_tool("preview_retention", {}))
        assert preview["files"] == []
        applied = unpack(
            await session.call_tool(
                "apply_retention",
                {"plan_id": preview["id"], "expected_digest": preview["digest"]},
            )
        )
        assert applied["status"] == "complete" and applied["deleted"] == []
        assert (await session.call_tool("readiness", {"host": "outside"})).is_error
        assert (
            await session.call_tool(
                "preview_batch",
                {"action": "start_time_service", "hosts": ["outside"]},
            )
        ).is_error
