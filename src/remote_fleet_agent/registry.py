from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_REGISTRY_DIR = Path.home() / ".remote-fleet-agent" / "clients"


class HostNotFoundError(Exception):
    """Raised when a host name/nickname cannot be resolved for a client."""


class DuplicateNicknameError(Exception):
    """Raised when a nickname is already registered for a client."""


@dataclass
class Host:
    hostname: str
    nickname: str
    added_at: float
    last_seen: float | None = None


class ClientRegistry:
    """Per-client host list, persisted as JSON at <base_dir>/<client>.json."""

    def __init__(self, client: str, base_dir: Path = DEFAULT_REGISTRY_DIR) -> None:
        self._client = client
        self._path = Path(base_dir) / f"{client}.json"

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())

    def _save(self, hosts: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(hosts, indent=2))

    def add_host(self, hostname: str, nickname: str) -> Host:
        hosts = self._load()
        if any(h["nickname"] == nickname for h in hosts):
            raise DuplicateNicknameError(
                f"nickname '{nickname}' already registered for client '{self._client}'"
            )
        host = Host(hostname=hostname, nickname=nickname, added_at=time.time())
        hosts.append(asdict(host))
        self._save(hosts)
        return host

    def resolve(self, name: str) -> Host:
        for raw in self._load():
            if raw["nickname"] == name or raw["hostname"] == name:
                return Host(**raw)
        raise HostNotFoundError(
            f"host '{name}' not found for client '{self._client}'"
        )

    def list_hosts(self) -> list[Host]:
        return [Host(**raw) for raw in self._load()]
