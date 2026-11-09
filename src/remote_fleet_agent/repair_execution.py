"""Durable bounded plan execution; uncertain sends are never automatically replayed."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import UTC, datetime

from remote_fleet_agent.administration import (
    AdminRecords,
    EndpointClaim,
    decision_record,
    endpoint_key,
    require_administration,
    target_snapshot,
)
from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.repair_plan import (
    canonical_plan,
    evaluate_when,
    parse_time,
    plan_digest,
)
from remote_fleet_agent.storage import stamp
from remote_fleet_agent.support import required, run_bounded

log = logging.getLogger(__name__)
ACTIVE = {"queued", "executing", "scheduled", "awaiting_readiness"}


class RepairWorkflow(AdminRecords):
    kind = "repair_plan"

    def __init__(self, service):
        super().__init__(service)
        self.tasks = set()
        self.clock = lambda: datetime.now(UTC)
        self.instance = uuid.uuid4().hex
        self.max_queue = int(os.environ.get("REMOTE_FLEET_MAX_QUEUE", "64"))
        self.max_active = int(os.environ.get("REMOTE_FLEET_MAX_ACTIVE", "4"))
        if not 1 <= self.max_queue <= 10000 or not 1 <= self.max_active <= 16:
            raise ValueError("Invalid administration queue/concurrency configuration")
        db = service.store.db
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_steps'"
        ).fetchone():
            from remote_fleet_agent.host_platform import restrict_private

            backup_path = service.store.folder / "pre-administration.sqlite3"
            if backup_path.is_symlink():
                raise ValueError("Database backup cannot be a symlink")
            if not backup_path.exists():
                backup = sqlite3.connect(backup_path)
                try:
                    db.backup(backup)
                finally:
                    backup.close()
                restrict_private(backup_path)
        db.executescript("""CREATE TABLE IF NOT EXISTS execution_steps(run_id TEXT,target TEXT,step_id TEXT,phase TEXT,result TEXT,PRIMARY KEY(run_id,target,step_id));
CREATE TABLE IF NOT EXISTS endpoint_claims(endpoint TEXT PRIMARY KEY,run_id TEXT,target TEXT,phase TEXT);
CREATE TABLE IF NOT EXISTS scheduler_lease(id INTEGER PRIMARY KEY,owner TEXT,expires REAL);""")
        self.restore_claims()

    def restore_claims(self):
        for row in self.service.store.db.execute(
            "SELECT run_id,target FROM endpoint_claims"
        ).fetchall():
            source = self.service.store.db.execute(
                "SELECT client FROM records WHERE kind='repair_plan' AND id=?",
                (row["run_id"],),
            ).fetchone()
            if not source:
                continue
            record = self.service.store.get(self.kind, row["run_id"], source["client"])
            target = next(
                (t for t in record["targets"] if t["name"] == row["target"]), None
            )
            if target:
                try:
                    restored = EndpointClaim(
                        target,
                        row["run_id"],
                        self.service.store.folder / "endpoint-locks",
                    )
                    # Preserve the original worker identity when reconstructing a durable exclusion.
                    claim = json.loads(restored.path.read_text())
                    claim["owner_pid"] = record.get("controller_pid")
                    restored.path.write_text(json.dumps(claim))
                except Conflict:
                    pass

    def history(
        self, actor: dict, client: str, ident: str, offset: int = 0, limit: int = 10
    ) -> dict:
        self.get(actor, client, ident)
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 1 <= limit <= 20
        ):
            raise ValueError("Invalid history page")
        rows = self.service.store.db.execute(
            "SELECT result FROM execution_steps WHERE run_id=? AND result IS NOT NULL ORDER BY rowid LIMIT ? OFFSET ?",
            (ident, limit + 1, offset),
        ).fetchall()
        return {
            "items": [json.loads(r["result"]) for r in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def propose(self, actor: dict, client: str, body: dict) -> dict:
        actor = self.actor(actor)
        require_administration(self.service, actor, client, body.get("targets"))
        plan = canonical_plan(
            body, target_snapshot(self.service, actor, client, body.get("targets"))
        )
        if parse_time(plan["window"]["end"]) <= self.clock():
            raise ValueError("Plan window already expired")
        if plan.get("case_id"):
            self.service.support.get(actor, client, plan["case_id"])
        return self.create(
            actor,
            client,
            {
                "plan": plan,
                "targets": plan["target_context"],
                "digest": plan_digest(plan),
                "state": "awaiting_approval",
                "approval": None,
                "ready": None,
                "cancel_requested": False,
                "completed_targets": [],
                "results": [],
                "user_outcome": "pending",
                "created_at": stamp(),
                "updated_at": stamp(),
            },
        )

    def validate(self, actor: dict, client: str, record: dict):
        self.validate_targets(actor, client, record)
        if plan_digest(canonical_plan(record["plan"])) != record["digest"]:
            raise Conflict("Executable plan digest changed")
        if record.get("approval") and record["approval"]["digest"] != record["digest"]:
            raise Conflict("Approval digest changed")

    def decide(
        self, actor: dict, client: str, ident: str, body: dict, approve: bool
    ) -> dict:
        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        if record["state"] not in {
            "awaiting_approval",
            "approved",
            "scheduled",
            "awaiting_readiness",
            "queued",
        }:
            raise Conflict("Plan no longer awaiting a decision")
        self.validate(actor, client, record)
        if parse_time(record["plan"]["window"]["end"]) <= self.clock():
            raise Conflict("Approval window expired")
        return self.update(
            actor,
            client,
            record,
            {
                "state": "approved" if approve else "rejected",
                "approval": decision_record(actor, record["digest"], "repair")
                if approve
                else None,
            },
        )

    def execute(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor)
        record = self.checked(actor, client, ident, body)
        self.validate(actor, client, record)
        if record["state"] != "approved" or not record.get("approval"):
            raise Denied("Engineer approval required before execution")
        if parse_time(record["plan"]["window"]["end"]) <= self.clock():
            raise Conflict("Approval window expired")
        queued = self.service.store.db.execute(
            "SELECT count(*) FROM records WHERE kind='repair_plan' AND json_extract(data,'$.state') IN ('queued','executing','scheduled')"
        ).fetchone()[0]
        if queued >= self.max_queue:
            raise Conflict("Execution queue full; wait for current work")
        record = self.update(
            actor, client, record, {"state": "queued", "execution_actor": actor["id"]}
        )
        self.spawn(actor, client, ident)
        return record

    def spawn(self, actor: dict, client: str, ident: str):
        if len(self.tasks) >= self.max_active:
            return
        task = asyncio.create_task(self.run(actor, client, ident))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    def ready(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        if record["state"] != "awaiting_readiness":
            raise Conflict("Plan is not waiting for readiness")
        self.validate(actor, client, record)
        record = self.update(
            actor,
            client,
            record,
            {
                "state": "queued",
                "ready": {
                    **decision_record(actor, record["digest"], "readiness"),
                    "after_trial": bool(record["completed_targets"]),
                },
            },
        )
        self.spawn({"id": record["execution_actor"]}, client, ident)
        return record

    def cancel(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        if record["state"] not in ACTIVE:
            raise Conflict("No pending or running work to cancel")
        return self.update(
            actor,
            client,
            record,
            {
                "cancel_requested": True,
                "state": "cancellation_requested"
                if record["state"] == "executing"
                else "cancelled",
            },
        )

    def reconcile(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        if record["state"] not in {"unknown", "paused"}:
            raise Conflict("This plan does not need reconciliation")
        note = required(body.get("note"), "reconciliation observation")
        from remote_fleet_agent.administration import claim_is_running, claim_path

        for target in record["targets"]:
            path = claim_path(target, self.service.store.folder / "endpoint-locks")
            if claim_is_running(path):
                raise Conflict(
                    "A live controller still owns the command; wait for its outcome"
                )
        for target in record["targets"]:
            self.release_claim(ident, target)
        return self.update(
            actor,
            client,
            record,
            {
                "state": "reconciled",
                "reconciliation": {
                    "actor": actor["id"],
                    "at": stamp(),
                    "note": note,
                    "previous_state": record["state"],
                },
            },
        )

    def release_claim(self, ident: str, target: dict):
        root = (
            self.service.store.folder / "endpoint-locks"
            if target["simulation"]
            else None
        )
        # Reconstruct only the exact path owned by this run; no broad deletion.
        from remote_fleet_agent.administration import claim_path

        path = claim_path(target, root)
        if path.is_file() and json.loads(path.read_text()).get("owner") == ident:
            path.unlink()
        with self.service.store.db:
            self.service.store.db.execute(
                "DELETE FROM endpoint_claims WHERE run_id=? AND target=?",
                (ident, target["name"]),
            )

    def claim_step(self, ident: str, target: dict, step: dict) -> bool:
        db = self.service.store.db
        with db:
            db.execute(
                "INSERT OR IGNORE INTO execution_steps VALUES(?,?,?,'pending',NULL)",
                (ident, target["name"], step["id"]),
            )
            changed = db.execute(
                "UPDATE execution_steps SET phase='dispatch_intent' WHERE run_id=? AND target=? AND step_id=? AND phase='pending'",
                (ident, target["name"], step["id"]),
            )
        return changed.rowcount == 1

    def append_result(self, actor: dict, client: str, ident: str, result: dict):
        record = self.service.store.get(self.kind, ident, client)
        with self.service.store.db:
            self.service.store.db.execute(
                "UPDATE execution_steps SET phase=?,result=? WHERE run_id=? AND target=? AND step_id=?",
                (
                    "known_completed" if result["outcome"] == "known" else "unknown",
                    json.dumps(result),
                    ident,
                    result["host"],
                    result["step_id"],
                ),
            )
        self.update(
            actor,
            client,
            record,
            {
                "results": [*record["results"], result][-100:],
                "results_count": record.get("results_count", len(record["results"]))
                + 1,
            },
        )
        log.info(
            "repair_step",
            extra={
                "run_id": ident,
                "step_id": result["step_id"],
                "client": client,
                "outcome": result["outcome"],
                "elapsed_seconds": result["elapsed_seconds"],
            },
        )

    def fence(self, client: str, ident: str):
        row = self.service.store.db.execute(
            "SELECT owner,expires FROM scheduler_lease WHERE id=1"
        ).fetchone()
        record = self.service.store.get(self.kind, ident, client)
        if (
            not row
            or row["owner"] != self.instance
            or row["expires"] <= self.clock().timestamp()
            or record.get("controller") != self.instance
            or record["state"] not in {"executing", "cancellation_requested"}
        ):
            raise Conflict("Controller execution lease or run authority changed")

    async def dispatch(
        self, actor: dict, client: str, ident: str, target: dict, step: dict
    ) -> dict:
        self.fence(client, ident)
        if target["simulation"]:
            return {
                "exit_code": 0,
                "stdout": '{"passed":true,"detail":"Simulated; script NOT run"}'
                if step["kind"] == "check"
                else "Simulated; script NOT run",
                "stderr": "",
                "truncated": False,
            }
        async with asyncio.timeout(step["timeout_seconds"]):
            runtime = await self.service.runtime(client)
            connection = await runtime.connection(target["name"])
            self.validate(actor, client, self.get(actor, client, ident))
            self.fence(client, ident)
            return await run_bounded(
                connection, target["platform"], step["script"], step["timeout_seconds"]
            )

    async def run_target(
        self, actor: dict, client: str, ident: str, target: dict
    ) -> str:
        record = self.get(actor, client, ident)
        self.validate(actor, client, record)
        claim = EndpointClaim(
            target, ident, self.service.store.folder / "endpoint-locks"
        )
        known, failed = True, False
        try:
            with self.service.store.db:
                self.service.store.db.execute(
                    "INSERT INTO endpoint_claims VALUES(?,?,?,'running')",
                    (endpoint_key(target), ident, target["name"]),
                )
            results = {
                r["step_id"]: r
                for r in record["results"]
                if r["host"] == target["name"]
            }
            recovered = False
            for step in record["plan"]["steps"]:
                if target["name"] not in step["targets"]:
                    continue
                record = self.service.store.get(self.kind, ident, client)
                if record.get("cancel_requested"):
                    return "cancelled"
                self.validate(actor, client, record)
                self.fence(client, ident)
                recovery = step["kind"] == "recovery"
                if recovery and not failed:
                    continue
                # Explicit checks after a failed change may establish recovery conditions.
                if failed and not recovery and step["kind"] != "check":
                    continue
                window = (
                    record["plan"].get("recovery_window", record["plan"]["window"])
                    if recovery
                    else record["plan"]["window"]
                )
                if (
                    not parse_time(window["start"])
                    <= self.clock()
                    < parse_time(window["end"])
                ):
                    return "paused"
                condition = evaluate_when(step.get("when"), results)
                if condition is None:
                    return "paused"
                if condition is False:
                    continue
                if not self.claim_step(ident, target, step):
                    prior = self.service.store.db.execute(
                        "SELECT phase,result FROM execution_steps WHERE run_id=? AND target=? AND step_id=?",
                        (ident, target["name"], step["id"]),
                    ).fetchone()
                    if prior and prior["phase"] == "known_completed":
                        results[step["id"]] = json.loads(prior["result"])
                        continue
                    known = False
                    return "unknown"
                start = time.monotonic()
                result = {
                    "host": target["name"],
                    "step_id": step["id"],
                    "kind": step["kind"],
                    "outcome": "unknown",
                    "started_at": stamp(),
                    "simulation": target["simulation"],
                }
                try:
                    output = await self.dispatch(actor, client, ident, target, step)
                    result.update(output, outcome="known")
                    if step["kind"] == "check":
                        try:
                            checked = json.loads(output["stdout"].lstrip("\ufeff"))
                            result["passed"] = (
                                checked["passed"]
                                if output["exit_code"] == 0
                                and not output["truncated"]
                                and isinstance(checked, dict)
                                and type(checked.get("passed")) is bool
                                else None
                            )
                        except (ValueError, AttributeError):
                            result["passed"] = None
                    if output["exit_code"] != 0 or (
                        step["kind"] == "check" and result.get("passed") is not True
                    ):
                        failed = True
                        if recovery:
                            return "recovery_failed"
                    if recovery:
                        recovered = True
                except BaseException as exc:
                    result["error"] = type(exc).__name__
                    known = False
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                finally:
                    result.update(
                        elapsed_seconds=time.monotonic() - start, finished_at=stamp()
                    )
                    self.append_result(actor, client, ident, result)
                results[step["id"]] = result
                if not known:
                    return "unknown"
            if failed:
                return "recovered" if recovered else "verification_failed"
            ordered_results = list(results.values())
            last_change = max(
                (
                    i
                    for i, r in enumerate(ordered_results)
                    if r.get("kind") in {"change", "recovery"}
                ),
                default=-1,
            )
            verified = any(
                i > last_change and r.get("kind") == "check" and r.get("passed") is True
                for i, r in enumerate(ordered_results)
            )
            return "checks_passed" if verified else "command_completed"
        finally:
            if not known:
                claim.mark_unknown()
            if known:
                claim.release()
                with self.service.store.db:
                    self.service.store.db.execute(
                        "DELETE FROM endpoint_claims WHERE run_id=? AND target=?",
                        (ident, target["name"]),
                    )

    async def run(self, actor: dict, client: str, ident: str):
        try:
            if not self.lease():
                return
            record = self.get(actor, client, ident)
            if record["state"] not in {"queued", "scheduled"}:
                return
            self.validate(actor, client, record)
            now = self.clock()
            if now >= parse_time(record["plan"]["window"]["end"]):
                self.update(actor, client, record, {"state": "expired"})
                return
            if now < parse_time(record["plan"]["window"]["start"]):
                self.update(actor, client, record, {"state": "scheduled"})
                return
            if record["plan"]["disruption"] == "readiness" and not record.get("ready"):
                self.update(actor, client, record, {"state": "awaiting_readiness"})
                return
            record = self.update(
                actor,
                client,
                record,
                {
                    "state": "executing",
                    "controller": self.instance,
                    "controller_pid": os.getpid(),
                },
            )
            trial = record["plan"]["trial"]
            ordered = sorted(
                record["targets"], key=lambda t: (t["name"] != trial, t["name"])
            )
            remaining = [
                t for t in ordered if t["name"] not in record["completed_targets"]
            ]
            failures = 0
            all_verified = True
            for target in remaining:
                # Conservative sequential dispatch is within the approved batch ceiling.
                outcome = await self.run_target(actor, client, ident, target)
                self.fence(client, ident)
                record = self.service.store.get(self.kind, ident, client)
                if outcome == "unknown":
                    self.update(actor, client, record, {"state": "unknown"})
                    return
                if outcome == "cancelled":
                    self.update(actor, client, record, {"state": "cancelled"})
                    return
                if outcome != "checks_passed":
                    failures += 1
                    all_verified = False
                completed = [*record["completed_targets"], target["name"]]
                record = self.update(
                    actor, client, record, {"completed_targets": completed}
                )
                if (
                    (target["name"] == trial and outcome != "checks_passed")
                    or outcome in {"paused", "recovery_failed"}
                    or failures >= record["plan"]["stop_after_failures"]
                ):
                    self.update(
                        actor,
                        client,
                        record,
                        {"state": "paused", "last_outcome": outcome},
                    )
                    return
                if (
                    target["name"] == trial
                    and len(ordered) > 1
                    and record["plan"]["pause_after_trial"]
                    and not (record.get("ready") or {}).get("after_trial")
                ):
                    self.update(actor, client, record, {"state": "awaiting_readiness"})
                    return
            record = self.service.store.get(self.kind, ident, client)
            self.update(
                actor,
                client,
                record,
                {
                    "state": "checks_passed"
                    if all_verified and not failures
                    else "paused",
                    "finished_at": stamp(),
                },
            )
        except BaseException as exc:
            record = self.service.store.get(self.kind, ident, client)
            self.update(
                {"id": record["owner_id"]},
                client,
                record,
                {
                    "state": "unknown"
                    if record["state"]
                    in {"executing", "cancellation_requested", "unknown"}
                    else "paused",
                    "error": type(exc).__name__,
                },
            )
            if isinstance(exc, asyncio.CancelledError):
                raise

    def lease(self) -> bool:
        now = self.clock().timestamp()
        db = self.service.store.db
        with db:
            db.execute(
                "INSERT OR IGNORE INTO scheduler_lease VALUES(1,?,?)",
                (self.instance, now + 30),
            )
            changed = db.execute(
                "UPDATE scheduler_lease SET owner=?,expires=? WHERE id=1 AND (owner=? OR expires<?)",
                (self.instance, now + 30, self.instance, now),
            )
        if changed.rowcount != 1:
            return False
        for row in db.execute(
            "SELECT id,client FROM records WHERE kind='repair_plan' AND json_extract(data,'$.state') IN ('executing','cancellation_requested')"
        ).fetchall():
            record = self.service.store.get(self.kind, row["id"], row["client"])
            if record.get("controller") != self.instance:
                with db:
                    db.execute(
                        "UPDATE execution_steps SET phase='unknown' WHERE run_id=? AND phase='dispatch_intent'",
                        (record["id"],),
                    )
                self.update(
                    {"id": record["owner_id"]},
                    row["client"],
                    record,
                    {
                        "state": "unknown",
                        "error": "Controller interrupted; reconcile before new changes",
                    },
                )
        return True

    async def tick(self):
        if not self.lease():
            return
        db = self.service.store.db
        for row in db.execute(
            "SELECT id,client FROM records WHERE kind='repair_plan' AND json_extract(data,'$.state') IN ('queued','scheduled')"
        ).fetchall():
            if len(self.tasks) >= self.max_active:
                break
            record = self.service.store.get(self.kind, row["id"], row["client"])
            if record.get("execution_actor"):
                self.spawn(
                    {"id": record["execution_actor"]}, row["client"], record["id"]
                )

    async def wait(self):
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=False)

    async def close(self):
        if self.tasks:
            await asyncio.wait(list(self.tasks), timeout=2)
        for task in list(self.tasks):
            task.cancel()
        await self.wait()
        with self.service.store.db:
            self.service.store.db.execute(
                "DELETE FROM scheduler_lease WHERE owner=?", (self.instance,)
            )
