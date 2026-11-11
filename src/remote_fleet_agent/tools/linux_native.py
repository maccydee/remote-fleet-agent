"""Account-visible Linux fallback evidence when Python isn't available yet."""

import re

COMMANDS = {
    "os": "cat /etc/os-release",
    "hardware": "uname -m; cat /proc/meminfo",
    "disks": "df -Pk /",
    "storage": "df -Pk /",
    "disk_space": "df -Pk /",
    "permissions": "id -u",
    "modules": 'for c in uname df cat id; do if command -v "$c" >/dev/null 2>&1; then printf "%s\\tavailable\\n" "$c"; else printf "%s\\tmissing\\n" "$c"; fi; done',
    "software": """if command -v dpkg-query >/dev/null 2>&1; then dpkg-query -W -f='${binary:Package}\\t${Version}\\n'; elif command -v rpm >/dev/null 2>&1; then rpm -qa --qf '%{NAME}\\t%{VERSION}-%{RELEASE}\\n'; else exit 2; fi""",
    "network": "ip -o addr show",
    "local_users": "getent passwd",
    "administrators": 'getent group sudo; getent group wheel; printf "root:x:0:root\\n"',
    "firewall": "ufw status",
    "encryption": ":",
    "antivirus_products": ":",
    "cleanup": ":",
    "reboot_pending": "if [ -f /var/run/reboot-required ]; then echo required; else echo absent; fi",
    "dns_configuration": "cat /etc/resolv.conf",
    "performance": "cat /proc/loadavg; getconf _NPROCESSORS_ONLN",
    "services": "systemctl --failed --no-legend --plain --no-pager",
}


def command(section):
    return '[ "$(uname -s)" = Linux ] && { ' + COMMANDS[section] + "; }"


def parse(section, output):
    from remote_fleet_agent.tools.macos_native import parse as common

    unknown = {
        "status": "unknown",
        "items": [],
        "note": "Not established by account-visible native Linux evidence.",
    }
    if section in ("encryption", "antivirus_products", "cleanup"):
        return unknown
    if section in ("disks", "storage", "disk_space"):
        return common("disk_space", output)
    if section in ("permissions", "modules"):
        return common(section, output)
    rows = []
    if section == "os":
        fields = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        if not any(key in fields for key in ("ID", "PRETTY_NAME")):
            return unknown
        rows = [
            {
                "Caption": fields.get("PRETTY_NAME", "Linux").strip('"'),
                "Version": fields.get("VERSION_ID", "").strip('"'),
            }
        ]
    elif section == "hardware":
        memory = re.search(r"^MemTotal:\s+(\d+)", output, re.MULTILINE)
        rows = [
            {
                "Architecture": output.splitlines()[0],
                "TotalPhysicalMemory": int(memory[1]) * 1024 if memory else None,
                "note": "Kernel/account-visible hardware; containers may describe the host VM.",
            }
        ]
    elif section == "software":
        rows = [
            {"DisplayName": line.split("\t")[0], "DisplayVersion": line.split("\t")[1]}
            for line in output.splitlines()
            if "\t" in line
        ]
    elif section == "local_users":
        rows = [
            {"Name": c[0], "uid": int(c[2]), "shell": c[6]}
            for line in output.splitlines()
            if len(c := line.split(":")) == 7 and c[2].isdigit()
        ]
    elif section == "administrators":
        rows = [
            {
                "Name": name,
                "group": c[0],
                "scope": "Direct local membership only; other grants not established",
            }
            for line in output.splitlines()
            if len(c := line.split(":")) == 4
            for name in c[3].split(",")
            if name
        ]
    elif section == "network":
        rows = [
            {"InterfaceAlias": c[1], "family": c[2], "address": c[3]}
            for line in output.splitlines()
            if len(c := line.split()) >= 4
        ]
    elif section == "dns_configuration":
        rows = [
            {"ServerAddress": line.split()[1], "scope": "resolv.conf only"}
            for line in output.splitlines()
            if line.strip().startswith("nameserver ") and len(line.split()) >= 2
        ]
    elif section == "reboot_pending":
        if output.strip() not in ("required", "absent"):
            return unknown
        rows = [
            {
                "required": output.strip() == "required",
                "scope": "Debian/Ubuntu reboot marker only; absence is not a universal guarantee",
            }
        ]
    elif section == "performance":
        lines = output.splitlines()
        rows = [
            {
                "load_average": list(map(float, lines[0].split()[:3])),
                "cpu_count": int(lines[1]),
            }
        ]
    elif section == "firewall":
        if "Status: active" in output:
            rows = [{"Name": "ufw", "Enabled": True}]
        elif "Status: inactive" in output:
            rows = [
                {
                    "Name": "ufw",
                    "Enabled": False,
                    "note": "Other firewall managers may still be active.",
                }
            ]
    elif section == "services":
        rows = [
            {"unit": line.split()[0], "state": "failed"}
            for line in output.splitlines()
            if line.strip()
        ]
        if not rows:
            return {
                "status": "collected",
                "items": [],
                "note": "No failed systemd units reported.",
            }
    if not rows:
        return unknown
    return {
        "status": "collected",
        "items": rows[:500],
        "total": len(rows),
        "truncated": len(rows) > 500,
        "source": "native Linux commands over pinned SSH",
    }
