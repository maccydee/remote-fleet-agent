import pytest

from remote_fleet_agent.server import build_server


@pytest.mark.asyncio
async def test_exposed_tools_match_expected_safe_set():
    server = build_server(key_path="/dev/null")
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "describe_capabilities",
        "readiness",
        "fleet_status",
        "export_evidence",
        "list_manual_evidence",
        "add_manual_evidence",
        "update_manual_evidence",
        "preview_retention",
        "apply_retention",
        "list_hosts",
        "inventory",
        "diagnostics",
        "run_check",
        "fit_check",
        "start_job",
        "get_job",
        "get_host_result",
        "list_jobs",
        "retry_job",
        "cancel_job",
        "compare_inventory",
    }
