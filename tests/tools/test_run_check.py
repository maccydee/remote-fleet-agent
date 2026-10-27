from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.run_check import run_check


@pytest.mark.asyncio
async def test_run_check_parses_json_result():
    fake = CommandResult(stdout='{"patched": true, "pending": 2}', stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.run_check.run_powershell", AsyncMock(return_value=fake)):
        result = await run_check("CONN", "patch_status")
    assert result == {"patched": True, "pending": 2}


@pytest.mark.asyncio
async def test_run_check_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown check"):
        await run_check("CONN", "not_a_real_check")


@pytest.mark.asyncio
async def test_run_check_nonzero_exit_raises():
    fake = CommandResult(stdout="", stderr="Access denied", exit_code=1)
    with (
        patch("remote_fleet_agent.tools.run_check.run_powershell", AsyncMock(return_value=fake)),
        pytest.raises(RuntimeError, match="Access denied"),
    ):
        await run_check("CONN", "patch_status")
