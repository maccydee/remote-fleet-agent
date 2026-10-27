from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.gdpr_scan import gdpr_scan


@pytest.mark.asyncio
async def test_gdpr_scan_returns_findings_without_content():
    remote_json = (
        '[{"path": "D:\\\\Share\\\\report.xlsx", "match_type": "ni_number", "count": 1}]'
    )
    fake = CommandResult(stdout=remote_json, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    assert len(findings) == 1
    assert findings[0].path.endswith("report.xlsx")
    assert findings[0].match_type == "ni_number"
    assert findings[0].count == 1


@pytest.mark.asyncio
async def test_gdpr_scan_no_matches_returns_empty_list():
    fake = CommandResult(stdout="[]", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    assert findings == []


@pytest.mark.asyncio
async def test_gdpr_scan_finding_never_carries_raw_text():
    remote_json = '[{"path": "D:\\\\x.docx", "match_type": "email", "count": 3}]'
    fake = CommandResult(stdout=remote_json, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    for f in findings:
        field_names = f.__dataclass_fields__.keys()
        assert "match_text" not in field_names
        assert "content" not in field_names


@pytest.mark.asyncio
async def test_gdpr_scan_exit_nonzero_raises():
    fake = CommandResult(stdout="", stderr="Access is denied", exit_code=1)
    with (
        patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)),
        pytest.raises(RuntimeError, match="Access is denied"),
    ):
        await gdpr_scan("CONN", root_path="D:\\NoAccess")
