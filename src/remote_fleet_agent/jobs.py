"""Persistent read-only fleet jobs with bounded concurrency and explicit retries."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from remote_fleet_agent.host_platform import process_exists
from remote_fleet_agent.storage import save_json
from remote_fleet_agent.tools.collect import timestamp

Worker = Callable[[str, str, list[str] | None], Awaitable[dict]]
ACTIVE = {"queued", "running"}
RETRYABLE = {"failed", "partial", "interrupted", "cancelled"}


class JobManager:
    def __init__(self, root: Path, client: str, worker: Worker, concurrency: int = 4):
        self.path = Path(root) / client
        self.client = client
        self.worker = worker
        self.semaphore = asyncio.Semaphore(concurrency)
        self.tasks: dict[str, asyncio.Task] = {}
        self.live: dict[str, dict] = {}

    def _file(self, job_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise ValueError("invalid job ID")
        return self.path / (job_id + ".json")

    def _save(self, job: dict) -> None:
        save_json(self._file(job["id"]), job)

    def get(self, job_id: str) -> dict:
        job = self.live.get(job_id)
        if job is None:
            job = json.loads(self._file(job_id).read_text())
        if job.get("client") != self.client:
            raise ValueError("job belongs to another engagement")
        if (
            job["status"] in ACTIVE
            and job_id not in self.tasks
            and not process_exists(job.get("owner_pid"))
        ):
            job["status"] = "interrupted"
            for host in job["hosts"].values():
                if host["status"] in ACTIVE:
                    host["status"] = "interrupted"
            self._save(job)

        return job

    def list(self, limit: int = 20) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        paths = sorted(
            self.path.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        return [self.summary(self.get(p.stem)) for p in paths[:limit]]

    @staticmethod
    def summary(job: dict) -> dict:
        result = {
            k: v
            for k, v in job.items()
            if k not in ("hosts", "owner_pid", "cancel_requested")
        }
        result["hosts"] = {}
        for name, host in job["hosts"].items():
            item = {k: v for k, v in host.items() if k != "result"}
            if "result" in host:
                item["sections"] = {
                    k: {
                        "status": s["status"],
                        "total": s.get("total"),
                        "truncated": s.get("truncated", False),
                        "error": s.get("error"),
                    }
                    for k, s in host["result"]["sections"].items()
                }
            result["hosts"][name] = item
        return result

    def start(
        self,
        hosts: list[str],
        operation: str,
        sections: list[str] | None = None,
        parent_id: str | None = None,
    ) -> dict:
        if not hosts or len(set(hosts)) != len(hosts):
            raise ValueError("a nonempty unique host scope is required")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "client": self.client,
            "operation": operation,
            "sections": sections,
            "parent_id": parent_id,
            "status": "queued",
            "created_at": timestamp(),
            "owner_pid": os.getpid(),
            "cancel_requested": False,
            "hosts": {host: {"status": "queued"} for host in hosts},
        }
        self._save(job)
        self.live[job_id] = job
        self.tasks[job_id] = asyncio.create_task(self._run(job))
        return self.summary(job)

    async def _run(self, job: dict) -> None:
        async def one(name: str, entry: dict):
            async with self.semaphore:
                entry.update(status="running", started_at=timestamp())
                self._save(job)
                try:
                    result = await self.worker(name, job["operation"], job["sections"])
                    entry.update(status=result["status"], result=result)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - each host failure must be saved without stopping other hosts
                    entry.update(status="failed", error=str(exc)[:1000])
                entry["finished_at"] = timestamp()
                self._save(job)

        job.update(status="running", started_at=timestamp())
        self._save(job)
        try:
            await asyncio.gather(
                *(one(name, entry) for name, entry in job["hosts"].items())
            )
            states = [h["status"] for h in job["hosts"].values()]
            job["status"] = (
                "complete" if all(s == "complete" for s in states) else "partial"
            )
            if all(s == "failed" for s in states):
                job["status"] = "failed"
        except asyncio.CancelledError:
            state = "cancelled" if job["cancel_requested"] else "interrupted"
            job["status"] = state
            for host in job["hosts"].values():
                if host["status"] in ACTIVE:
                    host["status"] = state
        finally:
            job["finished_at"] = timestamp()
            self._save(job)

    async def wait(self, job_id: str) -> dict:
        if job_id in self.tasks:
            await self.tasks[job_id]
        return self.get(job_id)

    async def cancel(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] not in ACTIVE:
            return self.summary(job)
        task = self.tasks.get(job_id)
        if task is None:
            raise ValueError("job is owned by another running server; cancel it there")
        job["cancel_requested"] = True
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        job["status"] = "cancelled"
        for host in job["hosts"].values():
            if host["status"] in ACTIVE:
                host["status"] = "cancelled"
        job["finished_at"] = timestamp()
        self._save(job)
        return self.summary(job)

    def retry_hosts(self, job_id: str) -> tuple[dict, list[str]]:
        job = self.get(job_id)
        if job["status"] in ACTIVE:
            raise ValueError("wait for or cancel the original job before retrying")
        hosts = [n for n, h in job["hosts"].items() if h["status"] in RETRYABLE]
        if not hosts:
            raise ValueError("this job has no unsuccessful hosts to retry")
        return job, hosts

    async def close(self):
        pending = [task for task in self.tasks.values() if not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    def compare(self, before_id: str, after_id: str) -> dict:
        before, after = self.get(before_id), self.get(after_id)
        if any(
            j["operation"] != "inventory" or j["status"] in ACTIVE
            for j in (before, after)
        ):
            raise ValueError("compare requires two finished inventory jobs")
        a, b = before["hosts"], after["hosts"]
        result = {
            "before": before_id,
            "after": after_id,
            "added_hosts": sorted(b.keys() - a.keys()),
            "removed_hosts": sorted(a.keys() - b.keys()),
            "hosts": {},
        }
        for name in sorted(a.keys() & b.keys()):
            previous = a[name].get("result", {}).get("sections", {})
            current = b[name].get("result", {}).get("sections", {})
            changed, unavailable = [], []
            for section in sorted(previous.keys() | current.keys()):
                x, y = previous.get(section, {}), current.get(section, {})
                if any(
                    z.get("status") != "collected" or z.get("truncated") for z in (x, y)
                ):
                    unavailable.append(section)
                    continue
                normalize = lambda z: sorted(
                    json.dumps(v, sort_keys=True) for v in z["items"]
                )
                if normalize(x) != normalize(y):
                    changed.append(section)
            result["hosts"][name] = {
                "changed_sections": changed,
                "unavailable_sections": unavailable,
                "before_status": a[name]["status"],
                "after_status": b[name]["status"],
            }
        return result
