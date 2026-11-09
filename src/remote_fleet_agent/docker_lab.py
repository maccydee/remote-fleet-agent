"""Disposable local Linux SSH lab. No Docker socket or host credentials in devices."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from importlib.resources import files

import asyncssh

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.lab_cli import collect_fleet, prepare_directory
from remote_fleet_agent.storage import atomic_text, save_json


def docker(args, *, timeout=120):
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError(
            "Docker CLI is missing. The demo command works without Docker."
        )
    if os.environ.get("DOCKER_HOST") and not os.environ["DOCKER_HOST"].startswith(
        ("unix://", "npipe://")
    ):
        raise ValueError(
            "lab commands require a local Docker engine; remote DOCKER_HOST is refused"
        )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("REMOTE_FLEET_AGENT_KEY_PATH", "REMOTE_FLEET_AGENT_CONFIG")
    }
    try:
        result = subprocess.run(
            [executable, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Docker timed out. Inspect lab status before retrying; existing lab files are retained."
        ) from exc
    if result.returncode:
        raise RuntimeError(
            (result.stderr or result.stdout)[-3000:].strip() or "Docker command failed"
        )
    return result.stdout.strip()


def local_engine():
    context = json.loads(docker(["context", "inspect"]))
    endpoint = context[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith(("unix://", "npipe://")):
        raise ValueError("lab commands require a local Docker context")
    docker(["info", "--format", "{{.OSType}}"])


def compose(directory, args, timeout=120):
    return docker(
        [
            "compose",
            "--project-directory",
            str(directory),
            "--file",
            str(directory / "compose.json"),
            *args,
        ],
        timeout=timeout,
    )


def init(directory):
    directory = prepare_directory(directory, "docker-linux")
    state = directory / ".state"
    state.mkdir(mode=0o700, exist_ok=True)
    key = state / "client_key"
    if not key.exists():
        private = asyncssh.generate_private_key("ssh-ed25519")
        atomic_text(key, private.export_private_key().decode())
        atomic_text(
            directory / "public_key.pub",
            "restrict " + private.export_public_key().decode(),
        )
        (directory / "public_key.pub").chmod(0o644)
    elif not (directory / "public_key.pub").is_file():
        raise ValueError(
            "existing lab client key has no public counterpart; refusing to replace it"
        )
    services = {}
    for suffix in ("a", "b"):
        folder = state / ("hostkeys-" + suffix)
        folder.mkdir(exist_ok=True, mode=0o700)
        hostkey = folder / "ssh_host_ed25519_key"
        if not hostkey.exists():
            private = asyncssh.generate_private_key("ssh-ed25519")
            atomic_text(hostkey, private.export_private_key().decode())
            atomic_text(
                hostkey.with_suffix(".pub"), private.export_public_key().decode()
            )
        services["device-" + suffix] = {
            "build": {"context": "."},
            "ports": ["127.0.0.1::2222"],
            "volumes": ["./.state/hostkeys-" + suffix + ":/hostkeys:ro"],
            "cap_drop": ["ALL"],
            "cap_add": ["SETUID", "SETGID", "SYS_CHROOT", "DAC_OVERRIDE"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": 64,
            "mem_limit": "256m",
            "cpus": 0.5,
            "read_only": True,
            "tmpfs": ["/run:size=16m,mode=755", "/tmp:size=16m,mode=1777"],
            "healthcheck": {
                "test": [
                    "CMD",
                    "python3",
                    "-c",
                    "import socket; socket.create_connection(('127.0.0.1',2222),2).close()",
                ],
                "interval": "2s",
                "timeout": "3s",
                "retries": 15,
                "start_period": "3s",
            },
            "restart": "no",
            "labels": {"remote-fleet-agent.lab": "true"},
        }
    config = {
        "name": "fleet-lab-" + hashlib.sha256(str(directory).encode()).hexdigest()[:10],
        "services": services,
    }
    for name in ("Dockerfile", "sshd_config"):
        target = directory / name
        desired = files("remote_fleet_agent").joinpath("lab_assets", name).read_text()
        if target.exists() and target.read_text() != desired:
            raise ValueError(
                "lab template was modified; use a new empty directory for this release"
            )
        atomic_text(target, desired)
    path = directory / "compose.json"
    if path.exists() and json.loads(path.read_text()) != config:
        raise ValueError("compose file was modified; use a new directory")
    save_json(path, config)
    atomic_text(
        directory / ".dockerignore", "**\n!Dockerfile\n!sshd_config\n!public_key.pub\n"
    )
    return directory


def validate(directory):
    directory = directory.expanduser().resolve()
    if not (directory / ".fleet-lab.json").is_file() or json.loads(
        (directory / ".fleet-lab.json").read_text()
    ) != {"kind": "docker-linux", "schema": 1}:
        raise ValueError("not an initialized fleet Docker lab")
    # Validate exact generated definitions before any engine operation; never run arbitrary compose files.
    init(directory)
    return directory


def configure(directory):
    hosts = []
    known = []
    for suffix in ("a", "b"):
        mapping = compose(directory, ["port", "device-" + suffix, "2222"])
        address, port = mapping.rsplit(":", 1)
        if address != "127.0.0.1" or not port.isdigit():
            raise ValueError("lab device must publish only on IPv4 loopback")
        port = int(port)
        public = (
            (directory / ".state" / ("hostkeys-" + suffix) / "ssh_host_ed25519_key.pub")
            .read_text()
            .strip()
        )
        known.append(f"[127.0.0.1]:{port} {public}")
        hosts.append(
            {
                "name": "linux-device-" + suffix,
                "target": "fleet@127.0.0.1",
                "port": port,
                "platform": "linux",
                "groups": ["all", "docker", "linux"],
                "approved": True,
            }
        )
    atomic_text(directory / "known_hosts", "\n".join(known) + "\n")
    save_json(
        directory / "fleet.json",
        {
            "client": "docker-lab",
            "known_hosts": "known_hosts",
            "hosts": hosts,
            "concurrency": 2,
            "query_timeout_seconds": 20,
            "maintenance_actions": [],
        },
    )
    FleetConfig.load(directory / "fleet.json")


def manage(mode, directory):
    if mode == "init":
        return {
            "directory": str(init(directory)),
            "next": "lab up --directory <same directory>",
            "note": "Linux containers only; no device has started.",
        }
    directory = validate(directory)
    local_engine()
    if mode == "up":
        compose(
            directory,
            ["up", "--detach", "--build", "--wait", "--wait-timeout", "60"],
            timeout=600,
        )
        configure(directory)
        return {
            "directory": str(directory),
            "status": "started",
            "devices": 2,
            "platform": "linux",
            "note": "Loopback-only SSH. Run lab collect to generate evidence and a dashboard.",
        }
    if mode == "down":
        compose(directory, ["down", "--remove-orphans"])
        return {
            "status": "stopped",
            "note": "Lab containers and network removed; local evidence and lab keys retained for reuse.",
        }
    if mode == "status":
        return {"containers": compose(directory, ["ps", "--format", "json"])}
    if mode == "collect":
        configure(directory)
        return asyncio.run(collect_fleet(directory))
    raise ValueError("unknown lab operation")
