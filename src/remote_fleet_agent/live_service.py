"""Authorized local application workflows. No implicit endpoint discovery or installation."""

from __future__ import annotations

import contextvars
import copy
import hashlib
import html
import json
import shlex
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.jobs import ACTIVE, RETRYABLE
from remote_fleet_agent.live_store import Conflict, Denied, LiveStore
from remote_fleet_agent.reporting import render_report, shareable_snapshot
from remote_fleet_agent.runtime import OPERATIONS, FleetRuntime
from remote_fleet_agent.storage import digest, read_json, stamp
from remote_fleet_agent.support import SupportWorkflow
from remote_fleet_agent.tools.collect import select_queries
from remote_fleet_agent.workspace import EvidenceWorkspace

AUTHORITY = contextvars.ContextVar("live_collection_authority", default=None)


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Use an ISO date/time with a timezone.")
    return parsed.astimezone(UTC)


def text(value, name, maximum=1000, required=False):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (required and not value.strip())
    ):
        raise ValueError("Invalid " + name)
    return value.strip()


class LiveService:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.store = LiveStore(self.directory)
        self.runtimes = {}
        self.sessions = {}
        self.plans = {}
        self.schedule_busy = set()
        self.registry = self.load_registry()
        self.support = SupportWorkflow(self)
        from remote_fleet_agent.investigation import InvestigationWorkflow
        from remote_fleet_agent.repair_execution import RepairWorkflow

        self.investigations = InvestigationWorkflow(self)
        self.repairs = RepairWorkflow(self)

    def load_registry(self):
        value = read_json(self.directory / "workspace.json")
        if value.get("schema") != 1 or not isinstance(value.get("clients"), list):
            raise ValueError("Invalid workspace catalog.")
        result = {}
        for item in value["clients"]:
            c = FleetConfig.load(item["config"], require_known_hosts=False)
            if c.client in result:
                raise ValueError("Client IDs must be unique in this workspace.")
            for flag in ("allow_collection", "allow_schedules"):
                if type(item.get(flag, False)) is not bool:
                    raise ValueError("Invalid client access policy.")
            if item.get("access_expires_at"):
                utc(item["access_expires_at"])
            if (
                not c.simulation
                and item.get("allow_collection")
                and not item.get("authorisation_reference")
            ):
                raise ValueError("Real collections require an authorisation reference.")
            result[c.client] = {
                **item,
                "client": c.client,
                "name": item.get("name", c.client),
            }
        return result

    def descriptor(self, client):
        current = self.load_registry()
        if client not in current:
            raise FileNotFoundError("Client not found.")
        return current[client]

    def config(self, client):
        return FleetConfig.load(
            self.descriptor(client)["config"], require_known_hosts=False
        )

    def sites(self, client):
        return sorted({h.site for h in self.config(client).hosts})

    def grants(self, actor, client):
        self.descriptor(client)
        if actor["role"] == "owner":
            return "*"
        grant = actor["scopes"].get(client)
        if grant == "*":
            return "*"
        if isinstance(grant, list) and grant:
            return set(grant)
        raise Denied("Client is outside your permitted scope.")

    def require_site(self, actor, client, site):
        grant = self.grants(actor, client)
        if grant != "*" and (not site or site not in grant):
            raise Denied("Site is outside your permitted scope.")

    def permitted(self, actor, client, site):
        grant = self.grants(actor, client)
        return grant == "*" or bool(site and site in grant)

    def selected(self, actor, client, site="", hosts=None):
        from remote_fleet_agent.operations import select_permitted

        config = self.config(client)
        grant = self.grants(actor, client)
        return select_permitted(config, grant, site, hosts)

    def operations(self, actor: dict, filters: dict) -> dict:
        from remote_fleet_agent.operations import operations

        return operations(self, actor, filters)

    def fingerprint(self, client):
        entry = self.descriptor(client)
        c = self.config(client)
        return digest(
            {
                "entry": entry,
                "config": Path(entry["config"]).read_text(),
                "known_hosts": hashlib.sha256(c.known_hosts.read_bytes()).hexdigest()
                if c.known_hosts.is_file()
                else None,
            }
        )

    def endpoint_permission(self, actor, client, hosts, signature=None):
        actor = self.store.operator(actor["id"])
        self.store.require(actor, "collect")
        entry = self.descriptor(client)
        c = self.config(client)
        self.selected(actor, client, hosts=hosts)
        if not entry.get("allow_collection"):
            raise Denied("This client is registered for saved evidence only.")
        if entry.get("access_expires_at") and utc(
            entry["access_expires_at"]
        ) <= datetime.now(UTC):
            raise Denied("Client access has expired.")
        if not c.simulation:
            FleetConfig.load(entry["config"])
            if not entry.get("key_path") or not Path(entry["key_path"]).is_file():
                raise Denied("The client private key is unavailable.")
        if signature and signature != self.fingerprint(client):
            raise Conflict(
                "Client scope or access policy changed; create a fresh preview."
            )
        return c

    async def runtime(self, client):
        signature = self.fingerprint(client)
        cached = self.runtimes.get(client)
        if cached and cached[0] != signature:
            if any(not t.done() for t in cached[1].jobs.tasks.values()):
                raise Conflict(
                    "Client configuration changed during a job. Wait for its terminal result."
                )
            await cached[1].close()
            cached = None
        if not cached:
            entry = self.descriptor(client)
            rt = FleetRuntime(
                self.config(client),
                entry.get("key_path", "simulation-no-key"),
                Path(entry["jobs"]),
            )

            async def guarded(host, operation, sections):
                authority = AUTHORITY.get()
                if not authority:
                    raise Denied("A reviewed collection scope is required.")
                actor = self.store.operator(authority["actor"])
                self.endpoint_permission(actor, client, [host], authority["signature"])
                return await rt.collect(host, operation, sections)

            rt.jobs.worker = guarded
            self.runtimes[client] = (signature, rt)
        return self.runtimes[client][1]

    def workspace(self, client):
        return EvidenceWorkspace(
            self.config(client), Path(self.descriptor(client)["jobs"])
        )

    def snapshot(self, actor, client, site=""):
        grant = self.grants(actor, client)
        if site:
            self.require_site(actor, client, site)
        ws = self.workspace(client)
        snap = ws.snapshot()
        current = {h.name: h for h in self.config(client).hosts}
        snap["hosts"] = [
            h
            for h in snap["hosts"]
            if (grant == "*" or h.get("site") in grant)
            and (not site or h.get("site") == site)
        ]
        names = {h["name"] for h in snap["hosts"]}
        for h in snap["hosts"]:
            h["freshness"] = self.freshness(h.get("last_collected_at"), client)
            h["capabilities"] = (
                {
                    op: list(FleetRuntime.catalog(current[h["name"]].platform, op))
                    for op in OPERATIONS
                }
                if h["name"] in current
                else {}
            )
        snap["jobs"] = [
            {**j, "hosts": {n: s for n, s in j["hosts"].items() if n in names}}
            for j in snap["jobs"]
            if names.intersection(j["hosts"])
        ]
        snap["manual"] = [
            r
            for r in snap["manual"]
            if self.permitted(actor, client, r.get("site", ""))
            and (not site or r.get("site") == site)
        ]
        snap["batches"] = [
            {**b, "hosts": {n: v for n, v in b["hosts"].items() if n in names}}
            for b in snap["batches"]
            if names.intersection(b["hosts"])
        ]
        for batch in snap["batches"]:
            if batch.get("canary") not in names:
                batch.pop("canary", None)
        snap["storage_errors"] = [
            {"error": "A source record could not be read."}
            for _ in snap["storage_errors"]
        ]
        snap["tasks"] = self.records(actor, "task", client, site)
        snap["views"] = self.records(actor, "view", client, site)
        snap["schedules"] = self.records(actor, "schedule", client, site)
        snap["baselines"] = self.records(actor, "baseline", client, site)
        snap["branding"] = self.branding(actor, client)
        snap["access"] = self.access_status(actor, client, site)
        snap["sites"] = [s for s in self.sites(client) if grant == "*" or s in grant]
        snap["integrity"] = self.store.integrity
        return snap

    def freshness(self, when, client):
        settings = self.store.list("settings", client)
        hours = settings[0].get("freshness_hours", 24) if settings else 24
        if not when:
            return {
                "state": "never_collected",
                "age_hours": None,
                "threshold_hours": hours,
            }
        try:
            age = (datetime.now(UTC) - utc(when)).total_seconds() / 3600
            if age < -0.0833:
                return {
                    "state": "clock_skew",
                    "age_hours": None,
                    "threshold_hours": hours,
                }
            return {
                "state": "stale" if age > hours else "fresh",
                "age_hours": round(max(0, age), 1),
                "threshold_hours": hours,
            }
        except (ValueError, TypeError):
            return {"state": "unknown", "age_hours": None, "threshold_hours": hours}

    def records(self, actor, kind, client, site=""):
        self.grants(actor, client)
        return [
            r
            for r in self.store.list(kind, client)
            if self.permitted(actor, client, r["site"])
            and (not site or r["site"] == site)
            and (kind != "view" or r.get("shared") or r["owner_id"] == actor["id"])
        ]

    def scoped_record(self, actor, kind, client, ident):
        record = self.store.get(kind, ident, client)
        self.require_site(actor, client, record["site"])
        return record

    def branding(self, actor, client):
        self.grants(actor, client)
        rows = self.store.list("settings", client)
        value = rows[0] if rows else {}
        return {
            "name": value.get("name", self.descriptor(client)["name"]),
            "prepared_by": value.get("prepared_by", "IT support"),
            "theme": value.get("theme", "violet"),
            "freshness_hours": value.get("freshness_hours", 24),
            "revision": value.get("revision", 0),
            "id": value.get("id"),
        }

    def access_status(self, actor, client, site=""):
        entry = self.descriptor(client)
        c = self.config(client)
        expired = bool(
            entry.get("access_expires_at")
            and utc(entry["access_expires_at"]) <= datetime.now(UTC)
        )
        return {
            "simulation": c.simulation,
            "collection_enabled": bool(
                entry.get("allow_collection")
                and not expired
                and self.store.allowed(actor, "collect")
                and self.store.integrity
            ),
            "schedules_enabled": bool(
                self.store.integrity
                and entry.get("allow_schedules")
                and entry.get("allow_collection")
                and not expired
                and self.store.allowed(actor, "schedules")
            ),
            "expires_at": entry.get("access_expires_at"),
            "expired": expired,
            "host_keys_present": c.known_hosts.is_file(),
            "private_key_present": bool(
                entry.get("key_path") and Path(entry["key_path"]).is_file()
            ),
            "authorisation_reference": entry.get(
                "authorisation_reference", "Not recorded"
            ),
            "platforms": sorted(
                {h.platform for h in self.selected(actor, client, site)}
            ),
            "note": "Connection state is last observed evidence, not live presence. Unix installation and access expiry are not managed by this application.",
        }

    async def bootstrap(self, actor):
        from remote_fleet_agent.operations import _grant, _sources, select_permitted

        actor = self.store.operator(actor["id"])
        self.store.require(actor, "read")
        sources, partial = _sources(self, actor, "")
        clients = []
        for client, (entry, config) in sources.items():
            hosts = select_permitted(config, _grant(actor, client))
            clients.append(
                {
                    "id": client,
                    "name": entry.get("name") or client,
                    "simulation": config.simulation,
                    "sites": sorted({h.site for h in hosts}),
                    "hosts": len(hosts),
                    "stale": None,
                }
            )
        return {
            "actor": actor,
            "capabilities": {
                "operations": True,
                "investigation_sessions": True,
                "repair_plans": True,
                "human_approval": "workspace",
                "provider_approval": "workspace_fallback",
            },
            "clients": clients,
            "partial_sources": partial,
            "integrity": self.store.integrity,
            "permissions": sorted(
                a
                for a in [
                    "read",
                    "views",
                    "collect",
                    "manual",
                    "tasks",
                    "baselines",
                    "schedules",
                    "branding",
                    "shared_views",
                    "approve_repairs",
                ]
                if self.store.allowed(actor, a)
            ),
        }

    def preview(self, actor, client, data):
        hosts = data.get("hosts")
        if not isinstance(hosts, list) or not hosts:
            raise ValueError("Select explicit devices for this collection.")
        self.plans = {
            k: v
            for k, v in self.plans.items()
            if utc(v["expires_at"]) > datetime.now(UTC)
        }
        if len(self.plans) >= 1000:
            raise Conflict("Too many pending collection previews.")
        site = data.get("site", "")
        selected = self.selected(actor, client, site, hosts)
        c = self.endpoint_permission(actor, client, [h.name for h in selected])
        op = data.get("operation")
        if op not in OPERATIONS:
            raise ValueError("Choose inventory, diagnostics or readiness.")
        sections = data.get("sections") or None
        for host in selected:
            select_queries(FleetRuntime.catalog(host.platform, op), sections)
        spec = {
            "id": uuid.uuid4().hex,
            "client": client,
            "actor": actor["id"],
            "site": site,
            "hosts": [h.name for h in selected],
            "operation": op,
            "sections": sections,
            "signature": self.fingerprint(client),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "simulation": c.simulation,
        }
        spec["digest"] = digest(spec)
        self.plans[spec["id"]] = spec
        with self.store.db:
            self.store.audit(
                actor,
                "collection.previewed",
                client,
                site,
                {"plan": spec["id"], "hosts": spec["hosts"], "operation": op},
            )
        return spec

    async def start(self, actor, client, data):
        plan = self.plans.get(data.get("plan_id"))
        if not plan or plan["client"] != client or plan["actor"] != actor["id"]:
            raise Denied("Collection preview is not available to this operator/client.")
        if plan["digest"] != data.get("digest") or utc(
            plan["expires_at"]
        ) <= datetime.now(UTC):
            raise Conflict("Collection preview expired or changed.")
        self.endpoint_permission(actor, client, plan["hosts"], plan["signature"])
        rt = await self.runtime(client)
        with self.store.db:
            self.store.audit(
                actor,
                "collection.started",
                client,
                plan["site"],
                {
                    "plan": plan["id"],
                    "hosts": plan["hosts"],
                    "operation": plan["operation"],
                },
            )
        del self.plans[plan["id"]]
        authority = AUTHORITY.set(
            {"actor": actor["id"], "signature": plan["signature"]}
        )
        try:
            return rt.start(
                plan["operation"], hosts=plan["hosts"], sections=plan["sections"]
            )
        finally:
            AUTHORITY.reset(authority)

    async def job(self, actor, client, ident):
        self.grants(actor, client)
        rt = await self.runtime(client)
        job = copy.deepcopy(rt.jobs.get(ident))
        c = self.config(client)
        grant = self.grants(actor, client)
        if grant != "*":
            allowed = {h.name for h in c.hosts if h.site in grant}
            job["hosts"] = {n: v for n, v in job["hosts"].items() if n in allowed}
            if not job["hosts"]:
                raise Denied("Job has no devices in your permitted sites.")
        job.pop("owner_pid", None)
        return job

    async def retry(self, actor, client, ident):
        self.store.require(actor, "collect")
        rt = await self.runtime(client)
        job = await self.job(actor, client, ident)
        if job["status"] in ACTIVE:
            raise Conflict("The original job is still active.")
        hosts = [n for n, v in job["hosts"].items() if v["status"] in RETRYABLE]
        if not hosts:
            raise ValueError("There are no unsuccessful devices to retry.")
        c = self.endpoint_permission(actor, client, hosts)
        for name in hosts:
            select_queries(
                FleetRuntime.catalog(c.resolve(name).platform, job["operation"]),
                job.get("sections"),
            )
        signature = self.fingerprint(client)
        with self.store.db:
            self.store.audit(
                actor,
                "collection.retried",
                client,
                data={"parent": ident, "hosts": hosts},
            )
        authority = AUTHORITY.set({"actor": actor["id"], "signature": signature})
        try:
            return rt.jobs.start(
                hosts, job["operation"], job.get("sections"), parent_id=ident
            )
        finally:
            AUTHORITY.reset(authority)

    async def cancel(self, actor, client, ident):
        self.store.require(actor, "collect")
        rt = await self.runtime(client)
        visible = await self.job(actor, client, ident)
        full = rt.jobs.get(ident)
        if set(visible["hosts"]) != set(full["hosts"]):
            raise Denied(
                "A job spanning other sites cannot be cancelled from this scope."
            )
        with self.store.db:
            self.store.audit(actor, "collection.cancelled", client, data={"job": ident})
        return await rt.jobs.cancel(ident)

    def save_task(self, actor, client, data):
        self.store.require(actor, "tasks")
        site = data.get("site", "")
        self.require_site(actor, client, site)
        old = (
            self.scoped_record(actor, "task", client, data["id"])
            if data.get("id")
            else None
        )
        host = text(data.get("host", ""), "host", 64)
        if host:
            self.selected(actor, client, site, [host])
        status = data.get("status", "open")
        severity = data.get("priority", "normal")
        if status not in (
            "open",
            "in_progress",
            "resolved",
            "verified",
            "dismissed",
        ) or severity not in ("low", "normal", "high", "urgent"):
            raise ValueError("Unsupported task state or priority.")
        due = data.get("due", "")
        if due:
            datetime.strptime(due, "%Y-%m-%d")
        verification = data.get("verification_job", "")
        if status == "verified" and not verification:
            raise ValueError("Verification requires a saved collection reference.")
        if verification:
            job = self.workspace(client).jobs()[0]
            found = next((j for j in job if j["id"] == verification), None)
            if not found or found["status"] in ACTIVE:
                raise ValueError(
                    "Verification job must be a saved terminal collection in this client."
                )
            candidates = [
                n
                for n, v in found["hosts"].items()
                if v.get("status") == "complete" and (not host or n == host)
            ]
            if not candidates or not set(candidates).intersection(
                h.name for h in self.selected(actor, client, site)
            ):
                raise Denied("Verification evidence does not cover this task scope.")
        result = {
            k: text(data.get(k, ""), k, m, k == "title")
            for k, m in [
                ("title", 200),
                ("owner", 160),
                ("notes", 4000),
                ("evidence", 1000),
            ]
        }
        result.update(
            host=host,
            status=status,
            priority=severity,
            due=due,
            verification_job=verification,
            created_at=old.get("created_at") if old else stamp(),
            updated_at=stamp(),
        )
        return self.store.save(
            "task", client, site, actor, result, data.get("id"), data.get("revision")
        )

    def save_view(self, actor, client, data):
        self.store.require(actor, "views")
        site = data.get("site", "")
        self.require_site(actor, client, site)
        if type(data.get("shared", False)) is not bool:
            raise ValueError("Shared must be a boolean.")
        if data.get("page", "Fleet") not in (
            "Fleet",
            "Evidence",
            "Jobs",
            "Follow-ups",
            "Compare",
        ):
            raise ValueError("Unsupported view page.")
        if data.get("shared"):
            self.store.require(actor, "shared_views")
        if data.get("id"):
            old = self.scoped_record(actor, "view", client, data["id"])
            if old["owner_id"] != actor["id"] and actor["role"] != "owner":
                raise Denied("Only the creator can change this view.")
        filters = data.get("filters", {})
        if not isinstance(filters, dict) or set(filters) - {
            "search",
            "platform",
            "outcome",
            "freshness",
            "section",
            "mode",
            "columns",
            "column_filters",
            "sort",
            "ascending",
        }:
            raise ValueError("Unsupported saved view filters.")
        if len(json.dumps(filters)) > 16000:
            raise ValueError("Saved view is too large.")
        return self.store.save(
            "view",
            client,
            site,
            actor,
            {
                "name": text(data.get("name", ""), "name", 100, True),
                "page": data.get("page", "Fleet"),
                "filters": filters,
                "shared": bool(data.get("shared")),
            },
            data.get("id"),
            data.get("revision"),
        )

    def save_branding(self, actor, client, data):
        self.store.require(actor, "branding")
        self.require_site(actor, client, "")
        if data.get("theme") not in ("violet", "blue", "green", "slate"):
            raise ValueError("Choose a supported theme.")
        hours = data.get("freshness_hours")
        if type(hours) is not int or not 1 <= hours <= 8760:
            raise ValueError("Freshness threshold must be 1–8760 hours.")
        rows = self.store.list("settings", client)
        old = rows[0] if rows else None
        return self.store.save(
            "settings",
            client,
            "",
            actor,
            {
                "name": text(data.get("name", ""), "name", 120, True),
                "prepared_by": text(
                    data.get("prepared_by", ""), "prepared_by", 120, True
                ),
                "theme": data["theme"],
                "freshness_hours": hours,
            },
            old["id"] if old else None,
            data.get("revision"),
        )

    def save_schedule(self, actor, client, data):
        self.store.require(actor, "schedules")
        entry = self.descriptor(client)
        if not entry.get("allow_schedules"):
            raise Denied("Scheduled collections are not enabled for this client.")
        if type(data.get("enabled")) is not bool:
            raise ValueError("Schedule enabled must be true or false.")
        if not isinstance(data.get("hosts"), list) or not data["hosts"]:
            raise ValueError("Select explicit schedule devices.")
        site = data.get("site", "")
        hosts = [h.name for h in self.selected(actor, client, site, data.get("hosts"))]
        self.endpoint_permission(actor, client, hosts)
        op = data.get("operation")
        sections = data.get("sections") or None
        if op not in OPERATIONS:
            raise ValueError("Invalid operation.")
        for h in hosts:
            select_queries(
                FleetRuntime.catalog(self.config(client).resolve(h).platform, op),
                sections,
            )
        interval = data.get("interval_minutes")
        if type(interval) is not int or not 5 <= interval <= 10080:
            raise ValueError("Interval must be 5–10080 minutes.")
        if data.get("id"):
            self.scoped_record(actor, "schedule", client, data["id"])
        return self.store.save(
            "schedule",
            client,
            site,
            actor,
            {
                "name": text(data.get("name", ""), "name", 100, True),
                "hosts": hosts,
                "operation": op,
                "sections": sections,
                "interval_minutes": interval,
                "enabled": data["enabled"],
                "signature": self.fingerprint(client),
                "next_run": (
                    datetime.now(UTC) + timedelta(minutes=interval)
                ).isoformat(),
                "last_run": None,
                "last_job": None,
                "state": "scheduled" if data["enabled"] else "disabled",
            },
            data.get("id"),
            data.get("revision"),
        )

    async def tick(self, now=None):
        await self.repairs.tick()
        now = now or datetime.now(UTC)
        for client in self.load_registry():
            for schedule in self.store.list("schedule", client):
                if not schedule.get("enabled") or utc(schedule["next_run"]) > now:
                    continue
                if schedule["id"] in self.schedule_busy:
                    continue
                self.schedule_busy.add(schedule["id"])
                value = {
                    k: v
                    for k, v in schedule.items()
                    if k not in ("id", "revision", "site", "owner_id")
                }
                try:
                    actor = self.store.operator(schedule["owner_id"])
                    self.store.require(actor, "schedules")
                    entry = self.descriptor(client)
                    if not entry.get("allow_schedules"):
                        raise Denied("Scheduling was disabled for this client.")
                    self.require_site(actor, client, schedule["site"])
                    self.endpoint_permission(
                        actor, client, schedule["hosts"], schedule["signature"]
                    )
                    rt = await self.runtime(client)
                    if any(not t.done() for t in rt.jobs.tasks.values()):
                        value["state"] = "skipped_overlap"
                    else:
                        authority = AUTHORITY.set(
                            {"actor": actor["id"], "signature": schedule["signature"]}
                        )
                        try:
                            job = rt.start(
                                schedule["operation"],
                                hosts=schedule["hosts"],
                                sections=schedule["sections"],
                            )
                        finally:
                            AUTHORITY.reset(authority)
                        value.update(state="dispatched", last_job=job["id"])
                    value.update(
                        last_run=now.isoformat(),
                        next_run=(
                            now + timedelta(minutes=schedule["interval_minutes"])
                        ).isoformat(),
                    )
                except (ValueError, PermissionError, FileNotFoundError) as exc:
                    value.update(state="blocked", enabled=False, error=str(exc)[:500])
                    actor = {"id": schedule["owner_id"]}
                finally:
                    self.schedule_busy.discard(schedule["id"])
                if self.store.integrity:
                    self.store.save(
                        "schedule",
                        client,
                        schedule["site"],
                        actor,
                        value,
                        schedule["id"],
                        schedule["revision"],
                    )

    def save_manual(self, actor, client, data):
        self.store.require(actor, "manual")
        site = data.get("site", "")
        self.require_site(actor, client, site)
        ws = self.workspace(client)
        fields = data.get("fields", {})
        fields = {**fields, "reviewer": actor["name"], "site": site}
        if data.get("id"):
            old = next((r for r in ws.manual_records() if r["id"] == data["id"]), None)
            if not old:
                raise FileNotFoundError("Manual record not found.")
            self.require_site(actor, client, old.get("site", ""))
            result = ws.import_manual(
                [
                    {
                        "id": data["id"],
                        "expected_revision": data.get("revision"),
                        "fields": fields,
                    }
                ]
            )
            result = next(r for r in result if r["id"] == data["id"])
        else:
            result = ws.add_manual(**fields)
        with self.store.db:
            self.store.audit(
                actor,
                "manual.saved",
                client,
                site,
                {"record": result["id"], "revision": result["revision"]},
            )
        return result

    async def compare(self, actor, client, before, after, site=""):
        a = await self.job(actor, client, before)
        b = await self.job(actor, client, after)
        if any(j["operation"] != "inventory" or j["status"] in ACTIVE for j in (a, b)):
            raise ValueError("Select two terminal inventory jobs.")
        names = {h.name for h in self.selected(actor, client, site)} if site else None
        a = {n: v for n, v in a["hosts"].items() if names is None or n in names}
        b = {n: v for n, v in b["hosts"].items() if names is None or n in names}
        changes = []
        unknown = []
        for name in sorted(a.keys() & b.keys()):
            if not a[name].get("result", {}).get("sections") or not b[name].get(
                "result", {}
            ).get("sections"):
                unknown.append(
                    {
                        "host": name,
                        "section": "all",
                        "reason": "Collection evidence is missing or the device failed.",
                    }
                )
                continue
            old = a[name].get("result", {})
            new = b[name].get("result", {})
            if old.get("platform", "windows") != new.get("platform", "windows"):
                unknown.append(
                    {
                        "host": name,
                        "section": "all",
                        "reason": "Platform changed; evidence cannot be compared.",
                    }
                )
                continue
            for section in sorted(
                old.get("sections", {}).keys() | new.get("sections", {}).keys()
            ):
                x = old.get("sections", {}).get(section, {})
                y = new.get("sections", {}).get(section, {})
                if any(
                    v.get("status") != "collected" or v.get("truncated") for v in (x, y)
                ):
                    unknown.append(
                        {
                            "host": name,
                            "section": section,
                            "reason": "Missing, unknown or truncated evidence.",
                        }
                    )
                    continue
                left = {json.dumps(v, sort_keys=True): v for v in x.get("items", [])}
                right = {json.dumps(v, sort_keys=True): v for v in y.get("items", [])}
                if left.keys() != right.keys():
                    changes.append(
                        {
                            "host": name,
                            "section": section,
                            "removed": [
                                left[k] for k in sorted(left.keys() - right.keys())
                            ],
                            "added": [
                                right[k] for k in sorted(right.keys() - left.keys())
                            ],
                        }
                    )
        return {
            "before": before,
            "after": after,
            "added_hosts": sorted(b.keys() - a.keys()),
            "removed_hosts": sorted(a.keys() - b.keys()),
            "changes": changes,
            "unavailable": unknown,
            "note": "Differences are recorded changes, not automatic security findings.",
        }

    async def baseline(self, actor, client, data):
        self.store.require(actor, "baselines")
        site = data.get("site", "")
        self.require_site(actor, client, site)
        job = await self.job(actor, client, data.get("job_id", ""))
        if job["operation"] != "inventory" or job["status"] in ACTIVE:
            raise ValueError("Choose a terminal inventory collection.")
        if not set(job["hosts"]).intersection(
            h.name for h in self.selected(actor, client, site)
        ):
            raise ValueError("Baseline has no devices in this site.")
        rows = [
            r
            for r in self.records(actor, "baseline", client, site)
            if r["site"] == site
        ]
        old = rows[0] if rows else None
        return self.store.save(
            "baseline",
            client,
            site,
            actor,
            {"job_id": job["id"], "set_at": stamp(), "set_by": actor["name"]},
            old["id"] if old else None,
            data.get("revision"),
        )

    def onboarding(self, actor, client, site=""):
        snap = self.snapshot(actor, client, site)
        commands = {
            "windows": f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\Install-Assessment.ps1" -Engagement "{client}" -AllowedIP "ENGINEER_IPV4"',
            "macos": "Use approved Remote Login access and Python 3. Verify the SSH host fingerprint at the Mac console before registration.",
            "linux": "Use an existing approved unprivileged SSH account with Python 3. Verify the host key through your trusted provisioning process.",
        }
        return {
            "access": snap["access"],
            "devices": [
                {
                    "name": h["name"],
                    "platform": h["platform"],
                    "site": h.get("site", "main"),
                    "approved": h["approved"],
                    "collection": h["collection_status"],
                    "readiness": h.get("readiness"),
                    "freshness": h["freshness"],
                }
                for h in snap["hosts"]
            ],
            "instructions": commands,
            "windows_kit_command": f"remote-fleet-agent init-client {shlex.quote(client)} --stage-dir ./assessment-kit --allowed-ip ENGINEER_IPV4",
            "note": "Instructions only. No SSH service, user, dependency, key or installer is provisioned automatically. The Windows kit command runs separately on the engineer laptop and copies only its public key.",
        }

    def activity(self, actor, client, site=""):
        self.grants(actor, client)
        result = []
        for row in self.store.db.execute(
            "SELECT * FROM activity WHERE client=? ORDER BY seq DESC LIMIT 1000",
            (client,),
        ):
            if self.permitted(actor, client, row["site"]) and (
                not site or row["site"] == site
            ):
                result.append(
                    {
                        k: row[k]
                        for k in ("seq", "ts", "actor", "event", "site", "signature")
                    }
                )
        return {"integrity": self.store.integrity, "events": result[:200]}

    async def report(self, actor, client, site="", shareable=True):
        snap = self.snapshot(actor, client, site)
        tasks = snap["tasks"]
        brand = snap["branding"]
        baselines = [r for r in snap["baselines"] if r["site"] == site]
        counts = {
            "Devices": len(snap["hosts"]),
            "Recorded observations": sum(len(h["findings"]) for h in snap["hosts"]),
            "Stale evidence": sum(
                h["freshness"]["state"] == "stale" for h in snap["hosts"]
            ),
            "Uncollected devices": sum(
                not h["last_collected_at"] for h in snap["hosts"]
            ),
            "Open actions": sum(
                t["status"] not in ("verified", "dismissed") for t in tasks
            ),
            "Verified actions": sum(t["status"] == "verified" for t in tasks),
        }
        progress = "No inventory baseline is selected for this scope."
        inventories = [
            j
            for j in snap["jobs"]
            if j["operation"] == "inventory" and j["status"] not in ACTIVE
        ]
        if baselines and inventories:
            try:
                diff = await self.compare(
                    actor, client, baselines[0]["job_id"], inventories[-1]["id"], site
                )
                progress = f"{len(diff['changes'])} changed host/section combinations, {len(diff['added_hosts'])} devices only in later evidence and {len(diff['removed_hosts'])} devices absent from later evidence since the selected baseline. {len(diff['unavailable'])} section comparisons are unavailable."
            except (ValueError, PermissionError, FileNotFoundError):
                progress = "The selected baseline could not be compared in this scope."
        output = shareable_snapshot(snap) if shareable else snap
        output["client"] = "Engagement" if shareable else brand["name"]
        output["note"] = snap["note"]
        report = render_report(output)
        accent = {
            "violet": "#7156d9",
            "blue": "#3879bd",
            "green": "#39816a",
            "slate": "#596477",
        }[brand["theme"]]
        report = report.replace(
            "</style>",
            ":root{--accent:" + accent + "}h2{color:" + accent + "}</style>",
            1,
        )
        report = report.replace(
            '<button onclick="window.print()">Print / save as PDF</button>',
            "<span>Use your browser’s Print command to print or save as PDF.</span>",
        )
        e = html.escape
        summary = (
            '<section class="panel" style="padding:22px"><h2>Executive summary</h2><p>'
            + ("Redacted client summary" if shareable else e(brand["name"]))
            + " · Prepared by "
            + e("IT support" if shareable else brand["prepared_by"])
            + "</p><ul>"
            + "".join(
                f"<li>{e(k)}: <strong>{v}</strong></li>" for k, v in counts.items()
            )
            + "</ul><p>"
            + e(progress)
            + "</p><p>Unknown or incomplete evidence is not a pass. Verification statuses are operator-recorded and reference saved evidence.</p></section>"
        )
        if shareable:
            summary += '<section class="panel" style="padding:22px"><h2>Action progress</h2><p>Owners, notes, device identities and evidence references are omitted from this shared copy.</p></section>'
        else:
            summary += (
                '<section class="panel scroll"><div class="panel-head"><h2>Action plan</h2></div><table><thead><tr><th>Action</th><th>Owner</th><th>Priority</th><th>Due</th><th>Status</th><th>Verification</th></tr></thead><tbody>'
                + "".join(
                    "<tr>"
                    + "".join(
                        "<td>" + e(str(t.get(k, ""))) + "</td>"
                        for k in (
                            "title",
                            "owner",
                            "priority",
                            "due",
                            "status",
                            "verification_job",
                        )
                    )
                    + "</tr>"
                    for t in tasks
                )
                + "</tbody></table></section>"
            )
        return report.replace("<main>", "<main>" + summary, 1)

    async def close(self):
        await self.repairs.close()
        await self.support.close()
        for _, rt in self.runtimes.values():
            await rt.close()
        self.store.close()
