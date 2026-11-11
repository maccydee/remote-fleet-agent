from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import wraps
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from remote_fleet_agent.batches import BatchManager
from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.maintenance import ACTIONS
from remote_fleet_agent.runtime import OPERATIONS, FleetRuntime
from remote_fleet_agent.tools.fit_check import fit_check as _fit_check
from remote_fleet_agent.tools.run_check import CHECKS
from remote_fleet_agent.tools.run_check import run_check as _run_check
from remote_fleet_agent.workspace import EvidenceWorkspace


def build_server(
    key_path: str, *, config: FleetConfig | None = None, job_dir: Path | None = None
) -> MCPServer:
    """Build a fleet server; maintenance requires explicit engagement opt-in."""
    if config is None and os.environ.get("REMOTE_FLEET_AGENT_CONFIG"):
        config = FleetConfig.load(os.environ["REMOTE_FLEET_AGENT_CONFIG"])
    if config and os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
        config = replace(config, maintenance_actions=())
    folder = job_dir or Path(
        os.environ.get(
            "REMOTE_FLEET_AGENT_JOB_DIR",
            str(Path.home() / ".remote-fleet-agent" / "jobs"),
        )
    )
    runtime = FleetRuntime(config, key_path, folder) if config else None

    workspace = EvidenceWorkspace(config, folder) if config else None
    batches = BatchManager(runtime, folder) if runtime else None

    @asynccontextmanager
    async def lifespan(_server):
        try:
            yield {}
        finally:
            if runtime:
                await runtime.close()

    server = MCPServer(
        "remote-fleet-agent", version="0.9.0", lifespan=lifespan, log_level="WARNING"
    )
    readonly = ToolAnnotations(read_only_hint=True, destructive_hint=False)
    job_control = ToolAnnotations(read_only_hint=False, destructive_hint=False)

    def safe_tool(annotations):
        def decorate(fn):
            @wraps(fn)
            async def invoke(*args, **kwargs):
                try:
                    return await fn(*args, **kwargs)
                except (ValueError, FileNotFoundError) as exc:
                    raise ToolError(str(exc)) from exc

            return server.tool(annotations=annotations)(invoke)

        return decorate

    def require() -> FleetRuntime:
        if runtime is None:
            raise ValueError(
                "Set REMOTE_FLEET_AGENT_CONFIG to an engagement file with approved hosts and verified known_hosts"
            )
        return runtime

    @safe_tool(annotations=readonly)
    async def describe_capabilities() -> dict:
        """List available fixed queries and whether an engagement is configured."""
        from remote_fleet_agent.mac_enrollment import MAC_SETUP_DEFAULTS

        return {
            "read_only": True,
            "configured": runtime is not None,
            "operations": {name: list(spec[0]) for name, spec in OPERATIONS.items()},
            "legacy_checks": list(CHECKS),
            "simulation": bool(config and config.simulation),
            "platforms": {
                p: {op: list(FleetRuntime.catalog(p, op)) for op in OPERATIONS}
                for p in ("windows", "macos", "linux")
            },
            "target_setup": {
                "macos": {**MAC_SETUP_DEFAULTS, "workspace_page": "Settings"}
            },
            "maintenance_platforms": ["windows"],
            "mutating_endpoint_tools": [],
            "administration": "Use the workspace support MCP bridge for human-approved repairs",
            "maintenance_actions": {
                name: ACTIONS[name]
                for name in (config.maintenance_actions if config else ())
            },
            "note": "Evidence collection is not a compliance verdict. SSH credentials retain their Windows account privileges.",
        }

    @safe_tool(annotations=readonly)
    async def list_hosts() -> dict:
        """List only configured, approved hosts and their groups. Performs no network discovery."""
        return {
            "client": config.client if config else None,
            "hosts": runtime.hosts() if runtime else [],
        }

    @safe_tool(annotations=readonly)
    async def inventory(host: str, sections: list[str] | None = None) -> dict:
        """Collect selected inventory evidence from one approved host."""
        return await require().collect(host, "inventory", sections)

    @safe_tool(annotations=readonly)
    async def diagnostics(host: str, sections: list[str] | None = None) -> dict:
        """Collect bounded diagnostic metadata without repairs, event messages or external probes."""
        return await require().collect(host, "diagnostics", sections)

    @safe_tool(annotations=readonly)
    async def readiness(host: str) -> dict:
        """Read access, command availability, disk headroom and cleanup expiry; no repair."""
        return await require().collect(host, "readiness")

    @safe_tool(annotations=readonly)
    async def fleet_status() -> dict:
        """Summarize saved host evidence, readiness, findings, manual evidence and batches. No SSH."""
        require()
        snapshot = workspace.snapshot()
        for host in snapshot["hosts"]:
            host["sections"] = {
                name: {
                    k: section.get(k) for k in ("status", "total", "truncated", "error")
                }
                for name, section in host["sections"].items()
            }
        snapshot["manual"] = [
            {
                k: record.get(k)
                for k in (
                    "id",
                    "asset",
                    "category",
                    "control",
                    "status",
                    "revision",
                    "updated_at",
                )
            }
            for record in snapshot["manual"]
        ]
        return snapshot

    @safe_tool(annotations=job_control)
    async def export_evidence(kind: str = "report", shareable: bool = True) -> dict:
        """Create a managed offline dashboard/report. Shareable mode omits raw evidence and identities."""
        require()
        return workspace.export(kind, shareable)

    @safe_tool(annotations=readonly)
    async def list_manual_evidence() -> list[dict]:
        """Read saved operator-recorded appliance/cloud evidence and revisions."""
        require()
        return workspace.manual_records()

    @safe_tool(annotations=job_control)
    async def add_manual_evidence(
        asset: str,
        category: str,
        control: str,
        reviewer: str,
        status: str = "open",
        note: str = "",
        reference: str = "",
        job_id: str = "",
    ) -> dict:
        """Record manual NAS/M365/router/CCTV/backup evidence; no password or secret values."""
        require()
        return workspace.add_manual(
            asset=asset,
            category=category,
            control=control,
            reviewer=reviewer,
            status=status,
            note=note,
            reference=reference,
            job_id=job_id,
        )

    @safe_tool(annotations=job_control)
    async def update_manual_evidence(
        record_id: str, expected_revision: int, fields: dict
    ) -> list[dict]:
        """Replace a manual record's fields only at its current revision; preserve history."""
        require()
        return workspace.import_manual(
            [
                {
                    "id": record_id,
                    "expected_revision": expected_revision,
                    "fields": fields,
                }
            ]
        )

    @safe_tool(annotations=job_control)
    async def preview_retention(
        days: int = 90, keep_recent: int = 5, include_partial: bool = False
    ) -> dict:
        """Preview exact managed collection/export files eligible for deletion; deletes nothing."""
        require()
        return workspace.preview_retention(days, keep_recent, include_partial)

    @safe_tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
    async def apply_retention(plan_id: str, expected_digest: str) -> dict:
        """Permanently delete the exact reviewed retention preview. Retains manual and maintenance journals."""
        require()
        return workspace.apply_retention(plan_id, expected_digest)

    @safe_tool(annotations=readonly)
    async def run_check(host: str, check_name: str) -> dict | list:
        """Run one legacy fixed read-only query on an approved host."""
        if check_name not in CHECKS:
            raise ValueError(f"unknown check; choose from {list(CHECKS)}")
        require().require_windows(host)
        return await _run_check(await require().connection(host), check_name)

    @safe_tool(annotations=readonly)
    async def fit_check(host: str, source_path: str, dest_drive: str) -> dict:
        """Measure a requested folder and destination capacity. No files are copied."""
        from dataclasses import asdict

        require().require_windows(host)
        return asdict(
            await _fit_check(
                await require().connection(host),
                source_path=source_path,
                dest_drive=dest_drive,
            )
        )

    @safe_tool(annotations=job_control)
    async def start_job(
        operation: str,
        hosts: list[str] | None = None,
        group: str | None = None,
        sections: list[str] | None = None,
    ) -> dict:
        """Start a saved inventory/diagnostic job for explicit approved hosts OR one approved group."""
        return require().start(operation, hosts, group, sections)

    @safe_tool(annotations=readonly)
    async def get_job(job_id: str) -> dict:
        """Get progress and per-host/section outcomes, without returning the full evidence payload."""
        return require().jobs.summary(require().jobs.get(job_id))

    @safe_tool(annotations=readonly)
    async def get_host_result(job_id: str, host: str) -> dict:
        """Read one named host's saved evidence from an existing job; makes no remote request."""
        job = require().jobs.get(job_id)
        if host not in job["hosts"]:
            raise ValueError("host is not in this job")
        return job["hosts"][host]

    @safe_tool(annotations=readonly)
    async def list_jobs(limit: int = 20) -> list[dict]:
        """List recent jobs for this engagement."""
        return require().jobs.list(limit)

    @safe_tool(annotations=job_control)
    async def retry_job(job_id: str) -> dict:
        """Create a linked job for unsuccessful hosts only, re-checking current approval."""
        return require().retry(job_id)

    @safe_tool(annotations=job_control)
    async def cancel_job(job_id: str) -> dict:
        """Cancel this server's collection job. Already saved evidence is retained."""
        return await require().jobs.cancel(job_id)

    @safe_tool(annotations=readonly)
    async def compare_inventory(before_job_id: str, after_job_id: str) -> dict:
        """Compare finished inventory snapshots; unknown/truncated sections are excluded from change claims."""
        return require().jobs.compare(before_job_id, after_job_id)

    if config and config.maintenance_actions:

        @safe_tool(annotations=job_control)
        async def preview_batch(
            action: str,
            hosts: list[str] | None = None,
            group: str | None = None,
            canary: str | None = None,
            wave_size: int = 1,
            max_failures: int = 1,
        ) -> dict:
            """Read readiness and create per-host change previews; no mutations. Review scope/skips/digest."""
            return await batches.preview(
                action, hosts, group, canary, wave_size, max_failures
            )

        @safe_tool(annotations=readonly)
        async def get_batch(batch_id: str) -> dict:
            """Read batch progress and child plan IDs; no endpoint request or automatic recovery."""
            return batches.get(batch_id)

        @safe_tool(annotations=job_control)
        async def cancel_batch(batch_id: str) -> dict:
            """Prevent later waves; in-flight operations may finish and are retained."""
            return batches.cancel(batch_id)

        @safe_tool(annotations=job_control)
        async def preview_maintenance(host: str, action: str) -> dict:
            """Read state and save a ten-minute change preview. No endpoint changes."""
            return await require().maintenance.preview(host, action)

        @safe_tool(annotations=readonly)
        async def get_maintenance(plan_id: str) -> dict:
            """Read the saved change record, including uncertain outcomes; does not retry."""
            return require().maintenance.get(plan_id)

    return server


async def main() -> None:
    key_path = os.environ.get("REMOTE_FLEET_AGENT_KEY_PATH")
    config_path = os.environ.get("REMOTE_FLEET_AGENT_CONFIG")
    config = FleetConfig.load(config_path) if config_path else None
    if not key_path and not (config and config.simulation):
        raise RuntimeError(
            "REMOTE_FLEET_AGENT_KEY_PATH must identify the engagement private key"
        )
    await build_server(key_path or "simulation-no-key", config=config).run_stdio_async()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
