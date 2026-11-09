"""Fixed read-only readiness probes. No feature installation or external traffic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from remote_fleet_agent.ssh_client import ps_quote
from remote_fleet_agent.tools.collect import collect

QUERIES = {
    "permissions": "$p=New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent()); [pscustomobject]@{administrator=$p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator);powershell_major=$PSVersionTable.PSVersion.Major}",
    "modules": "foreach($name in @('Get-CimInstance','Get-NetFirewallProfile','Get-LocalUser','Get-MpComputerStatus','Get-BitLockerVolume','Clear-DnsClientCache')){[pscustomobject]@{command=$name;available=[bool](Get-Command $name -ErrorAction SilentlyContinue)}}",
    "disk_space": "$d=Get-CimInstance Win32_LogicalDisk -Filter ('DeviceID='+[char]39+$env:SystemDrive+[char]39); [pscustomobject]@{free_bytes=$d.FreeSpace;size_bytes=$d.Size}",
    "cleanup": "$p=Join-Path $env:ProgramData __ENGAGEMENT__; $f=Join-Path $p 'state.json'; if(Test-Path -LiteralPath $f){$s=Get-Content -LiteralPath $f -Raw | ConvertFrom-Json; $t=Get-ScheduledTask -TaskName $s.taskName -ErrorAction SilentlyContinue; [pscustomobject]@{journal_found=$true;expires_at=$s.expiresAtUtc;completed=[bool]$s.completed;task_found=[bool]$t;task_enabled=[bool]$t.Settings.Enabled}}else{[pscustomobject]@{journal_found=$false}}",
}


def assess(report):
    sections = report["sections"]

    def rows(name):
        s = sections.get(name, {})
        return s.get("items", []) if s.get("status") == "collected" else []

    permissions = rows("permissions")
    disks = rows("disk_space")
    admin = permissions[0].get("administrator") if permissions else None
    free = disks[0].get("free_bytes") if disks else None
    space = type(free) in (int, float) and free >= 256 * 1024**2
    notes = []
    if admin is not True:
        notes.append(
            "Administrator access is missing or unknown; full collection and maintenance may fail."
        )
    if not space:
        notes.append(
            "System-drive headroom is below 256 MiB or unknown; investigate before maintenance."
        )
    missing = [
        r.get("command") for r in rows("modules") if r.get("available") is not True
    ]
    if missing:
        notes.append(
            "Some optional query commands are unavailable: " + ", ".join(missing)
        )
    cleanup = rows("cleanup")
    cleanup_state = "unknown"
    if cleanup and cleanup[0].get("journal_found"):
        r = cleanup[0]
        try:
            expiry = datetime.fromisoformat(r["expires_at"])
            if expiry.tzinfo is None:
                raise ValueError("expiry needs a timezone")
            if r.get("completed"):
                cleanup_state = "completed"
            elif not r.get("task_found") or not r.get("task_enabled"):
                cleanup_state = "attention"
            elif expiry <= datetime.now(UTC):
                cleanup_state = "overdue"
            elif expiry < datetime.now(UTC) + timedelta(days=1):
                cleanup_state = "expires_soon"
            else:
                cleanup_state = "scheduled"
        except (ValueError, TypeError, KeyError):
            cleanup_state = "unknown"
    if cleanup_state != "scheduled":
        notes.append(
            "Assessment cleanup needs review: "
            + cleanup_state
            + ". Legacy/custom installers may use a different journal location."
        )
    unknown = any(s.get("status") != "collected" for s in sections.values())
    state = (
        "blocked"
        if admin is False
        else "unknown"
        if admin is None or not disks
        else "needs_attention"
        if notes or unknown
        else "ready"
    )
    return {
        "state": state,
        "maintenance_ready": admin is True and space,
        "cleanup": cleanup_state,
        "missing_commands": missing,
        "notes": notes,
        "note": "Readiness is a point-in-time access check, not a security or compliance verdict.",
    }


async def readiness(conn, sections=None, timeout=30, *, client="example"):
    queries = {
        n: q.replace("__ENGAGEMENT__", ps_quote("RemoteFleetAgent-" + client))
        for n, q in QUERIES.items()
    }
    report = await collect(conn, "readiness", queries, sections, timeout, limit=20)
    report["connection"] = "connected"
    report["readiness"] = assess(report)
    return report
