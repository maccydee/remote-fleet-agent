"""Controller-wide device identities, independent of workspace and SSH key order."""

from __future__ import annotations

import ipaddress
import os
import sqlite3
import uuid
from pathlib import Path

from remote_fleet_agent.host_platform import restrict_private
from remote_fleet_agent.live_store import Conflict
from remote_fleet_agent.storage import private_dir


def lock_root() -> Path:
    root = Path(
        os.environ.get(
            "REMOTE_FLEET_ENDPOINT_LOCK_DIR",
            str(Path.home() / ".remote-fleet-agent/endpoint-locks"),
        )
    )
    if not root.is_absolute():
        raise ValueError("Endpoint lock directory must be absolute")
    return private_dir(root)


def resolve_device(target: dict) -> str:
    """Enroll verified aliases atomically; ambiguous existing identities fail closed.

    Disjoint key sets at different addresses cannot establish physical identity.
    Such aliases must carry the same operator-enrolled device_id in fleet config.
    """
    address = target["target"].split("@", 1)[1].casefold().rstrip(".")
    try:
        address = str(ipaddress.ip_address(address))
    except ValueError:
        pass
    pins = target["host_keys"]
    if not pins:
        raise ValueError("Device identity requires verified SSH host keys")
    aliases = sorted(
        {f"endpoint:{address}:{target['port']}", *("key:" + p for p in pins)}
    )
    explicit = target.get("device_id")
    if explicit:
        explicit = uuid.UUID(explicit).hex
    path = lock_root() / "device-identities.sqlite3"
    if path.exists():
        restrict_private(path)
    db = sqlite3.connect(path, timeout=10)
    try:
        restrict_private(path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, device_id TEXT NOT NULL)"
        )
        db.execute("BEGIN IMMEDIATE")
        found = {
            row[0]
            for alias in aliases
            for row in db.execute(
                "SELECT device_id FROM aliases WHERE alias=?", (alias,)
            )
        }
        if len(found) > 1 or (explicit and found and explicit not in found):
            raise Conflict(
                "Device aliases refer to conflicting enrolled identities; reconcile enrollment before continuing"
            )
        ident = explicit or next(iter(found), None) or uuid.uuid4().hex
        db.executemany(
            "INSERT OR IGNORE INTO aliases VALUES(?,?)",
            [(alias, ident) for alias in aliases],
        )
        db.commit()
        return ident
    finally:
        db.close()
