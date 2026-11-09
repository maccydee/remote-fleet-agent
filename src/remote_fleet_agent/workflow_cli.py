"""CLI entry points for offline evidence and reviewed batch maintenance."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.maintenance import ACTIONS
from remote_fleet_agent.runtime import FleetRuntime
from remote_fleet_agent.storage import atomic_text
from remote_fleet_agent.workspace import EvidenceWorkspace


def register(sub):
    p = sub.add_parser(
        "workspace",
        help="Offline dashboard, reports, manual evidence and reviewed retention",
    )
    p.add_argument(
        "mode",
        choices=[
            "status",
            "dashboard",
            "report",
            "manual-list",
            "manual-add",
            "manual-update",
            "import-manual",
            "retention-preview",
            "retention-apply",
        ],
    )
    p.add_argument("--config", required=True, type=Path)
    p.add_argument("--job-dir", type=Path)
    p.add_argument(
        "--private",
        action="store_true",
        help="Include identities, raw evidence and manual notes in an export",
    )
    p.add_argument(
        "--output", type=Path, help="Optional additional copy outside managed retention"
    )
    p.add_argument("--input", type=Path)
    p.add_argument("--record-id")
    p.add_argument("--revision", type=int)
    p.add_argument("--asset")
    p.add_argument(
        "--category", choices=["nas", "m365", "router", "cctv", "backup", "other"]
    )
    p.add_argument("--control", choices=["0001", "2314", "2500"])
    p.add_argument(
        "--status",
        choices=["open", "verified", "unknown", "not_applicable"],
        default="open",
    )
    p.add_argument("--reviewer")
    p.add_argument("--note", default="")
    p.add_argument("--reference", default="")
    p.add_argument("--job-id", default="")
    p.add_argument("--days", type=int, default=90)
    p.add_argument("--keep-recent", type=int, default=5)
    p.add_argument("--include-partial", action="store_true")
    p.add_argument("--plan-id")
    p.add_argument("--digest")
    b = sub.add_parser(
        "batch",
        help="Preview, canary, continue, inspect or cancel fixed-action batches",
    )
    b.add_argument("mode", choices=["preview", "canary", "continue", "show", "cancel"])
    b.add_argument("--config", required=True, type=Path)
    b.add_argument("--key-path", type=Path)
    b.add_argument("--job-dir", type=Path)
    b.add_argument("--action", choices=list(ACTIONS))
    scope = b.add_mutually_exclusive_group()
    scope.add_argument("--hosts", nargs="+")
    scope.add_argument("--group")
    b.add_argument("--canary")
    b.add_argument("--wave-size", type=int, default=1)
    b.add_argument("--max-failures", type=int, default=1)
    b.add_argument("--batch-id")
    b.add_argument("--digest")


def execute(args):
    config = FleetConfig.load(
        args.config, require_known_hosts=args.command != "workspace"
    )
    if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
        config = replace(config, maintenance_actions=())
    root = args.job_dir or Path.home() / ".remote-fleet-agent" / "jobs"
    if args.command == "workspace":
        workspace = EvidenceWorkspace(config, root)
        if args.output and args.mode not in ("dashboard", "report"):
            raise ValueError("--output is only for dashboard/report copies")
        if args.mode == "status":
            result = workspace.snapshot()
        elif args.mode in ("dashboard", "report"):
            result = workspace.export(args.mode, shareable=not args.private)
            if args.output:
                # Export to an additional private file, never overwrite another managed record.
                destination = args.output.expanduser().resolve()
                if destination.is_relative_to(workspace.path.resolve()):
                    raise ValueError(
                        "additional export copy must be outside the managed store"
                    )
                atomic_text(destination, Path(result["path"]).read_text())
                result["additional_copy"] = str(destination)
        elif args.mode == "manual-list":
            result = workspace.manual_records()
        elif args.mode in ("manual-add", "manual-update"):
            fields = {
                name: getattr(args, name)
                for name in (
                    "asset",
                    "category",
                    "control",
                    "status",
                    "reviewer",
                    "note",
                    "reference",
                    "job_id",
                )
            }
            if args.mode == "manual-add":
                result = workspace.add_manual(**fields)
            else:
                if not args.record_id or args.revision is None:
                    raise ValueError("manual-update needs --record-id and --revision")
                result = workspace.import_manual(
                    [
                        {
                            "id": args.record_id,
                            "expected_revision": args.revision,
                            "fields": fields,
                        }
                    ]
                )
        elif args.mode == "import-manual":
            if not args.input:
                raise ValueError("import-manual needs --input")
            document = json.loads(args.input.read_text())
            if (
                document.get("schema") != "fleet-manual-import-v1"
                or document.get("client") != config.client
            ):
                raise ValueError("manual import schema or engagement does not match")
            result = workspace.import_manual(document.get("changes"))
        elif args.mode == "retention-preview":
            result = workspace.preview_retention(
                args.days, args.keep_recent, args.include_partial
            )
        else:
            if not args.plan_id or not args.digest:
                raise ValueError("retention-apply needs --plan-id and --digest")
            result = workspace.apply_retention(args.plan_id, args.digest)
    else:
        from remote_fleet_agent.batches import BatchManager

        if args.mode == "preview":
            if (
                not args.action
                or bool(args.hosts) == bool(args.group)
                or args.batch_id
                or args.digest
            ):
                raise ValueError(
                    "batch preview needs --action and either --hosts or --group"
                )
        elif (
            not args.batch_id or args.action or args.hosts or args.group or args.canary
        ):
            raise ValueError(
                "existing batch modes need --batch-id without scope/action overrides"
            )
        if args.mode in ("canary", "continue") and not args.digest:
            raise ValueError("batch execution needs the reviewed --digest")
        key = args.key_path or Path(os.environ.get("REMOTE_FLEET_AGENT_KEY_PATH", ""))
        if args.mode not in ("show", "cancel") and not key.is_file():
            raise ValueError(
                "supply an existing private key for batch preview/execution"
            )

        async def run():
            runtime = FleetRuntime(config, str(key), root)
            batch = BatchManager(runtime, root)
            try:
                if args.mode == "preview":
                    return await batch.preview(
                        args.action,
                        args.hosts,
                        args.group,
                        args.canary,
                        args.wave_size,
                        args.max_failures,
                    )
                if args.mode == "show":
                    return batch.get(args.batch_id)
                if args.mode == "cancel":
                    return batch.cancel(args.batch_id)
                return await batch.execute(
                    args.batch_id, args.digest, continuation=args.mode == "continue"
                )
            finally:
                await runtime.close()

        result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    status = result.get("status") if isinstance(result, dict) else None
    return (
        2
        if status
        in (
            "partial",
            "halted",
            "unknown",
            "interrupted",
            "blocked",
            "expired",
            "cancelled",
        )
        else 0
    )
