from __future__ import annotations

from remote_fleet_agent.tools.collect import collect

QUERIES = {
    "performance": (
        "$os=Get-CimInstance Win32_OperatingSystem; $cpu=@(Get-CimInstance Win32_Processor); "
        "[pscustomobject]@{lastBoot=$os.LastBootUpTime;freeMemoryKB=$os.FreePhysicalMemory;"
        "totalMemoryKB=$os.TotalVisibleMemorySize;cpuLoadPercent=@($cpu | Select-Object -ExpandProperty LoadPercentage)}"
    ),
    "automatic_services_stopped": "Get-CimInstance Win32_Service -Filter \"StartMode='Auto' AND State<>'Running'\" | Select-Object Name,DisplayName,State,StartMode,ExitCode",
    "system_errors": (
        "$eventErrors=@(); $events=@(Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;"
        "StartTime=(Get-Date).AddHours(-24)} -MaxEvents 50 -ErrorAction SilentlyContinue -ErrorVariable eventErrors); "
        "if(@($eventErrors | Where-Object {$_.FullyQualifiedErrorId -notlike '*NoMatchingEventsFound*'}).Count){throw $eventErrors[0]}; "
        "$events | Select-Object TimeCreated,Id,ProviderName,LevelDisplayName"
    ),
    "reboot_pending": (
        "[pscustomobject]@{componentServicing=(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Component Based Servicing\\RebootPending');"
        "windowsUpdate=(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired');"
        "fileRenamePending=($null -ne (Get-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager' "
        "-Name PendingFileRenameOperations -ErrorAction SilentlyContinue).PendingFileRenameOperations)}"
    ),
    "dns_configuration": "Get-DnsClientServerAddress | Select-Object InterfaceAlias,AddressFamily,ServerAddresses",
    "storage": "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace,VolumeDirty",
}


async def diagnostics(
    conn, sections: list[str] | None = None, timeout: int = 30
) -> dict:
    report = await collect(conn, "diagnostics", QUERIES, sections, timeout, limit=100)
    report["coverage_notes"] = {
        "automatic_services_stopped": "Candidates for investigation; trigger-start services can legitimately be stopped.",
        "system_errors": "At most 50 System critical/error event metadata records from the last 24 hours. Event messages and user data are excluded.",
        "dns_configuration": "Configuration evidence only; no external connectivity probe is made.",
        "reboot_pending": "Selected Windows indicators, not a guarantee that every reboot condition is detected.",
    }
    return report
