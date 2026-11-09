"""Platform-specific, fixed Python probes over pinned SSH. Never sudo or install."""

from __future__ import annotations

import asyncio
import base64
import json
import shlex
from pathlib import Path

from remote_fleet_agent.tools.collect import select_queries, timestamp

INVENTORY = (
    "os",
    "hardware",
    "software",
    "disks",
    "encryption",
    "network",
    "local_users",
    "administrators",
    "firewall",
    "antivirus_products",
)
DIAGNOSTICS = (
    "performance",
    "services",
    "reboot_pending",
    "dns_configuration",
    "storage",
)
READINESS = ("permissions", "modules", "disk_space", "cleanup")
CATALOG = {
    p: {
        op: dict.fromkeys(names, "")
        for op, names in [
            ("inventory", INVENTORY),
            ("diagnostics", DIAGNOSTICS),
            ("readiness", READINESS),
        ]
    }
    for p in ("linux", "macos")
}


def build_command(platform, operation, section):
    if (
        platform not in CATALOG
        or operation not in CATALOG[platform]
        or section not in CATALOG[platform][operation]
    ):
        raise ValueError("unsupported platform query")
    encoded = base64.b64encode(
        Path(__file__).with_name("unix_probe.py").read_bytes()
    ).decode("ascii")
    launcher = (
        "import base64;exec(compile(base64.b64decode("
        + repr(encoded)
        + "),'<fleet-fixed-probe>','exec'))"
    )
    return (
        "python3 -c "
        + shlex.quote(launcher)
        + " "
        + " ".join(map(shlex.quote, (platform, operation, section)))
    )


def assess_readiness(sections):
    def rows(name):
        s = sections.get(name, {})
        return s.get("items", []) if s.get("status") == "collected" else []

    disks = rows("disk_space")
    permissions = rows("permissions")
    space = bool(
        disks
        and type(disks[0].get("free_bytes")) is int
        and disks[0]["free_bytes"] >= 256 * 1024**2
    )
    ready = bool(permissions and space)
    return {
        "state": "ready" if ready else "needs_attention",
        "read_only_ready": ready,
        "maintenance_ready": False,
        "cleanup": "not_managed",
        "missing_commands": [
            r["command"] for r in rows("modules") if not r.get("available")
        ],
        "notes": [
            "Unix maintenance and automatic access expiry are not implemented. Missing privileged evidence remains unknown."
        ],
        "note": "Read-only prerequisites only; not a security verdict or maintenance approval.",
    }


async def collect_unix(conn, platform, operation, sections=None, timeout=30):
    selected = select_queries(CATALOG[platform][operation], sections)
    report = {
        "schema": "fleet-evidence-v1",
        "kind": operation,
        "platform": platform,
        "simulation": False,
        "started_at": timestamp(),
        "sections": {},
    }
    for name in selected:
        try:
            result = await asyncio.wait_for(
                conn.run(build_command(platform, operation, name), check=False),
                timeout=timeout,
            )
            if result.exit_status:
                raise RuntimeError(
                    "Fixed probe failed: Python 3 and an SSH exec shell are required. "
                    + str(result.stderr)[:250]
                )
            if len(result.stdout) > 2_000_000:
                raise ValueError("evidence exceeds response limit")
            value = json.loads(result.stdout)
            if (
                not isinstance(value, dict)
                or value.get("status") not in ("collected", "unknown")
                or not isinstance(value.get("items"), list)
                or len(value["items"]) > 500
                or any(not isinstance(row, dict) for row in value["items"])
            ):
                raise ValueError("unexpected evidence shape")
            report["sections"][name] = value
        except Exception as exc:  # noqa: BLE001 - retain other sections when one remote probe fails
            report["sections"][name] = {
                "status": "unknown",
                "items": [],
                "error": str(exc)[:1000],
            }
    report["finished_at"] = timestamp()
    report["status"] = (
        "complete"
        if all(s["status"] == "collected" for s in report["sections"].values())
        else "partial"
    )
    report["coverage_notes"] = {
        "platform": "Requires Python 3; reads available account-visible metadata only. No sudo, installation, discovery or repairs.",
        "software": "Linux: dpkg/rpm package registrations; macOS: top-level system and shared Applications bundles. Per-user/portable applications may be absent.",
        "security": "Unavailable firewall, encryption, antivirus or privileged evidence is unknown, never a pass.",
        "container": "Containers share a Linux kernel and cannot validate physical disk encryption, systemd or macOS/Windows behavior.",
    }
    if operation == "readiness":
        report["connection"] = "connected"
        report["readiness"] = assess_readiness(report["sections"])
    return report
