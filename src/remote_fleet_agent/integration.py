"""Portable MCP client config generation and explicit simulator-only protocol checks."""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath, PureWindowsPath

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.storage import atomic_text, private_dir

PROVIDERS = (
    "codex",
    "claude-code",
    "claude-desktop",
    "gemini",
    "cursor",
    "vscode",
    "generic",
)
SOURCES = {
    "codex": "https://learn.chatgpt.com/docs/extend/mcp?surface=cli",
    "claude-code": "https://code.claude.com/docs/en/mcp",
    "claude-desktop": "https://modelcontextprotocol.io/docs/develop/connect-local-servers",
    "gemini": "https://geminicli.com/docs/tools/mcp-server/",
    "cursor": "https://cursor.com/docs/mcp",
    "vscode": "https://code.visualstudio.com/docs/agents/reference/mcp-configuration",
}


def launch_spec(
    python, config, key=None, jobs=None, enable_maintenance=False, system=None
):
    cls = (
        PureWindowsPath if (system or platform.system()) == "Windows" else PurePosixPath
    )
    values = [python, config, *([key] if key else []), *([jobs] if jobs else [])]
    if any(
        not isinstance(p, str)
        or not cls(p).is_absolute()
        or any(c in p for c in ("\x00", "\r", "\n"))
        for p in values
    ):
        raise ValueError(
            "Use absolute paths on the execution host, without control characters."
        )
    args = ["-m", "remote_fleet_agent.launcher", "--config", config]
    if key:
        args += ["--key", key]
    if jobs:
        args += ["--jobs", jobs]
    if enable_maintenance:
        args += ["--enable-maintenance"]
    return {"command": python, "args": args}


def render(provider, spec, name="remote-fleet"):
    import re

    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise ValueError("Invalid MCP server name.")
    if provider not in PROVIDERS:
        raise ValueError("Unsupported AI client.")
    if provider == "codex":
        q = lambda x: json.dumps(x, ensure_ascii=True)
        return (
            "config.toml",
            f"[mcp_servers.{name}]\ncommand = {q(spec['command'])}\nargs = {q(spec['args'])}\nstartup_timeout_sec = 30\ntool_timeout_sec = 180\n",
        )
    entry = dict(spec)
    if provider in ("claude-code", "vscode"):
        entry["type"] = "stdio"
    if provider == "gemini":
        entry.update(timeout=180000, trust=False)
    if provider == "generic":
        return "server.json", json.dumps(entry, indent=2) + "\n"
    wrapper = "servers" if provider == "vscode" else "mcpServers"
    names = {
        "claude-code": ".mcp.json",
        "claude-desktop": "claude_desktop_config.json",
        "gemini": "settings.json",
        "cursor": "mcp.json",
        "vscode": "mcp.json",
    }
    return names[provider], json.dumps({wrapper: {name: entry}}, indent=2) + "\n"


def register(sub):
    config = sub.add_parser(
        "ai-config",
        help="Generate reviewable setup files for MCP-compatible AI clients; never edit their settings automatically",
    )
    config.add_argument("--provider", choices=(*PROVIDERS, "all"), default="all")
    config.add_argument("--config", required=True, type=Path)
    config.add_argument("--key", type=Path)
    config.add_argument("--jobs", type=Path)
    config.add_argument("--directory", required=True, type=Path)
    config.add_argument("--enable-maintenance", action="store_true")
    doctor = sub.add_parser(
        "doctor",
        help="Check the host and optional MCP simulation without contacting real endpoints",
    )
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--key", type=Path)
    doctor.add_argument(
        "--probe",
        action="store_true",
        help="Negotiate MCP and call a simulated inventory tool; requires simulation=true",
    )


async def probe(config):
    from tempfile import TemporaryDirectory

    import anyio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    c = FleetConfig.load(config)
    if not c.simulation:
        raise ValueError(
            "Protocol probe requires an explicit simulated fleet; no real endpoint probe is permitted."
        )
    with TemporaryDirectory(prefix="fleet-mcp-probe-") as folder:
        spec = launch_spec(
            sys.executable,
            str(Path(config).resolve()),
            jobs=str(Path(folder).resolve()),
        )
        with anyio.fail_after(30):
            async with (
                stdio_client(
                    StdioServerParameters(
                        **spec,
                        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1])},
                    )
                ) as (read, write),
                ClientSession(read, write) as session,
            ):
                initialized = await session.initialize()
                tools = (await session.list_tools()).tools
                caps = await session.call_tool("describe_capabilities", {})
                if caps.is_error:
                    raise ValueError("MCP capability discovery failed.")
                result = {
                    "initialized": True,
                    "protocol_version": initialized.protocol_version,
                    "tools": len(tools),
                    "capabilities": "passed",
                    "endpoint_connections": 0,
                }
                if c.hosts:
                    collection = await session.call_tool(
                        "inventory", {"host": c.hosts[0].name}
                    )
                    if collection.is_error:
                        raise ValueError("Simulated inventory tool call failed.")
                    result["simulated_inventory"] = "passed"
                rejected = await session.call_tool(
                    "readiness", {"host": "not-approved-probe-host"}
                )
                if not rejected.is_error:
                    raise ValueError("Scope rejection check failed.")
                result["scope_rejection"] = "passed"
                return result


def diagnose(config=None, key=None):
    checks = []

    def add(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})

    add("python", sys.version_info >= (3, 11), platform.python_version())
    add(
        "host_os",
        platform.system() in ("Darwin", "Windows", "Linux"),
        platform.system(),
    )
    for package in ("remote-fleet-agent", "mcp", "asyncssh"):
        try:
            add(package, True, version(package))
        except PackageNotFoundError:
            add(package, False, "Not installed")
    if config:
        try:
            c = FleetConfig.load(config)
            add(
                "fleet_config",
                True,
                f"{len(c.hosts)} approved devices; "
                + ("simulated" if c.simulation else "real endpoint configuration"),
            )
            if not c.simulation:
                add(
                    "private_key",
                    bool(key and Path(key).expanduser().is_file()),
                    "Present"
                    if key and Path(key).expanduser().is_file()
                    else "Supply --key for real collections",
                )
        except (ValueError, OSError) as exc:
            add("fleet_config", False, str(exc))
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "note": "Host OS and endpoint OS are independent. No provider login, access installation or real endpoint connection was attempted.",
    }


def execute(args):
    if args.command == "doctor":
        result = diagnose(args.config, args.key)
        if args.probe:
            if not args.config:
                raise ValueError(
                    "--probe requires --config for an explicitly simulated fleet."
                )
            result["protocol_probe"] = asyncio.run(probe(args.config))
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    c = FleetConfig.load(args.config)
    if not c.simulation and (not args.key or not args.key.expanduser().is_file()):
        raise ValueError(
            "Real client configuration requires --key pointing to its private key."
        )
    spec = launch_spec(
        sys.executable,
        str(args.config.expanduser().resolve()),
        str(args.key.expanduser().resolve()) if args.key else None,
        str(args.jobs.expanduser().resolve()) if args.jobs else None,
        args.enable_maintenance,
    )
    out = args.directory.expanduser().resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError(
            "Choose an empty output directory; existing client settings are never overwritten."
        )
    private_dir(out)
    providers = PROVIDERS if args.provider == "all" else (args.provider,)
    for provider in providers:
        folder = private_dir(out / provider)
        name, body = render(provider, spec)
        atomic_text(folder / name, body)
    atomic_text(
        out / "READ-ME.txt",
        "Generated for this execution host. Merge the selected entry into your AI client settings; preserve existing entries. Restart/reconnect the MCP client, then call describe_capabilities.\nDefault: audit-only. No private key contents, AI API keys or workspace owner tokens are embedded. Paths can still identify a private engagement: keep this folder private.\nThese are local stdio MCP settings, not hosted chat website integrations. The Python environment must remain installed at the generated command path.\n"
        + "".join(
            f"{p}: {SOURCES.get(p, 'Any MCP stdio client accepting command/args')}\n"
            for p in providers
        ),
    )
    print(
        json.dumps(
            {
                "directory": str(out),
                "providers": list(providers),
                "audit_only": not args.enable_maintenance,
                "settings_changed": False,
                "host_os": platform.system(),
            },
            indent=2,
        )
    )
    return 0
