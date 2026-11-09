"""Reviewed maintenance batches: canary first, explicit continuation, bounded waves."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

from remote_fleet_agent.storage import (
    digest,
    locked,
    private_dir,
    read_json,
    save_json,
    stamp,
    valid_id,
)
from remote_fleet_agent.workspace import parse_time


class BatchManager:
    def __init__(self, runtime, root):
        self.runtime = runtime
        self.config = runtime.config
        root = Path(root) / "simulation" if self.config.simulation else Path(root)
        self.path = private_dir(root / self.config.client / "batches")
        self.active = set()

    def file(self, ident):
        return self.path / (valid_id(ident) + ".json")

    def save(self, batch):
        save_json(self.file(batch["id"]), batch)

    def get(self, ident):
        batch = read_json(self.file(ident))
        if batch["spec"]["client"] != self.config.client:
            raise ValueError("batch belongs to another engagement")
        return batch

    async def preview(
        self, action, hosts=None, group=None, canary=None, wave_size=1, max_failures=1
    ):
        if action not in self.config.maintenance_actions:
            raise ValueError("maintenance action is not enabled")
        selected = self.config.select(hosts, group)
        if (
            type(wave_size) is not int
            or not 1 <= wave_size <= self.config.concurrency
            or type(max_failures) is not int
            or not 1 <= max_failures <= len(selected)
        ):
            raise ValueError(
                "wave size must fit engagement concurrency; failure limit must fit scope"
            )
        canary = canary or selected[0].name
        if canary not in [h.name for h in selected]:
            raise ValueError("canary must be a named host in the selected scope")
        plans, errors = {}, {}
        # Fixed reads only. Bound preview fan-out independently of the mutation waves.
        semaphore = asyncio.Semaphore(self.config.concurrency)

        async def one(host):
            async with semaphore:
                try:
                    readiness = await self.runtime.collect(host.name, "readiness")
                    if (
                        readiness.get("readiness", {}).get("maintenance_ready")
                        is not True
                    ):
                        raise ValueError(
                            "readiness did not establish administrator access and disk headroom"
                        )
                    p = await self.runtime.maintenance.preview(host.name, action)
                    plans[host.name] = {
                        k: p[k]
                        for k in (
                            "id",
                            "digest",
                            "expires_at",
                            "before",
                            "effect",
                            "recovery",
                        )
                    }
                    plans[host.name]["readiness"] = readiness["readiness"]
                    plans[host.name]["readiness_checked_at"] = readiness.get(
                        "finished_at"
                    )
                except Exception as exc:  # noqa: BLE001 - retain per-host preview failures without silently expanding scope
                    errors[host.name] = str(exc)[:1000]

        await asyncio.gather(*(one(h) for h in selected))
        spec = {
            "id": uuid.uuid4().hex,
            "client": self.config.client,
            "created_at": stamp(),
            "action": action,
            "targets": {h.name: h.target for h in selected},
            "ports": {h.name: h.port for h in selected},
            "platforms": {h.name: h.platform for h in selected},
            "canary": canary,
            "wave_size": wave_size,
            "max_failures": max_failures,
            "plans": plans,
            "preview_errors": errors,
            "expires_at": min(
                (p["expires_at"] for p in plans.values()), default=stamp()
            ),
        }
        batch = {
            "id": spec["id"],
            "spec": spec,
            "digest": digest(spec),
            "status": "preview" if canary in plans else "blocked",
            "hosts": {
                h.name: {
                    "status": "preview" if h.name in plans else "preview_failed",
                    "error": errors.get(h.name),
                }
                for h in selected
            },
        }
        self.save(batch)
        return batch

    def validate(self, batch, expected_digest):
        spec = batch["spec"]
        if digest(spec) != expected_digest or batch["digest"] != expected_digest:
            raise ValueError("batch preview digest does not match")
        if parse_time(spec["expires_at"]) <= datetime.now(UTC):
            raise ValueError("batch preview expired; create a new preview")
        if spec["action"] not in self.config.maintenance_actions:
            raise ValueError("maintenance action is no longer enabled")
        for name, target in spec["targets"].items():
            host = self.config.resolve(name)
            if (
                host.target != target
                or host.port != spec.get("ports", {}).get(name, 22)
                or host.platform != spec.get("platforms", {}).get(name, "windows")
            ):
                raise ValueError("batch scope changed since preview")

    def cancelled(self, ident):
        return self.file(ident).with_suffix(".cancel").exists()

    def cancel(self, ident):
        batch = self.get(ident)
        if batch["status"] not in (
            "preview",
            "canary_running",
            "awaiting_continuation",
            "running",
        ):
            raise ValueError("batch is already terminal")
        # A separate marker can be written while the execution lock is held.
        save_json(self.file(ident).with_suffix(".cancel"), {"requested_at": stamp()})
        if ident not in self.active:
            try:
                with locked(self.file(ident).with_suffix(".lock")):
                    current = self.get(ident)
                    if current["status"] in ("preview", "awaiting_continuation"):
                        current["status"] = "cancelled"
                        for entry in current["hosts"].values():
                            if entry["status"] == "preview":
                                entry["status"] = "not_run"
                        self.save(current)
            except ValueError:
                # Another process may own the execution lock; the durable marker still applies.
                pass
        return {
            "id": ident,
            "cancel_requested": True,
            "note": "No new waves will start. Already dispatched operations may finish; inspect the saved results.",
        }

    async def execute(self, ident, expected_digest, *, continuation=False):
        with locked(self.file(ident).with_suffix(".lock")):
            batch = self.get(ident)
            self.validate(batch, expected_digest)
            expected = "awaiting_continuation" if continuation else "preview"
            if batch["status"] != expected:
                raise ValueError(
                    "run the canary once, review its result, then explicitly continue once"
                )
            spec = batch["spec"]
            batch["status"] = "running" if continuation else "canary_running"
            batch["started_at"] = stamp()
            self.save(batch)
            self.active.add(ident)

            async def one(name):
                entry = batch["hosts"][name]
                p = spec["plans"][name]
                entry.update(status="running", plan_id=p["id"], started_at=stamp())
                self.save(batch)
                try:
                    result = await self.runtime.maintenance.execute(
                        p["id"], p["digest"]
                    )
                    entry.update(
                        status=result["status"],
                        changed=result.get("result", {}).get("changed"),
                        result_plan_id=result["id"],
                    )
                except Exception as exc:  # noqa: BLE001 - stop later waves and preserve uncertainty for manual inspection
                    entry.update(status="unknown", error=str(exc)[:1000])
                entry["finished_at"] = stamp()
                self.save(batch)

            try:
                if self.cancelled(ident):
                    batch["status"] = "cancelled"
                elif not continuation:
                    await one(spec["canary"])
                    if self.cancelled(ident):
                        batch["status"] = "cancelled"
                    else:
                        batch["status"] = (
                            "awaiting_continuation"
                            if batch["hosts"][spec["canary"]]["status"] == "complete"
                            else "halted"
                        )
                else:
                    names = [
                        n
                        for n in spec["targets"]
                        if n != spec["canary"] and n in spec["plans"]
                    ]
                    batch["status"] = "complete"
                    for offset in range(0, len(names), spec["wave_size"]):
                        if self.cancelled(ident):
                            batch["status"] = "cancelled"
                            break
                        # Preview failures are recorded/skipped, not dispatched; execution failures count here.
                        failures = sum(
                            h["status"] in ("unknown", "failed", "rollback_unknown")
                            for h in batch["hosts"].values()
                        )
                        if failures >= spec["max_failures"]:
                            batch["status"] = "halted"
                            break
                        if parse_time(spec["expires_at"]) <= datetime.now(UTC):
                            batch["status"] = "expired"
                            break
                        batch["status"] = "running"
                        self.save(batch)
                        tasks = [
                            asyncio.create_task(one(n))
                            for n in names[offset : offset + spec["wave_size"]]
                        ]
                        try:
                            await asyncio.gather(*tasks)
                        except BaseException:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
                            raise
                    failures = any(
                        h["status"] in ("unknown", "failed", "rollback_unknown")
                        for h in batch["hosts"].values()
                    )
                    if batch["status"] == "running":
                        batch["status"] = "halted" if failures else "complete"
                    if batch["status"] == "complete" and spec["preview_errors"]:
                        batch["status"] = "partial"
                if batch["status"] not in ("awaiting_continuation",):
                    for entry in batch["hosts"].values():
                        if entry["status"] == "preview":
                            entry["status"] = "not_run"
                batch["finished_at"] = stamp()
                self.save(batch)
                return batch
            except BaseException:
                batch["status"] = "interrupted"
                for entry in batch["hosts"].values():
                    if entry["status"] == "running":
                        entry["status"] = "unknown"
                    elif entry["status"] == "preview":
                        entry["status"] = "not_run"
                self.save(batch)
                raise
            finally:
                self.active.discard(ident)
