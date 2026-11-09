"""Shared administration identity, durable records and endpoint exclusion."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import asyncssh

from remote_fleet_agent.host_platform import process_exists
from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.storage import digest, private_dir, stamp


def target_snapshot(service, actor: dict, client: str, names: list[str]) -> list[dict]:
    config = service.endpoint_permission(actor, client, names)
    snapshots = []
    for host in service.selected(actor, client, hosts=names):
        address = host.target.split("@", 1)[1].casefold()
        pins = []
        if not config.simulation:
            known = asyncssh.read_known_hosts(str(config.known_hosts))
            pins = sorted(
                k.get_fingerprint() for k in known.match(address, address, host.port)[0]
            )
            if not pins:
                raise Denied("No pinned host identity for selected target")
        snapshot = {
            "name": host.name,
            "target": host.target,
            "port": host.port,
            "platform": host.platform,
            "site": host.site,
            "host_keys": pins,
            "simulation": config.simulation,
            "key_reference": digest(
                str(service.descriptor(client).get("key_path", "simulation"))
            ),
        }
        if not config.simulation:
            from remote_fleet_agent.device_identity import resolve_device

            snapshot["device_id"] = resolve_device(
                {**snapshot, "device_id": host.device_id}
            )
        snapshots.append(snapshot)
    return sorted(snapshots, key=lambda item: item["name"])


def require_administration(service, actor: dict, client: str, names: list[str]):
    if not service.store.integrity:
        raise Denied("Journal integrity check failed; administration blocked")
    config = service.endpoint_permission(actor, client, names)
    if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1" or (
        not config.simulation and not config.administration_enabled
    ):
        raise Denied("Administration disabled or audit-only mode active")
    return config


def endpoint_key(target: dict) -> str:
    if not target.get("simulation"):
        from remote_fleet_agent.device_identity import resolve_device

        return resolve_device(target)
    # Fictional devices stay in their workspace namespace and never enroll.
    identity = target["host_keys"] or [target["target"].split("@", 1)[1].casefold()]
    return digest({"identity": identity, "port": target["port"]})


def claim_path(target: dict, simulation_folder: Path | None = None) -> Path:
    root = (
        simulation_folder
        if target.get("simulation")
        else Path(
            os.environ.get(
                "REMOTE_FLEET_ENDPOINT_LOCK_DIR",
                str(Path.home() / ".remote-fleet-agent" / "endpoint-locks"),
            )
        )
    )
    if not root.is_absolute():
        raise ValueError("Endpoint lock directory must be absolute")
    root = private_dir(root)
    if not target.get("simulation"):
        # Old versions used a hash of the complete pin set. Never silently
        # bypass their outstanding claims during upgrade, including unknown work.
        identity = target["host_keys"] or [target["target"].split("@", 1)[1].casefold()]
        legacy = root / (
            digest({"identity": identity, "port": target["port"]}) + ".lock"
        )
        if legacy.exists():
            return legacy  # Reconciliation of the original record can still find it.
        for candidate in root.glob("*.lock"):
            if not json.loads(candidate.read_text(encoding="utf-8")).get("device_id"):
                raise Conflict(
                    "Legacy device claims remain; stop old controllers and reconcile their work before upgrading"
                )
    return root / (endpoint_key(target) + ".lock")


class EndpointClaim:
    """Cross-interface non-expiring exclusion; unknown dispatch retains the claim."""

    def __init__(self, target: dict, owner: str, simulation_folder: Path | None = None):
        self.path = claim_path(target, simulation_folder)
        self.owner = owner
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise Conflict(
                "Device has active or uncertain work; reconcile before continuing"
            ) from None
        with os.fdopen(fd, "w") as f:
            json.dump(
                {
                    "owner": owner,
                    "device_id": self.path.stem
                    if not target.get("simulation")
                    else None,
                    "owner_pid": os.getpid(),
                    "active": True,
                    "created_at": stamp(),
                },
                f,
            )
            f.flush()
            os.fsync(f.fileno())

    def mark_unknown(self):
        if self.path.exists():
            value = json.loads(self.path.read_text())
            if value.get("owner") == self.owner:
                from remote_fleet_agent.storage import atomic_text

                value["active"] = False
                atomic_text(self.path, json.dumps(value))

    def release(self):
        if (
            self.path.exists()
            and json.loads(self.path.read_text()).get("owner") == self.owner
        ):
            self.path.unlink()


def claim_is_running(path: Path) -> bool:
    if not path.exists():
        return False
    value = json.loads(path.read_text())
    if value.get("active") is False:
        return False
    pid = value.get("owner_pid")
    if type(pid) is not int:
        return True  # Older or malformed claims need explicit administrator inspection.
    if pid == os.getpid():
        return True
    try:
        return process_exists(pid)
    except OSError:
        return True


class AdminRecords:
    kind = ""

    def __init__(self, service):
        self.service = service

    def actor(self, actor: dict, permission: str = "collect") -> dict:
        current = self.service.store.operator(actor["id"])
        self.service.store.require(current, permission)
        return current

    def get(self, actor: dict, client: str, ident: str) -> dict:
        actor = self.actor(actor, "read")
        record = self.service.store.get(self.kind, ident, client)
        self.service.selected(
            actor, client, hosts=[t["name"] for t in record["targets"]]
        )
        for target in record["targets"]:
            self.service.require_site(actor, client, target["site"])
        return record

    def list(self, actor: dict, client: str) -> list[dict]:
        actor = self.actor(actor, "read")
        self.service.grants(actor, client)
        result = []
        for r in self.service.store.list(self.kind, client):
            try:
                full = self.get(actor, client, r["id"])
                summary = {
                    k: v
                    for k, v in full.items()
                    if k not in {"commands", "results", "plan"}
                }
                if full.get("plan"):
                    summary["plan"] = {
                        "title": full["plan"]["title"],
                        "impact": full["plan"]["impact"],
                    }
                result.append(summary)
            except Denied:
                continue
        return result[:1000]

    @staticmethod
    def present(record: dict) -> dict:
        view = dict(record)
        for name in ("commands", "results"):
            if name in view:
                view[name + "_count"] = record.get(name + "_count", len(view[name]))
                view[name] = view[name][-10:]
        return view

    def checked(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        record = self.get(actor, client, ident)
        if (
            type(body.get("revision")) is not int
            or body["revision"] != record["revision"]
        ):
            raise Conflict("Record changed; refresh before continuing")
        if body.get("digest") != record.get("digest"):
            raise Conflict("Digest changed; review the current targets and plan")
        return record

    def create(self, actor: dict, client: str, values: dict) -> dict:
        return self.service.store.save(self.kind, client, "", actor, values)

    def update(self, actor: dict, client: str, record: dict, values: dict) -> dict:
        data = {
            k: v
            for k, v in record.items()
            if k not in {"id", "site", "owner_id", "revision"}
        }
        data.update(values, updated_at=stamp())
        db = self.service.store.db
        with db:
            changed = db.execute(
                "UPDATE records SET revision=revision+1,data=? WHERE kind=? AND id=? AND client=? AND revision=?",
                (json.dumps(data), self.kind, record["id"], client, record["revision"]),
            )
            if changed.rowcount != 1:
                raise Conflict("Record changed; refresh before continuing")
            self.service.store.audit(
                actor,
                self.kind + "." + data["state"],
                client,
                "",
                {"record": record["id"], "digest": data.get("digest")},
            )
        return self.service.store.get(self.kind, record["id"], client)

    def validate_targets(self, actor: dict, client: str, record: dict):
        actor = self.actor(actor)
        names = [t["name"] for t in record["targets"]]
        require_administration(self.service, actor, client, names)
        if target_snapshot(self.service, actor, client, names) != record["targets"]:
            raise Conflict("Confirmed target identity or connection context changed")
        decision = record.get("approval")
        if decision:
            approver = self.actor({"id": decision["actor"]}, "approve_repairs")
            self.service.endpoint_permission(approver, client, names)
        return actor


def decision_record(actor: dict, digest_value: str, purpose: str) -> dict:
    return {
        "actor": actor["id"],
        "name": actor["name"],
        "digest": digest_value,
        "purpose": purpose,
        "at": stamp(),
        "channel": "workspace",
        "nonce": uuid.uuid4().hex,
    }


def legacy_target(config, host) -> dict:
    """Shared exclusion identity for legacy interfaces; SSH still verifies pins."""
    address = host.target.split("@", 1)[1].casefold()
    try:
        pins = (
            []
            if config.simulation
            else sorted(
                k.get_fingerprint()
                for k in asyncssh.read_known_hosts(str(config.known_hosts)).match(
                    address, address, host.port
                )[0]
            )
        )
    except (ValueError, OSError):
        # A fixture's fake pin file can still share exclusion; real SSH rejects it.
        pins = [hashlib.sha256(config.known_hosts.read_bytes()).hexdigest()]
    return {
        "name": host.name,
        "target": host.target,
        "port": host.port,
        "host_keys": pins,
        "simulation": config.simulation,
        "device_id": host.device_id,
    }
