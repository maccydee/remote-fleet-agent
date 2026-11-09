import json
from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.diagnostics import diagnostics
from remote_fleet_agent.tools.inventory import inventory


@pytest.mark.asyncio
async def test_missing_feature_is_unknown_and_does_not_discard_other_sections():
    responses = [
        CommandResult(
            json.dumps(
                {
                    "status": "unknown",
                    "error": "BitLocker module unavailable",
                    "items": [],
                }
            ),
            "",
            0,
        ),
        CommandResult(
            json.dumps(
                {
                    "status": "collected",
                    "items": [{"DeviceID": "C:"}],
                    "total": 1,
                    "truncated": False,
                }
            ),
            "",
            0,
        ),
    ]
    with patch(
        "remote_fleet_agent.tools.collect.run_powershell",
        AsyncMock(side_effect=responses),
    ):
        report = await inventory("conn", ["encryption", "disks"])
    assert report["status"] == "partial"
    assert report["sections"]["encryption"]["status"] == "unknown"
    assert report["sections"]["disks"]["items"][0]["DeviceID"] == "C:"


@pytest.mark.asyncio
async def test_query_failure_timeout_and_malformed_output_are_not_passes():
    for outcome in [
        CommandResult("", "denied", 1),
        CommandResult("not json", "", 0),
        CommandResult("{}", "", 0),
        TimeoutError(),
        OSError("connection lost"),
    ]:
        mock = (
            AsyncMock(side_effect=outcome)
            if isinstance(outcome, Exception)
            else AsyncMock(return_value=outcome)
        )
        with patch("remote_fleet_agent.tools.collect.run_powershell", mock):
            report = await diagnostics("conn", ["storage"], timeout=1)
        assert report["status"] == "partial"
        assert report["sections"]["storage"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_invalid_section_never_executes_a_command():
    with patch(
        "remote_fleet_agent.tools.collect.run_powershell", AsyncMock()
    ) as command:
        for sections in [[], ["os", "os"], ["os';Remove-Item"]]:
            with pytest.raises(ValueError):
                await inventory("conn", sections)
        command.assert_not_called()


@pytest.mark.asyncio
async def test_event_collection_is_bounded_and_omits_messages():
    fake = CommandResult(
        '{"status":"collected","items":[],"total":0,"truncated":false}', "", 0
    )
    with patch(
        "remote_fleet_agent.tools.collect.run_powershell", AsyncMock(return_value=fake)
    ) as command:
        report = await diagnostics("conn", ["system_errors"])
    script = command.call_args.args[1]
    assert "-MaxEvents 50" in script
    assert "Select-Object TimeCreated,Id,ProviderName,LevelDisplayName" in script
    assert "ExpandProperty Message" not in script
    assert report["status"] == "complete"
