import asyncio
import http.client
import json
import os
import sys
import threading
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from remote_fleet_agent import support_mcp
from remote_fleet_agent.live_app import Application, seed_demo
from remote_fleet_agent.storage import read_json


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("MCP tests must never connect to real endpoints")

    monkeypatch.setattr("remote_fleet_agent.runtime.connect", forbidden)
    folder = seed_demo(tmp_path / "workspace")
    app = Application(folder, 0)
    owner_token = read_json(folder / ".state/owner-access.json")["token"]
    owner = app.service.store.authenticate(owner_token)
    _, token = app.service.store.add_operator(
        "AI support", "technician", {"northstar-demo": ["head-office"]}, owner
    )
    token_file = tmp_path / "technician-token"
    token_file.write_text(token)
    thread = threading.Thread(target=app.server.serve_forever, daemon=True)
    thread.start()
    yield app, owner_token, token_file
    app.server.shutdown()
    thread.join(timeout=3)
    app.close()


def request(app, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.server.server_port, timeout=10)
    conn.request(
        "POST" if body is not None else "GET",
        path,
        json.dumps(body) if body is not None else None,
        {"Origin": app.url, "Content-Type": "application/json", **(headers or {})},
    )
    response = conn.getresponse()
    raw = response.read()
    result = response.status, dict(response.getheaders()), json.loads(raw)
    conn.close()
    return result


def unpack(result):
    assert not result.is_error, result.content
    return (
        result.structured_content
        if result.structured_content is not None
        else json.loads(result.content[0].text)
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://localhost:8877",
        "http://127.0.0.1:8877/other",
        "http://user@127.0.0.1:8877",
        "http://127.0.0.1:8877?x=y",
    ],
)
def test_support_bridge_is_loopback_origin_only(tmp_path, url):
    with pytest.raises(ValueError):
        support_mcp.WorkspaceClient(url, tmp_path / "unused")


async def test_real_stdio_support_workflow_requires_browser_engineer_decision(
    workspace,
):
    app, owner_token, token_file = workspace
    status, headers, _ = request(app, "/api/login", {"token": owner_token})
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    _, _, boot = request(app, "/api/bootstrap", headers={"Cookie": cookie})
    owner_headers = {"Cookie": cookie, "X-Fleet-CSRF": boot["csrf"]}
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(support_mcp.__file__).resolve().parents[1]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "remote_fleet_agent.support_mcp",
            "--workspace-url",
            app.url,
            "--operator-token-file",
            str(token_file),
        ],
        env=env,
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        names = {t.name for t in (await session.list_tools()).tools}
        assert len(names) >= 7
        assert {"request_investigation", "propose_repair_plan", "execute_repair_plan"} <= names
        assert not any("approve" in n or "confirm" in n or "reject" in n for n in names)
        assert "support_workspace" in names
        metadata = unpack(await session.call_tool("support_workspace", {}))
        assert len(metadata["clients"]) == 1
        case = unpack(
            await session.call_tool(
                "open_support_case",
                {
                    "client_id": "northstar-demo",
                    "host": "windows-01",
                    "symptom": "Outlook will not open",
                    "profile": "outlook",
                },
            )
        )
        query = {"client_id": "northstar-demo", "case_id": case["id"]}
        case = unpack(
            await session.call_tool(
                "investigate_support_case", {**query, "revision": case["revision"]}
            )
        )
        for _ in range(20):
            case = unpack(await session.call_tool("get_support_case", query))
            if case["state"] != "investigating":
                break
            await asyncio.sleep(0.01)
        assert case["state"] == "investigated"
        case = unpack(
            await session.call_tool(
                "propose_support_repair",
                {
                    **query,
                    "revision": case["revision"],
                    "title": "Close the stalled Outlook instance",
                    "rationale": "Simulated process is unresponsive; cause not established",
                    "impact": "Would close only a reviewed process, potentially losing unsaved work",
                    "script": "Write-Output 'Simulation fixture only'",
                    "verification_script": 'Write-Output \'{"passed":true,"detail":"fixture"}\'',
                    "recovery": "No process rollback; ask user to reopen Outlook",
                },
            )
        )
        args = {
            **query,
            "revision": case["revision"],
            "expected_digest": case["repair"]["digest"],
        }
        assert (await session.call_tool("execute_support_repair", args)).is_error
        # A technician token cannot approve through the HTTP API either.
        bridge = support_mcp.WorkspaceClient(app.url, token_file)
        try:
            with pytest.raises(ValueError, match="role"):
                await asyncio.to_thread(
                    bridge.call,
                    f"/api/clients/northstar-demo/support-cases/{case['id']}/approve",
                    {"revision": case["revision"], "digest": case["repair"]["digest"]},
                )
        finally:
            bridge.close()
        status, _, case = request(
            app,
            f"/api/clients/northstar-demo/support-cases/{case['id']}/approve",
            {"revision": case["revision"], "digest": case["repair"]["digest"]},
            owner_headers,
        )
        assert status == 200 and case["state"] == "approved"
        case = unpack(
            await session.call_tool(
                "execute_support_repair",
                {
                    **query,
                    "revision": case["revision"],
                    "expected_digest": case["repair"]["digest"],
                },
            )
        )
        for _ in range(20):
            case = unpack(await session.call_tool("get_support_case", query))
            if case["state"] != "executing":
                break
            await asyncio.sleep(0.01)
        assert case["state"] == "checks_passed" and case["result"]["simulation"]
        assert (
            await session.call_tool(
                "execute_support_repair",
                {
                    **query,
                    "revision": case["revision"],
                    "expected_digest": case["repair"]["digest"],
                },
            )
        ).is_error


def test_owner_credential_is_refused_by_ai_bridge(workspace, tmp_path):
    app, owner_token, _ = workspace
    token = tmp_path / "owner-token"
    token.write_text(owner_token)
    client = support_mcp.WorkspaceClient(app.url, token)
    with pytest.raises(ValueError, match="technician"):
        client.call("/api/bootstrap")
    client.close()


def test_setup_profiles_include_no_token_contents_and_never_overwrite(
    workspace, tmp_path
):
    app, owner_token, token_file = workspace
    client = support_mcp.WorkspaceClient(app.url, token_file)
    folder = tmp_path / "generated"
    result = support_mcp.generate_configs(client, folder)
    assert len(result["providers"]) == 7 and not result["settings_modified"]
    for p in folder.rglob("*"):
        if p.is_file():
            assert token_file.read_text() not in p.read_text()
            assert owner_token not in p.read_text()
    with pytest.raises(ValueError, match="empty"):
        support_mcp.generate_configs(client, folder)


def test_support_bridge_honours_audit_only_without_loading_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "1")
    client = support_mcp.WorkspaceClient(
        "http://127.0.0.1:8877", tmp_path / "does-not-exist"
    )
    with pytest.raises(ValueError, match="Audit-only"):
        client.call("/api/clients/test/support-cases/test/execute", {})
