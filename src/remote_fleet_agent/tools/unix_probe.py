"""Fixed Unix probes shipped over SSH. Python 3 standard library only; no repairs."""

from __future__ import annotations

import grp
import json
import os
import platform
import plistlib
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path

LIMIT = 500


def command(args):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=12, check=False
    )
    if result.returncode:
        raise RuntimeError(
            result.stderr.strip()[:250] or "command unavailable to this account"
        )
    if len(result.stdout) > 2_000_000:
        raise RuntimeError("command output exceeds the evidence limit")
    return result.stdout


def unknown(reason, items=None):
    return {"status": "unknown", "error": reason, "items": items or []}


def disk():
    s = os.statvfs("/")
    return {
        "DeviceID": "/",
        "MountPoint": "/",
        "Size": s.f_blocks * s.f_frsize,
        "FreeSpace": s.f_bavail * s.f_frsize,
    }


def text_file(path):
    with open(path) as f:
        return f.read(65536)


def probe(system, operation, section):
    if operation == "readiness":
        if section == "permissions":
            return [
                {
                    "uid": os.geteuid(),
                    "administrator": os.geteuid() == 0,
                    "read_only_access": True,
                }
            ]
        if section == "modules":
            names = (
                ["python3", "df", "uname", "ip", "systemctl", "nft", "lsblk"]
                if system == "linux"
                else ["python3", "sw_vers", "sysctl", "fdesetup", "dscl", "launchctl"]
            )
            return [
                {"command": n, "available": shutil.which(n) is not None} for n in names
            ]
        if section == "disk_space":
            d = disk()
            return [{"free_bytes": d["FreeSpace"], "size_bytes": d["Size"]}]
        if section == "cleanup":
            return unknown(
                "No Unix assessment installer or automatic access-expiry journal is managed by this release."
            )
    if section in ("disks", "storage"):
        return [disk()]
    if section == "os":
        info = {
            "Caption": platform.system(),
            "Version": platform.release(),
            "OSArchitecture": platform.machine(),
        }
        if system == "linux":
            for line in text_file("/etc/os-release").splitlines():
                key, _, value = line.partition("=")
                if key == "PRETTY_NAME":
                    info["Caption"] = value.strip('"')
        else:
            info["Version"] = command(["/usr/bin/sw_vers", "-productVersion"]).strip()
            info["BuildNumber"] = command(["/usr/bin/sw_vers", "-buildVersion"]).strip()
        return [info]
    if section == "hardware":
        if system == "macos":
            return [
                {
                    "Model": command(["/usr/sbin/sysctl", "-n", "hw.model"]).strip(),
                    "TotalPhysicalMemory": int(
                        command(["/usr/sbin/sysctl", "-n", "hw.memsize"])
                    ),
                }
            ]
        memory = re.search(
            r"^MemTotal:\s+(\d+)", text_file("/proc/meminfo"), re.MULTILINE
        )
        return [
            {
                "Architecture": platform.machine(),
                "TotalPhysicalMemory": int(memory[1]) * 1024 if memory else None,
                "note": "Container-visible hardware may describe its VM, not a physical device.",
            }
        ]
    if section == "software":
        if system == "linux":
            if shutil.which("dpkg-query"):
                rows = command(
                    ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"]
                )
            elif shutil.which("rpm"):
                rows = command(
                    ["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"]
                )
            else:
                return unknown("No supported dpkg/rpm package database is available.")
            return [
                {
                    "DisplayName": row.split("\t")[0],
                    "DisplayVersion": row.split("\t")[1],
                }
                for row in rows.splitlines()
                if "\t" in row
            ]
        rows = []
        for base in (Path("/Applications"), Path("/System/Applications")):
            for app in sorted(base.glob("*.app")):
                try:
                    with (app / "Contents/Info.plist").open("rb") as f:
                        info = plistlib.load(f)
                    rows.append(
                        {
                            "DisplayName": str(
                                info.get("CFBundleDisplayName", app.stem)
                            ),
                            "DisplayVersion": str(
                                info.get("CFBundleShortVersionString", "Unknown")
                            ),
                        }
                    )
                except (OSError, ValueError):
                    rows.append({"DisplayName": app.stem, "DisplayVersion": "Unknown"})
        return rows
    if section == "network":
        if system == "linux":
            return json.loads(command(["ip", "-json", "address", "show"]))
        output = command(["/sbin/ifconfig"])
        rows = []
        current = None
        for line in output.splitlines():
            if line and not line[0].isspace():
                current = {"InterfaceAlias": line.split(":")[0], "addresses": []}
                rows.append(current)
            elif current is not None and line.strip().startswith(("inet ", "inet6 ")):
                current["addresses"].append(line.split()[1])
        return rows
    if section == "local_users":
        if system == "macos":
            return [
                {"Name": row.split()[0], "uid": int(row.split()[-1])}
                for row in command(
                    ["/usr/bin/dscl", ".", "-list", "/Users", "UniqueID"]
                ).splitlines()
                if len(row.split()) == 2
            ]
        return [
            {"Name": p.pw_name, "uid": p.pw_uid, "shell": p.pw_shell}
            for p in pwd.getpwall()
        ]
    if section == "administrators":
        if system == "macos":
            names = (
                command(
                    ["/usr/bin/dscl", ".", "-read", "/Groups/admin", "GroupMembership"]
                )
                .partition(":")[2]
                .split()
            )
            return [
                {"Name": n, "group": "admin", "scope": "direct local membership only"}
                for n in names
            ]
        rows = []
        for group in ("sudo", "wheel"):
            try:
                g = grp.getgrnam(group)
                names = set(g.gr_mem) | {
                    p.pw_name for p in pwd.getpwall() if p.pw_gid == g.gr_gid
                }
                rows.extend({"Name": n, "group": group} for n in sorted(names))
            except KeyError:
                pass
        rows.append(
            {
                "Name": "root",
                "group": "uid-0",
                "scope": "Does not establish all sudoers, PAM or directory-service grants.",
            }
        )
        return rows
    if section == "firewall":
        if system == "macos":
            output = command(
                ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"]
            )
            if "State = 1" in output:
                return [{"Name": "Application Firewall", "Enabled": True}]
            if "State = 0" in output:
                return [{"Name": "Application Firewall", "Enabled": False}]
            return unknown("Application Firewall state was not recognized.")
        if shutil.which("ufw"):
            output = command(["ufw", "status"])
            if "Status: active" in output:
                return [{"Name": "ufw", "Enabled": True}]
            if "Status: inactive" in output:
                return [
                    {
                        "Name": "ufw",
                        "Enabled": False,
                        "note": "Other firewall managers may still be active.",
                    }
                ]
        return unknown(
            "Firewall state requires an accessible supported firewall manager; no state inferred from missing commands."
        )
    if section == "encryption":
        if system == "macos":
            output = command(["/usr/bin/fdesetup", "status"])
            if "FileVault is On" in output:
                return [
                    {
                        "MountPoint": "/",
                        "ProtectionStatus": "On",
                        "product": "FileVault",
                    }
                ]
            if "FileVault is Off" in output:
                return [
                    {
                        "MountPoint": "/",
                        "ProtectionStatus": "Off",
                        "product": "FileVault",
                    }
                ]
            return unknown("FileVault state is transitional or unavailable.")
        return unknown(
            "Root-volume encryption cannot be established reliably from this account or a container. Verify LUKS/device mapping separately."
        )
    if section == "antivirus_products":
        return unknown(
            "No cross-platform antivirus health verdict is inferred. Record the installed endpoint-protection product and its health separately."
        )
    if section == "performance":
        return [
            {
                "load_average": list(os.getloadavg()),
                "cpu_count": os.cpu_count(),
                "kernel": platform.release(),
            }
        ]
    if section == "services":
        if system == "linux":
            lines = command(
                ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"]
            ).splitlines()
            return [
                {"unit": line.split()[0], "state": "failed"}
                for line in lines
                if line.strip()
            ]
        lines = command(["/bin/launchctl", "list"]).splitlines()[1:]
        return [
            {"PID": l.split()[0], "exit_status": l.split()[1], "label": l.split()[2]}
            for l in lines
            if len(l.split()) == 3
        ]
    if section == "reboot_pending":
        if system == "linux":
            return [
                {
                    "required": Path("/var/run/reboot-required").exists(),
                    "scope": "Selected Debian/Ubuntu marker only; absence is not a universal guarantee.",
                }
            ]
        return unknown(
            "No reliable general macOS pending-reboot indicator is provided."
        )
    if section == "dns_configuration":
        names = []
        for line in text_file("/etc/resolv.conf").splitlines():
            if line.strip().startswith("nameserver "):
                names.append(line.split()[1])
        return [
            {
                "ServerAddresses": names,
                "scope": "resolv.conf only; macOS scoped resolvers and split DNS may differ.",
            }
        ]
    raise ValueError("Unsupported fixed query")


def main():
    system, operation, section = sys.argv[1:]
    expected = "Darwin" if system == "macos" else "Linux"
    if platform.system() != expected:
        raise ValueError("Configured platform does not match the remote kernel")
    result = probe(system, operation, section)
    if isinstance(result, list):
        result = {
            "status": "collected",
            "items": result[:LIMIT],
            "total": len(result),
            "truncated": len(result) > LIMIT,
        }
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - fixed probes report unavailable evidence as unknown
        print(json.dumps(unknown(str(exc)[:500])))
