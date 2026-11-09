"""Engineer-approved support cases. AI investigation and execution never grant approval."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.ssh_client import build_encoded_command
from remote_fleet_agent.storage import digest, locked, stamp

WINDOWS_PROFILES = {
    "outlook": {
        "processes": "Get-Process -Name OUTLOOK,olk -ErrorAction SilentlyContinue | Select-Object -First 50 Id,ProcessName,SessionId,Responding,CPU",
        "office": "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Office\\ClickToRun\\Configuration' -ErrorAction Stop | Select-Object VersionToReport,ProductReleaseIds,Platform,UpdateChannel",
        "application_events": "Get-WinEvent -FilterHashtable @{LogName='Application';Level=1,2;StartTime=(Get-Date).AddHours(-24)} -MaxEvents 30 -ErrorAction Stop | Select-Object TimeCreated,Id,ProviderName,LevelDisplayName",
        "session_context": "[pscustomobject]@{sshAccount=[System.Security.Principal.WindowsIdentity]::GetCurrent().Name;interactiveAccount=(Get-CimInstance Win32_ComputerSystem).UserName;note='SSH is a separate session; interactive Outlook launch and add-in tests require user-session confirmation'}",
    },
    "printing": {
        "spooler": "Get-Service -Name Spooler -ErrorAction Stop | Select-Object Name,Status,StartType,DependentServices",
        "printers": "Get-Printer -ErrorAction Stop | Select-Object -First 100 Name,DriverName,PortName,PrinterStatus,Shared",
        "drivers": "Get-PrinterDriver -ErrorAction Stop | Select-Object -First 100 Name,Manufacturer,MajorVersion,InfPath",
        "ports": "Get-PrinterPort -ErrorAction Stop | Select-Object -First 100 Name,PrinterHostAddress,PortNumber,Protocol",
        "jobs": "Get-Printer -ErrorAction Stop | Select-Object -First 100 | ForEach-Object {Get-PrintJob -PrinterName $_.Name -ErrorAction Stop | Select-Object -First 30 ID,PrinterName,JobStatus,Size}",
    },
    "general": {
        "os": "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,LastBootUpTime,FreePhysicalMemory",
        "processes": "Get-Process | Sort-Object CPU -Descending | Select-Object -First 50 Id,ProcessName,SessionId,CPU,WorkingSet64",
        "services": "Get-Service | Select-Object -First 100 Name,Status,StartType",
    },
}


def required(value, name, maximum=4000):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(f"{name} must be nonempty text, at most {maximum} characters")
    return value.strip()


async def run_bounded(conn, platform, script, timeout=60, limit=65536):
    """One overall deadline; bound retained output while draining both streams."""
    command = build_encoded_command(script) if platform == "windows" else "sh -s"
    process = None

    async def read(stream):
        chunks, size, truncated = [], 0, False
        while chunk := await stream.read(4096):
            remaining = max(0, limit - size)
            if remaining:
                chunks.append(chunk[:remaining])
            size += min(len(chunk), remaining)
            truncated |= len(chunk) > remaining
        return "".join(chunks), truncated

    try:
        async with asyncio.timeout(timeout):
            process = await conn.create_process(command)
            if platform != "windows":
                process.stdin.write(script + "\n")
            process.stdin.write_eof()
            stdout, stderr = await asyncio.gather(
                read(process.stdout), read(process.stderr)
            )
            await process.wait_closed()
            return {
                "stdout": stdout[0],
                "stderr": stderr[0],
                "truncated": stdout[1] or stderr[1],
                "exit_code": process.exit_status,
            }
    finally:
        if process is not None:
            process.close()


def simulated_evidence(profile):
    sections = {}
    if profile == "outlook":
        sections = {
            "processes": [
                {
                    "Id": 4242,
                    "ProcessName": "OUTLOOK",
                    "SessionId": 1,
                    "Responding": False,
                }
            ],
            "office": [
                {
                    "VersionToReport": "16.0.demo",
                    "ProductReleaseIds": "Fictional Office installation",
                }
            ],
            "session_context": [
                {
                    "note": "Fictional interactive user session 1; SSH is a separate session"
                }
            ],
        }
    elif profile == "printing":
        sections = {
            "spooler": [
                {"Name": "Spooler", "Status": "Running", "StartType": "Automatic"}
            ],
            "printers": [
                {
                    "Name": "Warehouse Printer",
                    "DriverName": "Contoso Universal PCL6",
                    "PortName": "Demo TCP port",
                    "PrinterStatus": "Error",
                }
            ],
            "drivers": [
                {
                    "Name": "Contoso Universal PCL6",
                    "Manufacturer": "Fictional vendor",
                    "InfPath": "C:\\DemoDrivers\\printer.inf",
                }
            ],
            "jobs": [
                {"ID": 7, "PrinterName": "Warehouse Printer", "JobStatus": "Error"}
            ],
        }
    return {
        "simulation": True,
        "profile": profile,
        "collected_at": stamp(),
        "observations": [
            "Fictional Outlook process is unresponsive"
            if profile == "outlook"
            else "Fictional printer queue/driver needs review; driver failure is not established"
            if profile == "printing"
            else "Fictional general diagnostic snapshot"
        ],
        "limitations": [
            "Synthetic evidence only. No SSH, interactive session or physical printer was accessed."
        ],
        "sections": {
            name: {"status": "collected", "items": items}
            for name, items in sections.items()
        },
    }


class SupportWorkflow:
    def __init__(self, service):
        self.service = service
        self.tasks = set()
        self.failures = []

    def actor(self, actor, permission="collect"):
        current = self.service.store.operator(actor["id"])
        self.service.store.require(current, permission)
        return current

    def get(self, actor, client, ident):
        actor = self.actor(actor, "read")
        case = self.service.store.get("support", ident, client)
        self.service.selected(actor, client, hosts=[case["host"]])
        self.service.require_site(actor, client, case["site"])
        return case

    def list(self, actor, client, site=""):
        permitted = {
            h.name
            for h in self.service.selected(self.actor(actor, "read"), client, site)
        }
        fields = (
            "id",
            "host",
            "platform",
            "profile",
            "state",
            "revision",
            "updated_at",
            "site",
        )
        records = [
            c
            for c in self.service.store.list("support", client)
            if c["host"] in permitted
            and self.service.permitted(self.actor(actor, "read"), client, c["site"])
        ]
        return [
            {**{key: c[key] for key in fields}, "symptom": c["symptom"][:240]}
            for c in records[:100]
        ]

    @staticmethod
    def present(case):
        """Keep current case responses bounded; full prior repairs are fetched individually."""
        view = dict(case)
        history = case.get("repair_history", [])
        view["repair_history_count"] = len(history)
        view["repair_history"] = [
            {
                "index": index,
                "state": item["state"],
                "archived_at": item["archived_at"],
                "title": item["repair"]["payload"]["title"][:160],
                "digest": item["repair"]["digest"],
            }
            for index, item in enumerate(history)
            if index >= len(history) - 20
        ]
        view["investigation_history_count"] = len(case.get("investigation_history", []))
        view.pop("investigation_history", None)
        return view

    def save(self, actor, client, before, values):
        """Revision CAS is atomic across controller processes, including claim-before-send."""
        data = {
            k: v
            for k, v in before.items()
            if k not in ("id", "revision", "site", "owner_id")
        }
        data.update(values, updated_at=stamp())
        db = self.service.store.db
        with db:
            changed = db.execute(
                "UPDATE records SET revision=revision+1,data=? WHERE kind='support' AND id=? AND client=? AND revision=?",
                (json.dumps(data), before["id"], client, before["revision"]),
            )
            if changed.rowcount != 1:
                raise Conflict("Support case changed; read it again before continuing.")
            self.service.store.audit(
                actor,
                "support." + data["state"],
                client,
                before["site"],
                {
                    "case": before["id"],
                    "repair_digest": data.get("repair", {}).get("digest"),
                },
            )
        return self.service.store.get("support", before["id"], client)

    def checked(self, actor, client, ident, revision):
        self.actor(actor)
        case = self.get(actor, client, ident)
        if type(revision) is not int or revision != case["revision"]:
            raise Conflict("Support case changed; refresh its revision.")
        if case["state"] in ("executing", "investigating"):
            raise Conflict(
                "Support work is in progress; inspect the saved outcome first."
            )
        return case

    def create(self, actor, client, body):
        actor = self.actor(actor)
        host = self.service.selected(actor, client, hosts=[body.get("host")])[0]
        profile = body.get("profile", "general")
        if profile not in WINDOWS_PROFILES or (
            host.platform != "windows" and profile != "general"
        ):
            raise ValueError(
                "Outlook/printing investigations require Windows; use general on macOS/Linux"
            )
        return self.service.store.save(
            "support",
            client,
            host.site,
            actor,
            {
                "host": host.name,
                "platform": host.platform,
                "profile": profile,
                "symptom": required(body.get("symptom"), "symptom"),
                "state": "open",
                "evidence": None,
                "investigation_history": [],
                "repair_history": [],
                "created_at": stamp(),
                "updated_at": stamp(),
            },
        )

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def finished(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                self.failures.append(type(task.exception()).__name__)

        task.add_done_callback(finished)

    @staticmethod
    def archived(case):
        return case["repair_history"] + (
            [
                {
                    "repair": case["repair"],
                    "result": case.get("result"),
                    "state": case["state"],
                    "resolution": case.get("resolution"),
                    "error": case.get("error"),
                    "started_at": case.get("started_at"),
                    "finished_at": case.get("finished_at"),
                    "archived_at": stamp(),
                }
            ]
            if case.get("repair")
            else []
        )

    def investigate(self, actor, client, ident, body):
        case = self.checked(actor, client, ident, body.get("revision"))
        self.service.endpoint_permission(actor, client, [case["host"]])
        history = self.archived(case)
        investigations = case.get("investigation_history", []) + (
            [case["evidence"]] if case.get("evidence") else []
        )
        case = self.save(
            actor,
            client,
            case,
            {
                "state": "investigating",
                "repair": {},
                "repair_history": history,
                "evidence": None,
                "investigation_history": investigations,
                "error": None,
                "result": None,
                "resolution": None,
            },
        )
        self.spawn(self.investigation(actor, client, case))
        return case

    async def investigation(self, actor, client, case):
        try:
            config = self.service.endpoint_permission(actor, client, [case["host"]])
            host = config.resolve(case["host"])
            if config.simulation:
                if host.scenario == "offline":
                    raise ConnectionError("Simulated device is offline")
                evidence = simulated_evidence(case["profile"])
            else:
                runtime = await self.service.runtime(client)
                self.service.endpoint_permission(actor, client, [host.name])
                conn = await runtime.connection(host.name)
                if host.platform == "windows":
                    from remote_fleet_agent.tools.collect import collect

                    evidence = await collect(
                        conn,
                        "support",
                        WINDOWS_PROFILES[case["profile"]],
                        timeout=config.query_timeout,
                        limit=100,
                    )
                else:
                    evidence = await run_bounded(
                        conn,
                        host.platform,
                        "uname -a; id; df -P; ps -axo pid,comm | head -60",
                        config.query_timeout,
                    )
                evidence.update(
                    simulation=False,
                    collected_at=stamp(),
                    note="Partial or empty evidence does not establish the cause. SSH runs in its own account/session.",
                )
            self.save(
                actor, client, case, {"state": "investigated", "evidence": evidence}
            )
        except BaseException as exc:
            self.save(
                actor,
                client,
                case,
                {
                    "state": "investigation_failed",
                    "error": str(exc)[:1000] or type(exc).__name__,
                },
            )
            if isinstance(exc, asyncio.CancelledError):
                raise

    def propose(self, actor, client, ident, body):
        case = self.checked(actor, client, ident, body.get("revision"))
        if case["state"] not in (
            "investigated",
            "awaiting_approval",
            "approved",
            "rejected",
            "resolved",
            "issue_persists",
            "checks_passed",
            "verification_failed",
            "execution_failed",
        ) or not case.get("evidence"):
            raise ValueError(
                "Investigate the current device before proposing a repair; uncertain changes require fresh investigation"
            )
        evidence_deadline = datetime.fromisoformat(
            case["evidence"]["collected_at"]
        ) + timedelta(minutes=30)
        if evidence_deadline <= datetime.now(UTC):
            raise Conflict(
                "Investigation evidence is stale. Investigate again before proposing a repair."
            )
        config = self.service.endpoint_permission(actor, client, [case["host"]])
        if not config.simulation and not config.administration_enabled:
            raise Denied(
                "Real administration is disabled in this engagement configuration."
            )
        if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
            raise Denied("Audit-only mode blocks repair proposals and execution.")
        timeout = body.get("timeout_seconds", 60)
        if type(timeout) is not int or not 5 <= timeout <= 300:
            raise ValueError("timeout_seconds must be between 5 and 300")
        payload = {
            k: required(
                body.get(k),
                k,
                32768 if k in ("script", "verification_script") else 4000,
            )
            for k in (
                "title",
                "rationale",
                "impact",
                "script",
                "verification_script",
                "recovery",
            )
        }
        if case["platform"] == "windows":
            for key in ("script", "verification_script"):
                payload[key] = "$ErrorActionPreference='Stop'\n" + payload[key]
        endpoint = config.resolve(case["host"])
        payload.update(
            client=client,
            target=endpoint.target,
            port=endpoint.port,
            host=case["host"],
            platform=case["platform"],
            signature=self.service.fingerprint(client),
            evidence_digest=digest(case["evidence"]),
            timeout_seconds=timeout,
            simulation=config.simulation,
            expires_at=min(
                datetime.now(UTC) + timedelta(minutes=30), evidence_deadline
            ).isoformat(),
            nonce=uuid.uuid4().hex,
        )
        repair = {"payload": payload, "digest": digest(payload), "approval": None}
        history = self.archived(case)
        return self.save(
            actor,
            client,
            case,
            {
                "state": "awaiting_approval",
                "repair": repair,
                "repair_history": history,
                "result": None,
                "error": None,
                "resolution": None,
            },
        )

    def validate_plan(self, actor, client, case, expected_digest):
        repair = case.get("repair") or {}
        payload = repair.get("payload", {})
        if (
            not expected_digest
            or expected_digest != repair.get("digest")
            or digest(payload) != expected_digest
        ):
            raise Conflict("Repair digest mismatch. Review a fresh repair plan.")
        if datetime.fromisoformat(payload["expires_at"]) <= datetime.now(UTC):
            raise Conflict(
                "Repair approval window expired. Propose and review a fresh plan."
            )
        config = self.service.endpoint_permission(
            actor, client, [case["host"]], payload["signature"]
        )
        if (
            payload["host"] != case["host"]
            or payload["platform"] != case["platform"]
            or payload["evidence_digest"] != digest(case["evidence"])
        ):
            raise Conflict("Repair scope or evidence changed.")
        if (
            not config.simulation and not config.administration_enabled
        ) or os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
            raise Denied("Administration is disabled or audit-only mode is active.")
        return config

    def decide(self, actor, client, ident, body, approve):
        actor = self.actor(actor, "approve_repairs")
        case = self.checked(actor, client, ident, body.get("revision"))
        if case["state"] not in ("awaiting_approval", "approved"):
            raise Conflict("This repair is no longer awaiting an engineer decision.")
        self.validate_plan(actor, client, case, body.get("digest"))
        repair = dict(case["repair"])
        repair["approval"] = {
            "actor": actor["id"],
            "name": actor["name"],
            "digest": repair["digest"],
            "approved": approve,
            "at": stamp(),
        }
        return self.save(
            actor,
            client,
            case,
            {"state": "approved" if approve else "rejected", "repair": repair},
        )

    def execute(self, actor, client, ident, body):
        case = self.checked(actor, client, ident, body.get("revision"))
        if case["state"] != "approved":
            raise Denied(
                "An engineer must approve this exact repair in the workspace before execution."
            )
        self.validate_plan(actor, client, case, body.get("digest"))
        approval = case["repair"].get("approval") or {}
        if (
            approval.get("approved") is not True
            or approval.get("digest") != case["repair"]["digest"]
        ):
            raise Denied("No valid engineer approval.")
        approver = self.actor({"id": approval["actor"]}, "approve_repairs")
        self.service.selected(approver, client, hosts=[case["host"]])
        case = self.save(
            actor, client, case, {"state": "executing", "started_at": stamp()}
        )
        self.spawn(self.execution(actor, client, case))
        return case

    async def execution(self, actor, client, case):
        result = {}
        global_claim = None
        known_completion = True
        try:
            config = self.validate_plan(actor, client, case, case["repair"]["digest"])
            approver = self.actor(
                {"id": case["repair"]["approval"]["actor"]}, "approve_repairs"
            )
            self.service.selected(approver, client, hosts=[case["host"]])
            runtime = await self.service.runtime(client)
            host = config.resolve(case["host"])
            lock = runtime.maintenance.folder / (
                "host-"
                + hashlib.sha256(host.target.casefold().encode()).hexdigest()
                + ".lock"
            )
            with locked(lock):
                from remote_fleet_agent.administration import (
                    EndpointClaim,
                    legacy_target,
                )

                global_claim = EndpointClaim(
                    legacy_target(config, host),
                    case["id"],
                    self.service.store.folder / "endpoint-locks",
                )
                payload = case["repair"]["payload"]
                if config.simulation:
                    # Never interpret arbitrary submitted code as a fixture result.
                    result = {
                        "simulation": True,
                        "execution": {
                            "exit_code": 0,
                            "stdout": "Simulated repair completed; script was NOT run",
                            "stderr": "",
                            "truncated": False,
                        },
                        "verification": {
                            "exit_code": 0,
                            "stdout": '{"passed":true,"detail":"Simulated check only"}',
                            "stderr": "",
                            "truncated": False,
                        },
                    }
                else:
                    self.validate_plan(actor, client, case, case["repair"]["digest"])
                    conn = await runtime.connection(case["host"])
                    self.validate_plan(actor, client, case, case["repair"]["digest"])
                    current_approver = self.actor(
                        {"id": case["repair"]["approval"]["actor"]}, "approve_repairs"
                    )
                    self.service.selected(
                        current_approver, client, hosts=[case["host"]]
                    )
                    result["simulation"] = False
                    known_completion = False
                    result["execution"] = await run_bounded(
                        conn,
                        case["platform"],
                        payload["script"],
                        payload["timeout_seconds"],
                    )
                    if result["execution"]["exit_code"] == 0:
                        result["verification"] = await run_bounded(
                            conn,
                            case["platform"],
                            payload["verification_script"],
                            payload["timeout_seconds"],
                        )
                known_completion = True
                state = "execution_failed"
                if result["execution"]["exit_code"] == 0:
                    state = "verification_failed"
                    verify = result.get("verification", {})
                    try:
                        passed = (
                            json.loads(verify.get("stdout", "").lstrip("\ufeff")).get(
                                "passed"
                            )
                            is True
                        )
                    except (ValueError, AttributeError):
                        passed = False
                    if (
                        verify.get("exit_code") == 0
                        and not verify.get("truncated")
                        and passed
                    ):
                        state = "checks_passed"
                result["note"] = (
                    "Check success does not establish that Outlook opens in the user's session or a physical page prints. Engineer confirmation is still required."
                )
                self.save(
                    actor,
                    client,
                    case,
                    {"state": state, "result": result, "finished_at": stamp()},
                )
        except BaseException as exc:
            self.save(
                actor,
                client,
                case,
                {
                    "state": "unknown",
                    "result": result,
                    "error": str(exc)[:1000] or type(exc).__name__,
                    "finished_at": stamp(),
                },
            )
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            if global_claim is not None:
                if known_completion:
                    global_claim.release()
                else:
                    global_claim.mark_unknown()

    def confirm(self, actor, client, ident, body):
        case = self.checked(actor, client, ident, body.get("revision"))
        if (
            case["state"]
            not in ("checks_passed", "verification_failed", "execution_failed")
            or type(body.get("resolved")) is not bool
        ):
            raise Conflict(
                "Inspect the completed repair before recording its resolution."
            )
        return self.save(
            actor,
            client,
            case,
            {
                "state": "resolved" if body["resolved"] else "issue_persists",
                "resolution": {
                    "actor": actor["id"],
                    "at": stamp(),
                    "note": required(body.get("note"), "confirmation note"),
                    "operator_recorded": True,
                },
            },
        )

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
