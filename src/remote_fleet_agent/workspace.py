"""Offline engagement evidence, manual records, exports and reviewed retention."""

from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remote_fleet_agent.storage import (
    atomic_text,
    digest,
    locked,
    private_dir,
    read_json,
    save_json,
    stamp,
    valid_id,
)

CATEGORIES = ("nas", "m365", "router", "cctv", "backup", "other")
CONTROLS = ("0001", "2314", "2500")
MANUAL_STATES = ("open", "verified", "unknown", "not_applicable")


def parse_time(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


class EvidenceWorkspace:
    def __init__(self, config, root):
        self.config = config
        root = Path(root) / "simulation" if config.simulation else Path(root)
        self.path = private_dir(root / config.client)
        self.manual_path = self.path / "manual-evidence.json"
        self.lock = self.path / ".workspace.lock"

    def manual_records(self):
        if not self.manual_path.exists():
            return []
        data = read_json(self.manual_path)
        if data["client"] != self.config.client:
            raise ValueError("manual evidence belongs to another client")
        return data["records"]

    @staticmethod
    def validate_manual(fields):
        allowed = {
            "asset",
            "category",
            "control",
            "status",
            "note",
            "reference",
            "reviewer",
            "job_id",
            "site",
        }
        if set(fields) - allowed:
            raise ValueError("unknown manual evidence fields")
        result = {name: fields.get(name, "") for name in allowed}
        for name, maximum in {
            "site": 64,
            "asset": 160,
            "note": 4000,
            "reference": 1000,
            "reviewer": 160,
        }.items():
            if not isinstance(result[name], str) or len(result[name]) > maximum:
                raise ValueError(f"invalid {name} length/type")
        if not result["asset"].strip() or not result["reviewer"].strip():
            raise ValueError("asset and reviewer are required")
        if (
            result["category"] not in CATEGORIES
            or result["control"] not in CONTROLS
            or result["status"] not in MANUAL_STATES
        ):
            raise ValueError("invalid category, control or manual evidence status")
        if result["site"] and not __import__("re").fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", result["site"]
        ):
            raise ValueError("invalid site")
        if result["job_id"]:
            valid_id(result["job_id"])
        return result

    def import_manual(self, changes):
        if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
            raise ValueError("provide between one and 500 manual evidence changes")
        with locked(self.lock):
            records = copy.deepcopy(self.manual_records())
            for change in changes:
                if not isinstance(change, dict) or set(change) - {
                    "id",
                    "expected_revision",
                    "fields",
                }:
                    raise ValueError("invalid manual change shape")
                fields = self.validate_manual(change.get("fields", {}))
                if fields["job_id"]:
                    job = read_json(self.path / (fields["job_id"] + ".json"))
                    if job.get("client") != self.config.client:
                        raise ValueError("referenced job belongs to another client")
                ident = change.get("id")
                if ident:
                    valid_id(ident)
                    current = next((r for r in records if r["id"] == ident), None)
                    if (
                        current is None
                        or type(change.get("expected_revision")) is not int
                        or change["expected_revision"] != current["revision"]
                    ):
                        raise ValueError(
                            "manual evidence changed since editing; refresh its revision"
                        )
                    revision = current["revision"] + 1
                    history = current["history"] + [
                        {
                            "revision": current["revision"],
                            "updated_at": current["updated_at"],
                            "fields": {k: current[k] for k in fields},
                        }
                    ]
                    current.update(
                        fields, revision=revision, updated_at=stamp(), history=history
                    )
                else:
                    if change.get("expected_revision") not in (None, 0):
                        raise ValueError("new records cannot have an existing revision")
                    records.append(
                        {
                            "id": uuid.uuid4().hex,
                            "revision": 1,
                            "created_at": stamp(),
                            "updated_at": stamp(),
                            "history": [],
                            **fields,
                        }
                    )
            save_json(
                self.manual_path, {"client": self.config.client, "records": records}
            )
        return records

    def add_manual(self, **fields):
        records = self.import_manual([{"fields": fields}])
        return records[-1]

    def jobs(self):
        jobs, errors = [], []
        for path in sorted(self.path.glob("*.json")):
            if len(path.stem) != 32:
                continue
            try:
                valid_id(path.stem)
                data = read_json(path)
                if (
                    data.get("client") != self.config.client
                    or data.get("id") != path.stem
                    or not isinstance(data.get("hosts"), dict)
                ):
                    raise ValueError("job identity/shape mismatch")
                jobs.append(data)
            except (ValueError, OSError) as exc:
                errors.append({"file": path.name, "error": str(exc)[:300]})
        return sorted(jobs, key=lambda j: j.get("created_at", "")), errors

    def snapshot(self):
        from remote_fleet_agent.reporting import findings_for

        jobs, errors = self.jobs()
        hosts = {
            h.name: {
                "name": h.name,
                "target": h.target,
                "platform": h.platform,
                "site": h.site,
                "port": h.port,
                "simulation": self.config.simulation,
                "groups": list(h.groups),
                "approved": True,
                "sections": {},
                "sources": {},
                "collection_status": "not_collected",
                "last_collected_at": None,
                "readiness": None,
            }
            for h in self.config.hosts
        }
        summaries = []
        for job in jobs:
            summaries.append(
                {
                    k: job.get(k)
                    for k in ("id", "operation", "status", "created_at", "finished_at")
                }
                | {
                    "hosts": {
                        n: h.get("status", "unknown") for n, h in job["hosts"].items()
                    }
                }
            )
            for name, entry in job["hosts"].items():
                row = hosts.setdefault(
                    name,
                    {
                        "name": name,
                        "target": "",
                        "platform": "unknown",
                        "site": "",
                        "simulation": False,
                        "groups": [],
                        "approved": False,
                        "sections": {},
                        "sources": {},
                        "collection_status": "not_collected",
                        "last_collected_at": None,
                        "readiness": None,
                    },
                )
                row["collection_status"] = entry.get("status", "unknown")
                evidence = entry.get("result", {})
                if evidence.get("platform") in ("windows", "macos", "linux"):
                    row["platform"] = evidence["platform"]
                if evidence.get("simulation") is True:
                    row["simulation"] = True
                when = (
                    entry.get("finished_at")
                    or evidence.get("finished_at")
                    or job.get("created_at")
                )
                if evidence and (
                    row["last_collected_at"] is None
                    or (when or "") >= row["last_collected_at"]
                ):
                    row["last_collected_at"] = when
                for section, result in evidence.get("sections", {}).items():
                    key = job.get("operation", "unknown") + "." + section
                    previous = row["sources"].get(key, {}).get("collected_at")
                    if previous is None or (when or "") >= previous:
                        row["sections"][key] = result
                        row["sources"][key] = {
                            "job_id": job["id"],
                            "collected_at": when,
                        }
                if evidence.get("kind") == "readiness":
                    previous = (row["readiness"] or {}).get("checked_at")
                    if previous is None or (when or "") >= previous:
                        row["readiness"] = {
                            "checked_at": when,
                            "connection": evidence.get("connection", "unknown"),
                            **evidence.get("readiness", {}),
                        }
        rows = list(hosts.values())
        for row in rows:
            row["findings"] = findings_for(row)
        batches = []
        for path in sorted((self.path / "batches").glob("*.json")):
            try:
                b = read_json(path)
                if b.get("spec", {}).get("client") == self.config.client:
                    batches.append(
                        {
                            "id": b["id"],
                            "action": b["spec"]["action"],
                            "status": b["status"],
                            "canary": b["spec"]["canary"],
                            "hosts": b["hosts"],
                            "created_at": b["spec"]["created_at"],
                        }
                    )
            except (OSError, ValueError, KeyError) as exc:
                errors.append({"file": path.name, "error": str(exc)[:300]})
        return {
            "schema": "fleet-workspace-v1",
            "client": self.config.client,
            "simulation": self.config.simulation,
            "generated_at": stamp(),
            "hosts": rows,
            "jobs": summaries,
            "manual": self.manual_records(),
            "batches": batches,
            "storage_errors": errors,
            "note": "Saved evidence only. Connectivity and readiness are point-in-time observations; this is not a compliance verdict.",
        }

    def export(self, kind="dashboard", shareable=True):
        from remote_fleet_agent.reporting import (
            render_dashboard,
            render_report,
            shareable_snapshot,
        )

        if kind not in ("dashboard", "report"):
            raise ValueError("export kind must be dashboard or report")
        data = self.snapshot()
        if shareable:
            data = shareable_snapshot(data)
        html = (
            render_dashboard(data, private=not shareable)
            if kind == "dashboard"
            else render_report(data)
        )
        ident = uuid.uuid4().hex
        directory = private_dir(self.path / "exports")
        with locked(self.lock):
            atomic_text(directory / (ident + ".html"), html)
            save_json(
                directory / (ident + ".json"),
                {
                    "id": ident,
                    "kind": kind,
                    "created_at": stamp(),
                    "shareable": shareable,
                },
            )
        return {
            "id": ident,
            "path": str(directory / (ident + ".html")),
            "shareable": shareable,
            "kind": kind,
        }

    def referenced_jobs(self):
        references = set()
        for record in self.manual_records():
            for fields in [
                record,
                *[h.get("fields", {}) for h in record.get("history", [])],
            ]:
                if fields.get("job_id"):
                    references.add(fields["job_id"])
        return references

    def preview_retention(self, days=90, keep_recent=5, include_partial=False):
        if (
            type(days) is not int
            or not 1 <= days <= 3650
            or type(keep_recent) is not int
            or not 0 <= keep_recent <= 1000
        ):
            raise ValueError("days must be 1..3650 and keep_recent 0..1000")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        referenced = self.referenced_jobs()
        jobs, _ = self.jobs()
        kept = set()
        for operation in ("inventory", "diagnostics", "readiness"):
            ordered = [j for j in jobs if j.get("operation") == operation]
            kept.update(j["id"] for j in ordered[-keep_recent:] if keep_recent)
        candidates = []
        for job in jobs:
            statuses = (
                ("complete", "partial", "failed", "interrupted", "cancelled")
                if include_partial
                else ("complete",)
            )
            try:
                old = parse_time(job.get("finished_at", "")) < cutoff
            except (TypeError, ValueError):
                continue
            if old and job["status"] in statuses and job["id"] not in kept | referenced:
                candidates.append(self.path / (job["id"] + ".json"))
        candidate_ids = {p.stem for p in candidates}
        protected = {j["id"] for j in jobs if j["id"] not in candidate_ids} | referenced
        while True:
            parents = {
                j.get("parent_id")
                for j in jobs
                if j["id"] in protected and j.get("parent_id")
            }
            expanded = protected | parents
            if expanded == protected:
                break
            protected = expanded
        candidates = [p for p in candidates if p.stem not in protected]
        exports = []
        for meta in (self.path / "exports").glob("*.json"):
            try:
                record = read_json(meta)
                valid_id(record["id"])
                if record["id"] != meta.stem:
                    continue
                exports.append((parse_time(record["created_at"]), meta))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        for when, meta in sorted(exports, reverse=True)[keep_recent:]:
            if when < cutoff:
                candidates.extend([meta, meta.with_suffix(".html")])
        files = []
        for path in candidates:
            if path.is_symlink() or not path.is_file():
                continue
            body = path.read_bytes()
            files.append(
                {
                    "path": str(path.relative_to(self.path)),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                }
            )
        plan = {
            "id": uuid.uuid4().hex,
            "client": self.config.client,
            "created_at": stamp(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
            "days": days,
            "keep_recent": keep_recent,
            "include_partial": include_partial,
            "files": files,
            "bytes": sum(f["bytes"] for f in files),
            "note": "Permanent deletion of listed managed copies only. Manual records, maintenance/batch journals, referenced jobs, retained retry lineage and external copies are retained.",
        }
        plan["digest"] = digest(plan)
        save_json(
            private_dir(self.path / "retention") / (plan["id"] + ".json"),
            {"plan": plan, "status": "preview"},
        )
        return plan

    def apply_retention(self, plan_id, expected_digest):
        path = self.path / "retention" / (valid_id(plan_id) + ".json")
        with locked(self.lock):
            record = read_json(path)
            plan = record["plan"]
            immutable = {k: v for k, v in plan.items() if k != "digest"}
            if (
                record["status"] != "preview"
                or plan["client"] != self.config.client
                or digest(immutable) != expected_digest
                or plan["digest"] != expected_digest
                or parse_time(plan["expires_at"]) <= datetime.now(UTC)
            ):
                raise ValueError("retention preview changed, expired or already used")
            referenced = self.referenced_jobs()
            planned_jobs = {
                Path(item["path"]).stem
                for item in plan["files"]
                if len(Path(item["path"]).parts) == 1
            }
            current_jobs, _ = self.jobs()
            retained = {j["id"] for j in current_jobs if j["id"] not in planned_jobs}
            while True:
                expanded = retained | {
                    j.get("parent_id")
                    for j in current_jobs
                    if j["id"] in retained and j.get("parent_id")
                }
                if expanded == retained:
                    break
                retained = expanded
            referenced |= retained
            targets = []
            for item in plan["files"]:
                relative = Path(item["path"])
                target = self.path / relative
                allowed = (
                    len(relative.parts) == 1
                    and relative.suffix == ".json"
                    and relative.stem not in referenced
                ) or (
                    len(relative.parts) == 2
                    and relative.parts[0] == "exports"
                    and relative.suffix in (".json", ".html")
                )
                if not allowed or ".." in relative.parts or relative.is_absolute():
                    raise ValueError("retention target is not a managed evidence file")
                valid_id(relative.stem)
                if (
                    target.is_symlink()
                    or not target.resolve().is_relative_to(self.path.resolve())
                    or not target.is_file()
                    or hashlib.sha256(target.read_bytes()).hexdigest() != item["sha256"]
                ):
                    raise ValueError(
                        "retention candidate changed or is unsafe; create a new preview"
                    )
                if len(relative.parts) == 1:
                    current = read_json(target)
                    if current.get("client") != self.config.client or current.get(
                        "status"
                    ) in ("running", "queued"):
                        raise ValueError(
                            "retention target is active or belongs to another engagement"
                        )
                targets.append(target)
            record.update(status="deleting", started_at=stamp(), deleted=[])
            save_json(path, record)
            try:
                for target in targets:
                    target.unlink()
                    record["deleted"].append(str(target.relative_to(self.path)))
                    save_json(path, record)
                record["status"] = "complete"
            except OSError as exc:
                record.update(status="partial", error=str(exc))
                save_json(path, record)
                raise
            record["finished_at"] = stamp()
            save_json(path, record)
        return record
