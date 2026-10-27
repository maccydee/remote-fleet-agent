from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.file_ops import dry_run_copy, quarantine_move

ROBOCOPY_SUMMARY = """
-------------------------------------------------------------------------------
                 Total    Copied   Skipped  Mismatch    FAILED    Extras
    Dirs :        10        10         0         0         0         0
   Files :       120       115         5         0         0         0
   Bytes :   1.2 g     1.1 g     0.1 g         0         0         0
"""


@pytest.mark.asyncio
async def test_dry_run_copy_parses_summary():
    fake = CommandResult(stdout=ROBOCOPY_SUMMARY, stderr="", exit_code=1)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)):
        plan = await dry_run_copy("CONN", source="D:\\Share\\Old", dest="D:\\Share\\New")
    assert plan.files_total == 120
    assert plan.files_to_copy == 115
    assert plan.files_skipped == 5


@pytest.mark.asyncio
async def test_dry_run_copy_empty_source():
    empty = ROBOCOPY_SUMMARY.replace("120       115         5", "0         0         0")
    fake = CommandResult(stdout=empty, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)):
        plan = await dry_run_copy("CONN", source="D:\\Share\\Empty", dest="D:\\Share\\New")
    assert plan.files_total == 0


@pytest.mark.asyncio
async def test_dry_run_copy_error_exit_raises():
    fake = CommandResult(stdout="", stderr="ERROR 5: Access is denied.", exit_code=16)
    with (
        patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)),
        pytest.raises(RuntimeError, match="Access is denied"),
    ):
        await dry_run_copy("CONN", source="D:\\NoAccess", dest="D:\\Share\\New")


@pytest.mark.asyncio
async def test_quarantine_move_builds_same_drive_path():
    fake = CommandResult(stdout="", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)) as mock_run, \
         patch("remote_fleet_agent.tools.file_ops._utc_timestamp", return_value="20261027T140000Z"):
        result = await quarantine_move("CONN", paths=["D:\\Share\\old.docx"])
    assert result.quarantine_dir == "D:\\_quarantine\\20261027T140000Z"
    script = mock_run.call_args.args[1]
    assert "D:\\_quarantine\\20261027T140000Z" in script


@pytest.mark.asyncio
async def test_quarantine_move_rejects_mixed_drives():
    with pytest.raises(ValueError, match="same volume"):
        await quarantine_move("CONN", paths=["D:\\Share\\a.docx", "E:\\Other\\b.docx"])


@pytest.mark.asyncio
async def test_quarantine_move_avoids_collision():
    fake = CommandResult(stdout="", stderr="", exit_code=0)
    exists_check = CommandResult(stdout="True", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell",
               AsyncMock(side_effect=[exists_check, fake])), \
         patch("remote_fleet_agent.tools.file_ops._utc_timestamp", return_value="20261027T140000Z"):
        result = await quarantine_move("CONN", paths=["D:\\Share\\old.docx"])
    assert result.quarantine_dir == "D:\\_quarantine\\20261027T140000Z-2"
