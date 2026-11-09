from __future__ import annotations

import json

from remote_fleet_agent.ssh_client import run_powershell

# Each check is a self-contained PowerShell one-liner that ends by emitting
# JSON, so every check returns the same shape regardless of what it queries.
CHECKS: dict[str, str] = {
    "patch_status": (
        # PSWindowsUpdate is not an in-box module. Never turn its absence into a pass.
        "$cmd = Get-Command Get-WindowsUpdate -ErrorAction SilentlyContinue; "
        "if ($null -eq $cmd) { "
        "@{assessment='unknown'; pending=$null; "
        "reason='PSWindowsUpdate unavailable; review Windows Update and installed software manually'; "
        "recentHotfixes=@(Get-HotFix | Sort-Object InstalledOn -Descending | "
        "Select-Object -First 10 HotFixID, InstalledOn)} | ConvertTo-Json -Depth 5 -Compress "
        "} else { $updates = @(Get-WindowsUpdate -ErrorAction Stop); "
        "@{assessment='evidence_only'; pending=$updates.Count; "
        "reason='Pending count does not establish application patching or the 14-day requirement'} "
        "| ConvertTo-Json -Compress }"
    ),
    "av_status": (
        "$s = Get-MpComputerStatus; "
        "@{realTimeProtectionEnabled = $s.RealTimeProtectionEnabled; "
        "antivirusSignatureAge = $s.AntivirusSignatureAge} | ConvertTo-Json -Compress"
    ),
    "firewall_status": (
        "Get-NetFirewallProfile | Select-Object Name, Enabled | ConvertTo-Json -Compress"
    ),
    "local_admins": (
        "Get-LocalGroupMember -Group 'Administrators' | "
        "Select-Object Name, ObjectClass | ConvertTo-Json -Compress"
    ),
}


async def run_check(conn, check_name: str) -> dict:
    if check_name not in CHECKS:
        raise ValueError(f"unknown check '{check_name}', expected one of {sorted(CHECKS)}")
    result = await run_powershell(conn, "$ErrorActionPreference = 'Stop'; " + CHECKS[check_name])
    if result.exit_code != 0:
        raise RuntimeError(
            result.stderr or f"check '{check_name}' failed with exit {result.exit_code}"
        )
    return json.loads(result.stdout)
