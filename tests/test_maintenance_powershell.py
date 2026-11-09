"""Exercise generated PowerShell against in-process cmdlet doubles, never Windows services."""

import json
import os
import shutil
import subprocess

import pytest

from remote_fleet_agent.maintenance import change_script, probe_script

PWSH = os.environ.get("FLEET_TEST_PWSH") or shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell is unavailable")

DOUBLES = r"""
$ErrorActionPreference='Stop'
$global:service=[pscustomobject]@{Status='Stopped';StartType='Manual'}
$global:service | Add-Member ScriptMethod WaitForStatus {param($status,$timeout)}
function Get-Service {param($Name,$ErrorAction) return $global:service}
function Start-Service {param($Name,$ErrorAction) $global:service.Status='Running'}
function Stop-Service {param($Name,$ErrorAction) $global:service.Status='Stopped'}
function Clear-DnsClientCache {param($ErrorAction) $global:cleared=$true}
function Get-DnsClientCache {param($ErrorAction) if(-not $global:cleared){throw 'Cache was not cleared'}; return @()}
"""


def run(script, setup=""):
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            DOUBLES + setup + "\n" + script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,  # Includes PowerShell process startup on a loaded CI/developer host.
    )
    return result


@pytest.mark.parametrize("action", ["start_print_spooler", "start_time_service"])
def test_service_start_and_rollback_execute_as_expected(action):
    before = {"status": "Stopped", "start_type": "Manual"}
    result = run(change_script(action, before))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "changed": True,
        "status": "Running",
        "start_type": "Manual",
    }
    result = run(
        change_script(action, before, rollback=True), "$global:service.Status='Running'"
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "changed": True,
        "status": "Stopped",
        "start_type": "Manual",
    }


def test_running_service_is_noop_and_changed_state_is_rejected():
    result = run(
        change_script(
            "start_print_spooler", {"status": "Running", "start_type": "Manual"}
        ),
        "$global:service.Status='Running'",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["changed"] is False
    result = run(
        change_script(
            "start_print_spooler", {"status": "Stopped", "start_type": "Manual"}
        ),
        "$global:service.Status='Running'",
    )
    assert result.returncode != 0
    assert "State changed" in result.stderr


def test_startup_type_change_and_rollback_state_drift_are_rejected():
    before = {"status": "Stopped", "start_type": "Manual"}
    result = run(
        change_script("start_print_spooler", before),
        "$global:service.StartType='Disabled'",
    )
    assert result.returncode != 0
    result = run(change_script("start_print_spooler", before, rollback=True))
    assert result.returncode != 0
    assert "State changed" in result.stderr


@pytest.mark.parametrize(
    "action", ["start_print_spooler", "start_time_service", "clear_dns_cache"]
)
def test_preview_scripts_are_valid_and_read_only(action):
    result = run(probe_script(action))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data == (
        {"available": True}
        if action == "clear_dns_cache"
        else {"status": "Stopped", "start_type": "Manual"}
    )


def test_dns_clear_runs_and_measures_afterward():
    result = run(change_script("clear_dns_cache", {"available": True}))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["changed"] is True
    assert data["cache_entries_after"] == 0
