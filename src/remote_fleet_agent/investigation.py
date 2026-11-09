"""Once-per-target investigation confirmation and bounded custom diagnostics."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime, timedelta

from remote_fleet_agent.administration import (
    AdminRecords,
    EndpointClaim,
    decision_record,
    require_administration,
    target_snapshot,
)
from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.storage import digest, stamp
from remote_fleet_agent.support import required, run_bounded


class InvestigationWorkflow(AdminRecords):
    kind = "investigation"

    def __init__(self, service):
        super().__init__(service)
        self.running = set()
        service.store.db.execute(
            "CREATE TABLE IF NOT EXISTS investigation_commands(id TEXT PRIMARY KEY,session TEXT,client TEXT,data TEXT)"
        )
        service.store.db.commit()

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
            "SELECT data FROM investigation_commands WHERE session=? AND client=? ORDER BY rowid LIMIT ? OFFSET ?",
            (ident, client, limit + 1, offset),
        ).fetchall()
        return {
            "items": [json.loads(r["data"]) for r in rows[:limit]],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def reconcile(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        from remote_fleet_agent.administration import claim_is_running, claim_path

        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        host = body.get("host")
        commands = [c for c in record["commands"] if c["host"] == host]
        if not commands:
            rows = self.service.store.db.execute(
                "SELECT data FROM investigation_commands WHERE session=? AND client=? ORDER BY rowid DESC",
                (ident, client),
            )
            for row in rows:
                entry = json.loads(row["data"])
                if entry.get("host") == host:
                    commands = [entry]
                    break
        if (
            (ident, host) in self.running
            or not commands
            or commands[-1]["outcome"] not in {"unknown", "dispatch_intent"}
        ):
            raise Conflict("Only a completed unknown diagnostic may be reconciled")
        note = required(body.get("note"), "reconciliation observation")
        target = next(t for t in record["targets"] if t["name"] == host)
        path = claim_path(target, self.service.store.folder / "endpoint-locks")
        if claim_is_running(path):
            raise Conflict("A live controller still owns this diagnostic")
        owns_claim = (
            path.is_file() and json.loads(path.read_text()).get("owner") == ident
        )
        if commands[-1]["outcome"] == "dispatch_intent" and not owns_claim:
            raise Conflict(
                "Missing owned claim for interrupted diagnostic; inspect controller evidence"
            )
        if owns_claim:
            path.unlink()
        return self.update(
            actor,
            client,
            record,
            {
                "reconciliations": [
                    *record.get("reconciliations", []),
                    {
                        "host": host,
                        "actor": actor["id"],
                        "at": stamp(),
                        "note": note,
                        "prior_outcome": commands[-1]["outcome"],
                        "outcome_remains_unknown": True,
                    },
                ]
            },
        )

    def request(self, actor: dict, client: str, body: dict) -> dict:
        actor = self.actor(actor)
        names = body.get("targets")
        if not isinstance(names, list) or not 1 <= len(names) <= 100:
            raise ValueError("Select 1..100 named targets")
        targets = target_snapshot(self.service, actor, client, names)
        require_administration(self.service, actor, client, names)
        return self.create(
            actor,
            client,
            {
                "targets": targets,
                "digest": digest(targets),
                "state": "pending_target_confirmation",
                "approval": None,
                "case_id": body.get("case_id"),
                "created_at": stamp(),
                "updated_at": stamp(),
                "expires_at": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
                "commands": [],
            },
        )

    def confirm(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor, "approve_repairs")
        record = self.checked(actor, client, ident, body)
        if record["state"] != "pending_target_confirmation":
            raise Conflict("Investigation confirmation already consumed")
        self.validate_targets(actor, client, record)
        return self.update(
            actor,
            client,
            record,
            {
                "state": "active",
                "approval": decision_record(actor, record["digest"], "investigation"),
            },
        )

    def validate(self, actor: dict, client: str, ident: str) -> dict:
        record = self.get(actor, client, ident)
        self.validate_targets(actor, client, record)
        if record["state"] != "active" or not record.get("approval"):
            raise Denied("Engineer target confirmation required")
        if datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC):
            raise Conflict("Investigation session expired")
        if record.get("case_id"):
            case = self.service.support.get(actor, client, record["case_id"])
            if case["state"] == "resolved":
                raise Conflict("Support investigation closed")
        return record

    def close(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor)
        record = self.checked(actor, client, ident, body)
        if record["state"] == "closed":
            raise Conflict("Investigation already closed")
        return self.update(actor, client, record, {"state": "closed"})

    async def run(self, actor: dict, client: str, ident: str, body: dict) -> dict:
        actor = self.actor(actor)
        record = self.validate(actor, client, ident)
        host = body.get("host")
        targets = {t["name"]: t for t in record["targets"]}
        if host not in targets:
            raise Denied("Device outside confirmed investigation")
        script = required(body.get("script"), "script", 32768)
        timeout = body.get("timeout_seconds", 60)
        if type(timeout) is not int or not 5 <= timeout <= 300:
            raise ValueError("timeout_seconds must be integer 5..300")
        target = targets[host]
        if len(self.running) >= 4:
            raise Conflict("Diagnostic concurrency limit reached")
        claim = EndpointClaim(
            target, ident, self.service.store.folder / "endpoint-locks"
        )
        self.running.add((ident, host))
        command_id = uuid.uuid4().hex
        before = time.monotonic()
        entry = {
            "host": host,
            "script": script,
            "script_digest": digest(script),
            "started_at": stamp(),
            "outcome": "dispatch_intent",
            "identity": target,
        }
        known = True
        try:
            with self.service.store.db:
                self.service.store.db.execute(
                    "INSERT INTO investigation_commands VALUES(?,?,?,?)",
                    (command_id, ident, client, json.dumps(entry)),
                )
            record = self.update(
                actor,
                client,
                record,
                {
                    "commands": [*record["commands"], entry][-100:],
                    "commands_count": record.get(
                        "commands_count", len(record["commands"])
                    )
                    + 1,
                },
            )
            known = False
            if target["simulation"]:
                result = {
                    "simulation": True,
                    "stdout": "Simulated diagnostic; script NOT run",
                    "stderr": "",
                    "exit_code": 0,
                    "truncated": False,
                }
            else:
                async with asyncio.timeout(timeout):
                    runtime = await self.service.runtime(client)
                    conn = await runtime.connection(host)
                    self.validate(actor, client, ident)
                    result = await run_bounded(
                        conn, target["platform"], script, timeout
                    )
                result["simulation"] = False
            known = True
            entry.update(
                result,
                outcome="known",
                elapsed_seconds=time.monotonic() - before,
                finished_at=stamp(),
            )
        except BaseException as exc:
            entry.update(
                outcome="unknown",
                error=type(exc).__name__,
                elapsed_seconds=time.monotonic() - before,
                finished_at=stamp(),
            )
            raise
        finally:
            self.running.discard((ident, host))
            with self.service.store.db:
                self.service.store.db.execute(
                    "UPDATE investigation_commands SET data=? WHERE id=?",
                    (json.dumps(entry), command_id),
                )
            latest = self.service.store.get(self.kind, ident, client)
            commands = list(latest["commands"])
            for i, item in enumerate(commands):
                if item["started_at"] == entry["started_at"] and item["host"] == host:
                    commands[i] = entry
            self.update(actor, client, latest, {"commands": commands})
            if not known:
                claim.mark_unknown()
            if known:
                claim.release()
        return entry
