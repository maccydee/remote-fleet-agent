import pytest

from remote_fleet_agent.server import build_server


@pytest.mark.asyncio
async def test_exposed_tools_match_expected_safe_set():
    server = build_server(key_path="/dev/null")
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"run_check", "fit_check", "dry_run_copy", "quarantine_move", "gdpr_scan"}
