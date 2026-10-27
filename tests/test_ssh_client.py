import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from remote_fleet_agent.ssh_client import (
    build_encoded_command,
    connect,
    ps_quote,
    run_powershell,
)


def test_build_encoded_command_is_base64_powershell():
    cmd = build_encoded_command("Write-Output 'ok'")
    assert cmd.startswith("powershell -NoProfile -NonInteractive -EncodedCommand ")


def test_injection_attempt_is_neutralized_in_script_text():
    malicious = "'; Remove-Item C:\\ -Recurse -Force #"
    quoted = ps_quote(malicious)
    # the quoted value must escape embedded single quotes by doubling them,
    # so it can never close the string literal early
    assert quoted.count("''") >= 1
    assert not quoted.startswith("'; Remove-Item")
    assert quoted.startswith("'") and quoted.endswith("'")


@pytest.mark.asyncio
async def test_run_powershell_returns_result():
    fake_process = MagicMock()
    fake_process.stdout = "ok\n"
    fake_process.stderr = ""
    fake_process.exit_status = 0
    conn = MagicMock()
    conn.run = AsyncMock(return_value=fake_process)

    result = await run_powershell(conn, "Write-Output 'ok'")

    assert result.stdout == "ok\n"
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_connect_success_logs_ok(caplog):
    with (
        patch("remote_fleet_agent.ssh_client.asyncssh.connect", AsyncMock(return_value="CONN")),
        caplog.at_level(logging.INFO),
    ):
        conn = await connect("asset1.local", "/key/path", timeout=5)
    assert conn == "CONN"
    assert any("ssh.connect.ok" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_connect_retries_once_then_succeeds(caplog):
    calls = {"n": 0}

    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("refused")
        return "CONN"

    with (
        patch("remote_fleet_agent.ssh_client.asyncssh.connect", flaky),
        caplog.at_level(logging.INFO),
    ):
        conn = await connect("asset1.local", "/key/path", timeout=5)
    assert conn == "CONN"
    assert calls["n"] == 2
    assert any("ssh.connect.retry" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_connect_fails_both_attempts_raises_and_logs(caplog):
    async def always_fails(*a, **k):
        raise OSError("refused")

    with (
        patch("remote_fleet_agent.ssh_client.asyncssh.connect", always_fails),
        pytest.raises(OSError),
        caplog.at_level(logging.INFO),
    ):
        await connect("asset1.local", "/key/path", timeout=5)
    assert any("ssh.connect.failed" in r.message for r in caplog.records)
