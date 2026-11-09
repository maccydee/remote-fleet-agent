"""Fixed, opt-in maintenance with durable single-use previews and guarded recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remote_fleet_agent.ssh_client import run_powershell

ACTIONS = {
    "start_print_spooler": {
        "service": "Spooler",
        "effect": "Start Print Spooler if stopped; queued printing may resume.",
        "rollback": "Stop it only if this plan started it and it is still running. This may interrupt printing.",
    },
    "start_time_service": {
        "service": "W32Time",
        "effect": "Start Windows Time if stopped; configured time synchronization may run.",
        "rollback": "Stop it only if this plan started it and it is still running. Clock changes are not undone.",
    },
    "clear_dns_cache": {
        "service": None,
        "effect": "Clear the local DNS resolver cache; subsequent lookups repopulate it.",
        "rollback": "Unavailable: discarded cache entries cannot be restored.",
    },
}


def now():
    return datetime.now(UTC)


def probe_script(action):
    service = ACTIONS[action]["service"]
    if service:
        body = (
            f"$s=Get-Service -Name '{service}' -ErrorAction Stop; "
            "[pscustomobject]@{status=$s.Status.ToString();start_type=$s.StartType.ToString()}"
        )
    else:
        body = (
            "Get-Command Clear-DnsClientCache -ErrorAction Stop | Out-Null; "
            "[pscustomobject]@{available=$true}"
        )
    return "$ErrorActionPreference='Stop'; " + body + " | ConvertTo-Json -Compress"


def change_script(action, before, rollback=False):
    service = ACTIONS[action]["service"]
    if service:
        expected = "Running" if rollback else before["status"]
        verb, goal = ("Stop", "Stopped") if rollback else ("Start", "Running")
        start_type = before["start_type"]
        # All inserted values come from fixed action definitions or validated enums.
        return (
            "$ErrorActionPreference='Stop'; "
            f"$s=Get-Service -Name '{service}' -ErrorAction Stop; "
            f"if ($s.Status.ToString() -ne '{expected}' -or $s.StartType.ToString() -ne '{start_type}') "
            "{ throw 'State changed since preview; no change attempted' }; "
            "$changed=$false; "
            f"if ($s.Status.ToString() -ne '{goal}') {{ "
            f"{verb}-Service -Name '{service}' -ErrorAction Stop; $changed=$true; "
            f"$s.WaitForStatus([System.ServiceProcess.ServiceControllerStatus]::{goal},"
            "[TimeSpan]::FromSeconds(15)) }; "
            f"$s=Get-Service -Name '{service}'; "
            "[pscustomobject]@{changed=$changed;status=$s.Status.ToString();"
            "start_type=$s.StartType.ToString()} | ConvertTo-Json -Compress"
        )
    if rollback:
        raise ValueError("DNS cache clearing has no rollback")
    return (
        "$ErrorActionPreference='Stop'; Clear-DnsClientCache -ErrorAction Stop; "
        "$entries=@(Get-DnsClientCache -ErrorAction Stop); "
        "[pscustomobject]@{changed=$true;cache_entries_after=$entries.Count;"
        "verification='Cmdlet completed; cache may already be repopulated'} | ConvertTo-Json -Compress"
    )


class MaintenanceManager:
    def __init__(self, config, connection, folder: Path):
        self.config = config
        self.connection = connection
        self.folder = folder / config.client / "maintenance"
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _allowed(self, host, action):
        if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
            raise ValueError("maintenance is disabled in audit-only mode")
        if action not in ACTIONS or action not in self.config.maintenance_actions:
            raise ValueError("maintenance action is not enabled for this engagement")
        approved = self.config.resolve(host)
        if self.config.simulation or approved.platform != "windows":
            raise ValueError("maintenance currently supports real Windows hosts only")
        return approved

    def _path(self, plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise ValueError("invalid maintenance plan ID")
        return self.folder / (plan_id + ".json")

    def _save(self, plan):
        fd, name = tempfile.mkstemp(dir=self.folder)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(plan, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self._path(plan["id"]))
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def get(self, plan_id):
        plan = json.loads(self._path(plan_id).read_text())
        if plan["client"] != self.config.client:
            raise ValueError("plan belongs to another engagement")
        return plan

    async def _run(self, host, script):
        conn = await self.connection(host)
        result = await asyncio.wait_for(run_powershell(conn, script), timeout=30)
        if result.exit_code:
            raise RuntimeError(result.stderr[:1000] or "maintenance query failed")
        data = json.loads(result.stdout.lstrip("\ufeff"))
        if not isinstance(data, dict):
            raise TypeError("unexpected maintenance result")
        return data

    async def preview(self, host, action):
        approved = self._allowed(host, action)
        state = await self._run(approved.name, probe_script(action))
        if ACTIONS[action]["service"]:
            if state.get("status") not in ("Running", "Stopped") or state.get(
                "start_type"
            ) not in ("Automatic", "Manual"):
                raise ValueError(
                    "service must be stable and not disabled; no changes made"
                )
        elif state.get("available") is not True:
            raise ValueError("DNS maintenance cmdlet unavailable")
        plan = {
            "id": uuid.uuid4().hex,
            "client": self.config.client,
            "host": approved.name,
            "target": approved.target,
            "port": approved.port,
            "platform": approved.platform,
            "host_keys_digest": hashlib.sha256(
                self.config.known_hosts.read_bytes()
            ).hexdigest(),
            "action": action,
            "before": state,
            "effect": ACTIONS[action]["effect"],
            "recovery": ACTIONS[action]["rollback"],
            "created_at": now().isoformat(),
            "expires_at": (now() + timedelta(minutes=10)).isoformat(),
            "status": "preview",
        }
        plan["digest"] = hashlib.sha256(
            json.dumps(plan, sort_keys=True).encode()
        ).hexdigest()
        self._save(plan)
        return plan

    async def execute(self, plan_id, expected_digest, *, rollback=False):
        path = self._path(plan_id)
        lock = path.with_suffix(".lock")
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError(
                "plan is locked; inspect its saved state before recovery"
            ) from None
        os.close(fd)
        host_lock = None
        global_claim = None
        known_completion = True
        try:
            plan = self.get(plan_id)
            approved = self._allowed(plan["host"], plan["action"])
            if (
                approved.target != plan["target"]
                or approved.port != plan.get("port", 22)
                or approved.platform != plan.get("platform", "windows")
            ):
                raise ValueError("host target changed since preview")
            if (
                hashlib.sha256(self.config.known_hosts.read_bytes()).hexdigest()
                != plan["host_keys_digest"]
            ):
                raise ValueError("verified host-key file changed since preview")
            candidate = self.folder / (
                "host-"
                + hashlib.sha256(approved.target.casefold().encode()).hexdigest()
                + ".lock"
            )
            try:
                host_fd = os.open(
                    candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            except FileExistsError:
                raise ValueError(
                    "host has maintenance in progress or a stale lock; inspect before recovery"
                ) from None
            os.close(host_fd)
            host_lock = candidate
            if expected_digest != plan["digest"]:
                raise ValueError("preview digest does not match")
            original = {
                k: v
                for k, v in plan.items()
                if k
                not in {
                    "digest",
                    "result",
                    "started_at",
                    "finished_at",
                    "error",
                    "rollback_result",
                    "attempts",
                }
            }
            original["status"] = "preview"
            if (
                hashlib.sha256(
                    json.dumps(original, sort_keys=True).encode()
                ).hexdigest()
                != expected_digest
            ):
                raise ValueError("saved preview integrity check failed")
            if rollback:
                if (
                    not ACTIONS[plan["action"]]["service"]
                    or plan["status"] != "complete"
                    or plan["before"]["status"] != "Stopped"
                    or plan["result"].get("changed") is not True
                ):
                    raise ValueError("this plan has no eligible service rollback")
            elif plan["status"] != "preview" or now() >= datetime.fromisoformat(
                plan["expires_at"]
            ):
                raise ValueError(
                    "plan was already used or expired; inspect results and create a new preview"
                )
            from remote_fleet_agent.administration import EndpointClaim, legacy_target

            global_claim = EndpointClaim(legacy_target(self.config, approved), plan_id)
            script = change_script(plan["action"], plan["before"], rollback)
            plan["status"] = "rolling_back" if rollback else "executing"
            plan["started_at"] = now().isoformat()
            attempt = {
                "operation": "rollback" if rollback else "execute",
                "started_at": plan["started_at"],
                "status": plan["status"],
            }
            plan.setdefault("attempts", []).append(attempt)
            # A durable intent record is mandatory before any remote mutation.
            self._save(plan)
            try:
                known_completion = False
                result = await self._run(plan["host"], script)
                service = ACTIONS[plan["action"]]["service"]
                if service:
                    goal = "Stopped" if rollback else "Running"
                    if (
                        result.get("status") != goal
                        or result.get("start_type") != plan["before"]["start_type"]
                        or type(result.get("changed")) is not bool
                    ):
                        raise ValueError("post-change verification failed")
                elif (
                    result.get("changed") is not True
                    or type(result.get("cache_entries_after")) is not int
                    or result["cache_entries_after"] < 0
                ):
                    raise ValueError("DNS verification unavailable")
                plan["rollback_result" if rollback else "result"] = result
                plan["status"] = "rolled_back" if rollback else "complete"
            except BaseException as exc:
                # A transport error/cancellation cannot prove whether a command ran.
                # Never automatically retry or roll back an uncertain remote change.
                plan["status"] = "rollback_unknown" if rollback else "unknown"
                plan["error"] = str(exc)[:1000] or type(exc).__name__
                plan["finished_at"] = now().isoformat()
                attempt.update(
                    status=plan["status"],
                    error=plan["error"],
                    finished_at=plan["finished_at"],
                )
                self._save(plan)
                if isinstance(
                    exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)
                ):
                    raise
                return plan
            known_completion = True
            plan["finished_at"] = now().isoformat()
            attempt.update(
                status=plan["status"], result=result, finished_at=plan["finished_at"]
            )
            self._save(plan)
            return plan
        finally:
            if global_claim is not None:
                if known_completion:
                    global_claim.release()
                else:
                    global_claim.mark_unknown()
            if host_lock is not None:
                host_lock.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
