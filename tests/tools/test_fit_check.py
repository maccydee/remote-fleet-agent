from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.fit_check import fit_check


@pytest.mark.asyncio
async def test_fit_check_true_when_space_available():
    size_result = CommandResult(stdout="5000000000", stderr="", exit_code=0)
    free_result = CommandResult(stdout="10000000000", stderr="", exit_code=0)
    with patch(
        "remote_fleet_agent.tools.fit_check.run_powershell",
        AsyncMock(side_effect=[size_result, free_result]),
    ):
        result = await fit_check("CONN", source_path="D:\\Share\\Old", dest_drive="E")
    assert result.fits is True
    assert result.source_bytes == 5_000_000_000
    assert result.dest_free_bytes == 10_000_000_000


@pytest.mark.asyncio
async def test_fit_check_false_when_not_enough_space():
    size_result = CommandResult(stdout="20000000000", stderr="", exit_code=0)
    free_result = CommandResult(stdout="10000000000", stderr="", exit_code=0)
    with patch(
        "remote_fleet_agent.tools.fit_check.run_powershell",
        AsyncMock(side_effect=[size_result, free_result]),
    ):
        result = await fit_check("CONN", source_path="D:\\Share\\Old", dest_drive="E")
    assert result.fits is False


@pytest.mark.asyncio
async def test_fit_check_raises_on_inaccessible_source():
    error_result = CommandResult(stdout="", stderr="Access to the path is denied", exit_code=1)
    with (
        patch(
            "remote_fleet_agent.tools.fit_check.run_powershell",
            AsyncMock(return_value=error_result),
        ),
        pytest.raises(RuntimeError, match="Access"),
    ):
        await fit_check("CONN", source_path="D:\\NoAccess", dest_drive="E")
