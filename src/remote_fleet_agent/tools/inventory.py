from __future__ import annotations

from remote_fleet_agent.tools.collect import collect

QUERIES = {
    "os": "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,OSArchitecture,LastBootUpTime",
    "hardware": "Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model,Domain,PartOfDomain,TotalPhysicalMemory",
    "software": (
        "foreach($path in @('HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*')) { "
        "if(Test-Path $path){Get-ItemProperty $path | Where-Object DisplayName | "
        "Select-Object DisplayName,DisplayVersion,Publisher,InstallDate} }"
    ),
    "disks": "Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,FileSystem,Size,FreeSpace",
    "encryption": "Get-BitLockerVolume | Select-Object MountPoint,@{Name='VolumeType';Expression={[string]$_.VolumeType}},@{Name='VolumeStatus';Expression={[string]$_.VolumeStatus}},@{Name='ProtectionStatus';Expression={[string]$_.ProtectionStatus}},EncryptionPercentage",
    "network": (
        "Get-NetIPConfiguration | Select-Object InterfaceAlias,"
        "@{Name='ipv4';Expression={@($_.IPv4Address.IPAddress)}},"
        "@{Name='ipv6';Expression={@($_.IPv6Address.IPAddress)}},"
        "@{Name='gateway';Expression={@($_.IPv4DefaultGateway.NextHop)}},"
        "@{Name='dns';Expression={@($_.DNSServer.ServerAddresses)}}"
    ),
    "local_users": "Get-LocalUser | Select-Object Name,Enabled,LastLogon,PasswordLastSet,PasswordRequired,PrincipalSource",
    "administrators": "Get-LocalGroup -SID 'S-1-5-32-544' | Get-LocalGroupMember | Select-Object Name,ObjectClass,PrincipalSource",
    "firewall": "Get-NetFirewallProfile | Select-Object Name,Enabled,DefaultInboundAction,DefaultOutboundAction",
    "defender": "Get-MpComputerStatus | Select-Object AMRunningMode,AMServiceEnabled,AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureAge,AntivirusSignatureLastUpdated",
    "antivirus_products": "Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object displayName,productState,timestamp",
    "hotfixes": "Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object HotFixID,Description,InstalledOn",
}


async def inventory(conn, sections: list[str] | None = None, timeout: int = 30) -> dict:
    report = await collect(conn, "inventory", QUERIES, sections, timeout)
    report["coverage_notes"] = {
        "software": "Machine-wide registry registrations; per-user, portable and Store applications may be absent.",
        "hotfixes": "Installed hotfix evidence does not establish pending updates or third-party patch compliance.",
        "antivirus_products": "SecurityCenter2 may not be available on Windows Server. Raw productState is not a health verdict.",
        "encryption": "Status fields only; no recovery passwords or key protectors are collected.",
    }
    return report
