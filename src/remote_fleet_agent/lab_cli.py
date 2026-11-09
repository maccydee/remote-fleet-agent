"""Separate, local-only demo and disposable Docker lab commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.runtime import FleetRuntime
from remote_fleet_agent.storage import save_json
from remote_fleet_agent.workspace import EvidenceWorkspace


def register(sub):
    demo = sub.add_parser(
        "demo",
        help="Generate a clearly fictional mixed-platform fleet without Docker or SSH",
    )
    demo.add_argument("--directory", required=True, type=Path)
    lab = sub.add_parser(
        "lab",
        help="Manage an isolated Linux SSH Docker lab; never a macOS/Windows emulator",
    )
    lab.add_argument("mode", choices=["init", "up", "status", "collect", "down"])
    lab.add_argument("--directory", required=True, type=Path)


def prepare_directory(directory, kind):
    directory = directory.expanduser().resolve()
    marker = directory / ".fleet-lab.json"
    if directory.exists() and any(directory.iterdir()) and not marker.exists():
        raise ValueError(
            "choose an empty directory; existing files are never overwritten"
        )
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if marker.exists():
        value = json.loads(marker.read_text())
        if value != {"kind": kind, "schema": 1}:
            raise ValueError("directory belongs to a different lab type")
    else:
        save_json(marker, {"kind": kind, "schema": 1})
    return directory


async def collect_fleet(directory):
    config = FleetConfig.load(directory / "fleet.json")
    runtime = FleetRuntime(
        config, str(directory / ".state/client_key"), directory / "jobs"
    )
    jobs = []
    try:
        for operation in ("inventory", "diagnostics", "readiness"):
            job = runtime.start(operation, group="all")
            result = await runtime.jobs.wait(job["id"])
            jobs.append(
                {"operation": operation, "id": job["id"], "status": result["status"]}
            )
        workspace = EvidenceWorkspace(config, directory / "jobs")
        from shutil import copy2

        for kind in ("dashboard", "report"):
            export = workspace.export(kind, shareable=False)
            copy2(export["path"], directory / (kind + ".html"))
        return {
            "directory": str(directory),
            "simulation": config.simulation,
            "jobs": jobs,
            "dashboard": str(directory / "dashboard.html"),
        }
    finally:
        await runtime.close()


def demo(directory):
    directory = prepare_directory(directory, "simulation")
    hosts = []
    for system, scenario in [
        ("windows", "healthy"),
        ("windows", "attention"),
        ("macos", "healthy"),
        ("macos", "attention"),
        ("linux", "healthy"),
        ("linux", "limited"),
        ("linux", "offline"),
    ]:
        name = system + "-" + scenario
        hosts.append(
            {
                "name": name,
                "target": "demo@" + name + ".invalid",
                "approved": True,
                "platform": system,
                "scenario": scenario,
                "groups": ["all", system, scenario],
            }
        )
    config_path = directory / "fleet.json"
    if not config_path.exists():
        save_json(
            config_path,
            {
                "client": "demo-fleet",
                "simulation": True,
                "known_hosts": ".state/unused-known-hosts",
                "hosts": hosts,
            },
        )
    config = FleetConfig.load(config_path)
    if not config.simulation or config.client != "demo-fleet":
        raise ValueError(
            "demo config must remain simulation=true with the demo client name"
        )
    result = asyncio.run(collect_fleet(directory))
    ws = EvidenceWorkspace(config, directory / "jobs")
    if not ws.manual_records():
        ws.add_manual(
            asset="Fictional backup appliance",
            category="backup",
            control="2500",
            status="open",
            reviewer="Simulator",
            note="SIMULATED: restore evidence has not yet been recorded.",
            reference="Fictional example only",
        )
        result = asyncio.run(export_only(directory)) | {"jobs": result["jobs"]}
    return result


async def export_only(directory):
    from shutil import copy2

    config = FleetConfig.load(directory / "fleet.json", require_known_hosts=False)
    ws = EvidenceWorkspace(config, directory / "jobs")
    for kind in ("dashboard", "report"):
        copy2(ws.export(kind, shareable=False)["path"], directory / (kind + ".html"))
    return {
        "directory": str(directory),
        "simulation": config.simulation,
        "dashboard": str(directory / "dashboard.html"),
    }


def execute(args):
    if args.command == "demo":
        result = demo(args.directory)
    else:
        from remote_fleet_agent.docker_lab import manage

        result = manage(args.mode, args.directory)
    print(json.dumps(result, indent=2))
    return 0
