import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.tools.readiness import assess, readiness


def report(admin=True, space=1024**3):
    rows = {
        "permissions": [{"administrator": admin}],
        "disk_space": [{"free_bytes": space}],
        "modules": [{"command": "Get-CimInstance", "available": True}],
        "cleanup": [
            {
                "journal_found": True,
                "task_found": True,
                "task_enabled": True,
                "completed": False,
                "expires_at": (datetime.now(UTC) + timedelta(days=3)).isoformat(),
            }
        ],
    }
    return {
        "sections": {
            n: {"status": "collected", "items": items} for n, items in rows.items()
        }
    }


def test_readiness_separates_collection_from_admin_space_and_expiry():
    assert assess(report())["state"] == "ready"
    assert assess(report(False))["state"] == "blocked"
    assert assess(report(space=1))["maintenance_ready"] is False
    r = report()
    r["sections"]["cleanup"]["items"][0]["expires_at"] = (
        datetime.now(UTC) - timedelta(days=1)
    ).isoformat()
    assert assess(r)["cleanup"] == "overdue"
    r["sections"]["cleanup"]["items"] = [{"journal_found": False}]
    assert assess(r)["cleanup"] == "unknown"
    r["sections"]["permissions"] = {"status": "unknown", "items": []}
    assert assess(r)["state"] == "unknown"


@pytest.mark.asyncio
async def test_readiness_only_uses_fixed_read_queries_and_client_literal():
    result = SimpleNamespace(
        exit_code=0,
        stderr="",
        stdout=json.dumps(
            {
                "status": "collected",
                "items": [{"journal_found": False}],
                "total": 1,
                "truncated": False,
            }
        ),
    )
    with patch(
        "remote_fleet_agent.tools.collect.run_powershell",
        AsyncMock(return_value=result),
    ) as remote:
        r = await readiness("conn", ["cleanup"], client="client';Write-Host BAD")
    script = remote.call_args.args[1]
    assert "client'';Write-Host BAD" in script
    for word in (
        "Start-Service",
        "Stop-Service",
        "Set-NetFirewall",
        "Add-WindowsCapability",
        "Clear-DnsClientCache -",
    ):
        assert word not in script
    assert (
        r["connection"] == "connected" and r["readiness"]["maintenance_ready"] is False
    )


@pytest.mark.parametrize("section", ["permissions", "modules", "disk_space", "cleanup"])
def test_fixed_readiness_queries_parse_in_powershell(section):
    import base64
    import os
    import shutil
    import subprocess

    from remote_fleet_agent.tools.readiness import QUERIES

    executable = os.environ.get("FLEET_TEST_PWSH") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell unavailable")
    query = QUERIES[section].replace("__ENGAGEMENT__", "'RemoteFleetAgent-example'")
    encoded = base64.b64encode(query.encode()).decode()
    parser = (
        "$tokens=$null;$errors=$null;$q=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
        + encoded
        + "'));$null=[Management.Automation.Language.Parser]::ParseInput($q,[ref]$tokens,[ref]$errors);if($errors.Count){$errors|Out-String;exit 1}"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", parser],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
