"""Explicit fictional evidence. This transport never opens a network connection."""

from __future__ import annotations

from remote_fleet_agent.tools.collect import select_queries, timestamp


def simulated_collection(host, operation, sections=None):
    from remote_fleet_agent.runtime import FleetRuntime

    names = select_queries(FleetRuntime.catalog(host.platform, operation), sections)
    if host.scenario == "offline":
        raise ConnectionError(
            "SIMULATED: device is offline; no connection was attempted"
        )
    attention = host.scenario == "attention"
    windows = host.platform == "windows"
    good = not attention
    size = 256 * 1024**3
    free = (8 if attention else 120) * 1024**3
    data = {
        "os": [
            {
                "Caption": {
                    "windows": "Windows 11 Pro (simulated)",
                    "linux": "Ubuntu 24.04 (simulated)",
                    "macos": "macOS (simulated)",
                }[host.platform],
                "Version": "Fictional fixture",
                "OSArchitecture": "arm64" if host.platform == "macos" else "x86_64",
            }
        ],
        "hardware": [
            {"Model": "Simulated device", "TotalPhysicalMemory": 16 * 1024**3}
        ],
        "software": [{"DisplayName": "Demo application", "DisplayVersion": "2.0"}],
        "disks": [
            {
                "DeviceID": "C:" if windows else "/",
                "MountPoint": "C:" if windows else "/",
                "Size": size,
                "FreeSpace": free,
            }
        ],
        "encryption": [
            {
                "MountPoint": "C:" if windows else "/",
                "ProtectionStatus": "On" if good else "Off",
            }
        ],
        "firewall": [{"Name": "Simulated firewall", "Enabled": good}],
        "defender": [
            {
                "AMRunningMode": "Normal",
                "AMServiceEnabled": True,
                "AntivirusEnabled": good,
                "RealTimeProtectionEnabled": good,
            }
        ],
        "antivirus_products": [
            {
                "displayName": "Simulated protection product",
                "note": "Fictional fixture, not a real health assessment",
            }
        ],
        "local_users": [{"Name": "demo-user", "Enabled": True}],
        "administrators": [{"Name": "demo-admin"}],
        "network": [{"InterfaceAlias": "demo-interface", "ipv4": ["192.0.2.10"]}],
        "hotfixes": [{"HotFixID": "DEMO-001", "Description": "Synthetic example"}],
        "performance": [{"cpu_count": 4, "load_average": [0.2, 0.1, 0.1]}],
        "automatic_services_stopped": [],
        "services": [],
        "system_errors": [],
        "reboot_pending": [{"componentServicing": attention, "required": attention}],
        "dns_configuration": [{"ServerAddresses": ["192.0.2.53"]}],
        "storage": [
            {"DeviceID": "C:" if windows else "/", "Size": size, "FreeSpace": free}
        ],
        "permissions": [{"administrator": True, "uid": 0, "read_only_access": True}],
        "modules": [{"command": "Simulated prerequisite", "available": True}],
        "disk_space": [{"free_bytes": free, "size_bytes": size}],
        "cleanup": [
            {
                "journal_found": True,
                "expires_at": "2099-01-01T00:00:00+00:00",
                "task_found": True,
                "task_enabled": True,
                "completed": False,
            }
        ],
    }
    result = {}
    for name in names:
        if host.scenario == "limited" and name in (
            "firewall",
            "encryption",
            "antivirus_products",
            "permissions",
            "administrators",
            "cleanup",
        ):
            result[name] = {
                "status": "unknown",
                "items": [],
                "error": "SIMULATED: account does not have access to this evidence",
            }
        else:
            rows = data.get(name, [])
            result[name] = {
                "status": "collected",
                "items": rows,
                "total": len(rows),
                "truncated": False,
            }
    report = {
        "schema": "fleet-evidence-v1",
        "kind": operation,
        "platform": host.platform,
        "simulation": True,
        "scenario": host.scenario,
        "status": "complete"
        if all(s["status"] == "collected" for s in result.values())
        else "partial",
        "started_at": timestamp(),
        "finished_at": timestamp(),
        "sections": result,
        "assessment": "SIMULATED / FICTIONAL evidence. No real device was contacted. Not an assessment.",
    }
    if operation == "readiness":
        report["connection"] = "simulated"
        report["readiness"] = {
            "state": "needs_attention" if host.scenario == "limited" else "ready",
            "maintenance_ready": False,
            "cleanup": "not_managed",
            "notes": ["SIMULATED; endpoint changes and real connections are disabled."],
        }
    return report
