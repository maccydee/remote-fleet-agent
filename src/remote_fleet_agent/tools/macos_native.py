"""Fixed native macOS evidence probes. Parsing runs on the controller, never the Mac."""

import plistlib
import re

COMMANDS = {
    "os": "/usr/bin/sw_vers",
    "hardware": "/usr/sbin/sysctl hw.model hw.memsize hw.ncpu",
    "software": "/usr/sbin/system_profiler -xml SPApplicationsDataType -detailLevel mini",
    "disks": "/usr/sbin/diskutil info -plist /",
    "storage": "/bin/df -Pk /",
    "disk_space": "/bin/df -Pk /",
    "encryption": "/usr/bin/fdesetup status",
    "network": "/sbin/ifconfig -a",
    "local_users": "/usr/bin/dscl . -list /Users UniqueID",
    "administrators": "/usr/bin/dscl . -read /Groups/admin GroupMembership",
    "firewall": "/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate",
    "performance": "/usr/sbin/sysctl vm.loadavg hw.ncpu",
    "services": "/bin/launchctl list",
    "dns_configuration": "/usr/sbin/scutil --dns",
    "permissions": "/usr/bin/id -u",
    "modules": 'for c in /usr/bin/sw_vers /usr/sbin/sysctl /usr/sbin/diskutil /bin/df; do if [ -x "$c" ]; then printf "%s\\tavailable\\n" "$c"; else printf "%s\\tmissing\\n" "$c"; fi; done',
    "cleanup": ":",
    "reboot_pending": ":",
    "antivirus_products": ":",
}


def parse(section, output):
    rows = []
    if section in ("cleanup", "reboot_pending", "antivirus_products"):
        return {
            "status": "unknown",
            "items": [],
            "note": "Not established by native account-visible evidence.",
        }
    if section in ("os", "hardware"):
        rows = [
            {
                k.strip(): v.strip()
                for k, v in (s.split(":", 1) for s in output.splitlines() if ":" in s)
            }
        ]
        values = rows[0]
        if section == "os":
            rows = [
                {
                    "Caption": values.get("ProductName", "Darwin"),
                    "Version": values.get("ProductVersion", "Unknown"),
                    "BuildNumber": values.get("BuildVersion", "Unknown"),
                }
            ]
        else:
            rows = [
                {
                    "Model": values.get("hw.model", "Unknown"),
                    "TotalPhysicalMemory": int(values["hw.memsize"])
                    if "hw.memsize" in values
                    else None,
                    "cpu_count": int(values["hw.ncpu"])
                    if "hw.ncpu" in values
                    else None,
                }
            ]
    elif section == "disks":
        value = plistlib.loads(output.encode())
        rows = [{k: v for k, v in value.items() if isinstance(v, (str, int, bool))}]
        rows[0].update(
            {
                "DeviceID": "/",
                "Size": value.get("TotalSize"),
                "FreeSpace": value.get("APFSContainerFree")
                or value.get("VolumeFreeSpace"),
            }
        )
    elif section == "software":
        value = plistlib.loads(output.encode())
        rows = [
            {
                "DisplayName": app.get("_name", "Unknown"),
                "DisplayVersion": app.get("version", "Unknown"),
                "path": app.get("path", ""),
                "obtained_from": app.get("obtained_from", ""),
            }
            for entry in value
            for app in entry.get("_items", [])
        ]
    elif section in ("disk_space", "storage"):
        columns = output.splitlines()[-1].split()
        rows = [
            {
                "filesystem": columns[0],
                "free_bytes": int(columns[3]) * 1024,
                "FreeSpace": int(columns[3]) * 1024,
                "Size": int(columns[1]) * 1024,
                "size_bytes": int(columns[1]) * 1024,
                "DeviceID": columns[-1],
                "mount": columns[-1],
            }
        ]
    elif section == "permissions":
        uid = int(output.strip())
        rows = [{"uid": uid, "root": uid == 0, "scope": "SSH account-visible evidence"}]
    elif section == "modules":
        rows = [
            {"command": s.split("\t")[0], "available": s.endswith("\tavailable")}
            for s in output.splitlines()
        ]
    elif section == "local_users":
        rows = [
            {"Name": s.rsplit(None, 1)[0], "uid": int(s.rsplit(None, 1)[1])}
            for s in output.splitlines()
            if re.search(r"\s\d+$", s)
        ]
    elif section == "administrators":
        rows = [
            {"Name": s, "group": "admin", "scope": "Direct local membership only"}
            for s in output.partition(":")[2].split()
        ]
    elif section == "network":
        current = None
        for line in output.splitlines():
            if line and not line[0].isspace():
                current = {"InterfaceAlias": line.partition(":")[0], "addresses": []}
                rows.append(current)
            elif current is not None and line.strip().startswith(("inet ", "inet6 ")):
                current["addresses"].append(line.split()[1])
    elif section == "services":
        rows = [
            {"PID": c[0], "exit_status": c[1], "label": c[2]}
            for line in output.splitlines()[1:]
            if len(c := line.split()) == 3
        ]
    elif section == "dns_configuration":
        resolver = ""
        for line in output.splitlines():
            if line.startswith("resolver #"):
                resolver = line.strip()
            elif "nameserver[" in line:
                rows.append(
                    {
                        "resolver": resolver,
                        "ServerAddress": line.partition(":")[2].strip(),
                        "scope": "scutil DNS resolver evidence",
                    }
                )
    elif section == "performance":
        load = re.search(r"vm.loadavg:.*?([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)", output)
        cpu = re.search(r"hw.ncpu:\s*(\d+)", output)
        if load and cpu:
            rows = [
                {
                    "load_average": list(map(float, load.groups())),
                    "cpu_count": int(cpu[1]),
                }
            ]
    elif section == "firewall":
        state = re.search(r"State = ([01])", output)
        if state:
            rows = [{"Name": "Application Firewall", "Enabled": state[1] == "1"}]
    elif section == "encryption":
        state = re.search(r"FileVault is (On|Off)\b", output)
        if state:
            rows = [
                {
                    "MountPoint": "/",
                    "ProtectionStatus": state[1],
                    "product": "FileVault",
                }
            ]
    else:
        raise ValueError("Unsupported native section")
    if not rows:
        return {
            "status": "unknown",
            "items": [],
            "note": "Native probe returned no evidence.",
        }
    return {
        "status": "collected",
        "items": rows[:500],
        "total": len(rows),
        "truncated": len(rows) > 500,
        "source": "native macOS command over pinned SSH",
    }
