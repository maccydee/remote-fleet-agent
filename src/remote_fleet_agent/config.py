"""Explicit engagement scope shared by the CLI and MCP server."""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class FleetHost:
    name: str
    target: str
    groups: tuple[str, ...] = ()
    platform: str = "windows"
    port: int = 22
    scenario: str = "healthy"
    site: str = "main"


@dataclass(frozen=True)
class FleetConfig:
    client: str
    known_hosts: Path
    hosts: tuple[FleetHost, ...]
    concurrency: int = 4
    query_timeout: int = 30
    maintenance_actions: tuple[str, ...] = ()
    simulation: bool = False
    administration_enabled: bool = False

    @classmethod
    def load(cls, path: str | Path, *, require_known_hosts: bool = True) -> FleetConfig:
        path = Path(path).expanduser().resolve()
        raw = json.loads(path.read_text())
        client = raw.get("client", "")
        if not isinstance(client, str) or not _NAME.fullmatch(client):
            raise ValueError(
                "client must be a short name using letters, numbers, dots or hyphens"
            )
        simulation = raw.get("simulation", False)
        if type(simulation) is not bool:
            raise ValueError("simulation must be a boolean")
        administration = raw.get("administration_enabled", False)
        if type(administration) is not bool:
            raise ValueError("administration_enabled must be a boolean")
        known = raw.get("known_hosts")
        if not isinstance(known, str) or not known:
            raise ValueError("known_hosts must identify the verified host-key file")
        known_path = Path(known).expanduser()
        if not known_path.is_absolute():
            known_path = path.parent / known_path
        known_path = known_path.resolve()
        if (
            require_known_hosts
            and not simulation
            and (not known_path.is_file() or not known_path.read_text().strip())
        ):
            raise ValueError(
                "known_hosts must be an existing, nonempty verified host-key file"
            )
        hosts = []
        names: set[str] = set()
        targets: set[tuple[str, int]] = set()
        for item in raw.get("hosts", []):
            if item.get("approved") is not True:
                continue
            name, target = item.get("name", ""), item.get("target", "")
            if not isinstance(name, str) or not _NAME.fullmatch(name):
                raise ValueError("every approved host needs a valid name")
            if not isinstance(target, str) or target.count("@") != 1:
                raise ValueError("host targets must be username@address")
            username, address = target.split("@")
            if not re.fullmatch(r"[A-Za-z0-9_.\\-]+", username):
                raise ValueError("invalid SSH username")
            try:
                ipaddress.ip_address(address)
            except ValueError:
                if len(address) > 253 or not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", address
                ):
                    raise ValueError("invalid host address") from None
            platform = item.get("platform", "windows")
            port = item.get("port", 22)
            scenario = item.get("scenario", "healthy")
            if platform not in ("windows", "linux", "macos"):
                raise ValueError("platform must be windows, linux or macos")
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("port must be an integer between 1 and 65535")
            if scenario not in ("healthy", "attention", "limited", "offline"):
                raise ValueError("invalid simulation scenario")
            if not simulation and scenario != "healthy":
                raise ValueError("scenarios require simulation mode")
            site = item.get("site", "main")
            if not isinstance(site, str) or not _NAME.fullmatch(site):
                raise ValueError("site must be a short valid name")
            endpoint = (target.casefold(), port)
            if name.casefold() in names or endpoint in targets:
                raise ValueError("host names and targets must be unique")
            names.add(name.casefold())
            targets.add(endpoint)
            groups = item.get("groups", [])
            if not isinstance(groups, list) or any(
                not isinstance(g, str) or not _NAME.fullmatch(g) for g in groups
            ):
                raise ValueError("groups must be a list of valid names")
            hosts.append(
                FleetHost(name, target, tuple(groups), platform, port, scenario, site)
            )
        concurrency = raw.get("concurrency", 4)
        timeout = raw.get("query_timeout_seconds", 30)
        if type(concurrency) is not int or not 1 <= concurrency <= 16:
            raise ValueError("concurrency must be between 1 and 16")
        if type(timeout) is not int or not 5 <= timeout <= 120:
            raise ValueError("query_timeout_seconds must be between 5 and 120")
        from remote_fleet_agent.maintenance import ACTIONS

        actions = raw.get("maintenance_actions", [])
        if (
            not isinstance(actions, list)
            or any(not isinstance(a, str) or a not in ACTIONS for a in actions)
            or len(set(actions)) != len(actions)
        ):
            raise ValueError(
                "maintenance_actions must be a unique list of supported actions"
            )
        if simulation and actions:
            raise ValueError(
                "simulation is collection-only; maintenance actions must be empty"
            )
        return cls(
            client,
            known_path,
            tuple(hosts),
            concurrency,
            timeout,
            tuple(actions),
            simulation,
            administration,
        )

    def resolve(self, name: str) -> FleetHost:
        for host in self.hosts:
            if name == host.name:
                return host
        matches = [host for host in self.hosts if name == host.target]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError("target matches multiple ports; use the host name")
        raise ValueError(f"host is not in this engagement's approved scope: {name}")

    def select(
        self, hosts: list[str] | None = None, group: str | None = None
    ) -> list[FleetHost]:
        if bool(hosts) == bool(group):
            raise ValueError("specify either a nonempty host list or one group")
        selected = (
            [h for h in self.hosts if group in h.groups]
            if group
            else [self.resolve(n) for n in hosts or []]
        )
        if not selected:
            raise ValueError("the selected group has no approved hosts")
        if len({h.name for h in selected}) != len(selected):
            raise ValueError("duplicate hosts are not allowed")
        return selected
