from __future__ import annotations

import shutil
from pathlib import Path

from remote_fleet_agent.host_platform import restrict_private
from remote_fleet_agent.storage import atomic_text, private_dir

KEYS_DIR = Path.home() / ".remote-fleet-agent" / "keys"


def init_client(
    client: str, *, stage_dir: Path, force: bool = False, allowed_ip: str | None = None
) -> None:
    """Generate a private per-engagement key and stage the bundled assessment kit."""
    import re
    from importlib.resources import files

    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,47}", client):
        raise ValueError(
            "client must use lowercase letters, numbers and hyphens, up to 48 characters"
        )
    if allowed_ip is not None:
        import ipaddress

        if ipaddress.ip_address(allowed_ip).version != 4:
            raise ValueError("allowed_ip must be one IPv4 address")
    client_key_dir = KEYS_DIR / client
    priv_path = client_key_dir / "id_ed25519"
    pub_path = client_key_dir / "id_ed25519.pub"
    stage_dir = stage_dir.expanduser().resolve()
    if (
        stage_dir == client_key_dir.resolve()
        or client_key_dir.resolve() in stage_dir.parents
    ):
        raise ValueError("stage directory must be separate from private key storage")
    if priv_path.exists():
        if not force:
            raise FileExistsError(
                f"keypair for client '{client}' already exists at {priv_path}; --force reuses it"
            )
        if not pub_path.is_file():
            raise FileNotFoundError(
                "existing public key is missing; refusing to overwrite private key"
            )
    else:
        if pub_path.exists():
            raise FileExistsError("public key already exists without its private key")
        import asyncssh

        private_dir(client_key_dir)
        private = asyncssh.generate_private_key(
            "ssh-ed25519", comment=f"remote-fleet-agent:{client}"
        )
        atomic_text(priv_path, private.export_private_key().decode())
        atomic_text(pub_path, private.export_public_key().decode())
    restrict_private(priv_path)
    stage_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pub_path, stage_dir / "id_ed25519.pub")
    assets = files("remote_fleet_agent").joinpath("assessment")
    for name in ("Install-Assessment.ps1", "Remove-Assessment.ps1", "Protect-Assessment.ps1"):
        text = assets.joinpath(name).read_text(encoding="utf-8")
        (stage_dir / name).write_text(text, encoding="utf-8")
        (stage_dir / name.replace(".ps1", ".txt")).write_text(text, encoding="utf-8")
    (stage_dir / "INSTALL-COMMAND.txt").write_text(
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\Install-Assessment.ps1" '
        f'-Engagement "{client}" -AllowedIP "{allowed_ip or "ENGINEER_IPV4"}"\n',
        encoding="utf-8",
    )
    (stage_dir / "READ-ME.txt").write_text(
        "Run elevated Windows PowerShell from this kit directory. Replace ENGINEER_IPV4 in INSTALL-COMMAND.txt with the engineer laptop's IPv4 address. "
        "Only the public key is included. The private key stays on the engineer laptop. "
        "Installation records the original state and schedules SYSTEM cleanup after three days, with retries. "
        f'Manual cleanup: powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\Remove-Assessment.ps1" -StateDirectory "$env:ProgramData\\RemoteFleetAgent-{client}"\n'
        "Verify each displayed host fingerprint at the Windows console before adding it to the engagement known_hosts. "
        "The installer establishes administrator SSH access; maintenance actions remain disabled unless separately configured. "
        "See project deployment documentation for testing limits and recovery.\n",
        encoding="utf-8",
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="remote-fleet-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser(
        "init-client", help="Generate a keypair and stage the USB install kit"
    )
    init_parser.add_argument("client")
    init_parser.add_argument("--stage-dir", required=True, type=Path)
    init_parser.add_argument(
        "--allowed-ip",
        help="Engineer laptop IPv4 address to insert into the staged command",
    )
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Reuse existing keypair and restage; never replace its private key",
    )
    fleet_parser = sub.add_parser(
        "run-fleet", help="Run and save a read-only inventory or diagnostics job"
    )
    fleet_parser.add_argument("--config", required=True, type=Path)
    fleet_parser.add_argument("--key-path", type=Path)
    fleet_parser.add_argument(
        "--operation",
        choices=["inventory", "diagnostics", "readiness"],
        default="inventory",
    )
    scope = fleet_parser.add_mutually_exclusive_group()
    scope.add_argument("--hosts", nargs="+")
    scope.add_argument("--group")
    fleet_parser.add_argument("--sections", nargs="+")
    fleet_parser.add_argument("--retry-job")
    fleet_parser.add_argument("--job-dir", type=Path)
    maintenance_parser = sub.add_parser(
        "maintenance",
        help="Preview, execute, inspect or reverse one opt-in fixed action",
    )
    maintenance_parser.add_argument(
        "mode", choices=["preview", "execute", "show", "rollback"]
    )
    maintenance_parser.add_argument("--config", required=True, type=Path)
    maintenance_parser.add_argument("--key-path", type=Path)
    maintenance_parser.add_argument("--job-dir", type=Path)
    maintenance_parser.add_argument("--host")
    maintenance_parser.add_argument(
        "--action",
        choices=["start_print_spooler", "start_time_service", "clear_dns_cache"],
    )
    maintenance_parser.add_argument("--plan-id")
    maintenance_parser.add_argument("--digest")
    from remote_fleet_agent import workflow_cli

    workflow_cli.register(sub)
    from remote_fleet_agent import lab_cli

    lab_cli.register(sub)
    from remote_fleet_agent import live_app

    live_app.register(sub)
    from remote_fleet_agent import integration

    integration.register(sub)
    support_parser = sub.add_parser("support-mcp", help="AI support cases with separate engineer approval")
    support_parser.add_argument("--workspace-url", default="http://127.0.0.1:8877")
    support_parser.add_argument("--operator-token-file", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "support-mcp":
        import asyncio

        from remote_fleet_agent.support_mcp import WorkspaceClient, build_support_server

        try:
            asyncio.run(build_support_server(WorkspaceClient(args.workspace_url, args.operator_token_file)).run_stdio_async())
        except (OSError, ValueError, RuntimeError) as exc:
            parser.error(str(exc))
        return
    if args.command in ("ai-config", "doctor"):
        try:
            raise SystemExit(integration.execute(args))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
    if args.command == "workspace-app":
        raise SystemExit(live_app.execute(args))
    if args.command in ("demo", "lab"):
        try:
            raise SystemExit(lab_cli.execute(args))
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            parser.error(str(exc))
    if args.command in ("workspace", "batch"):
        try:
            raise SystemExit(workflow_cli.execute(args))
        except (ValueError, FileNotFoundError) as exc:
            parser.error(str(exc))

    if args.command == "init-client":
        init_client(
            args.client,
            stage_dir=args.stage_dir,
            force=args.force,
            allowed_ip=args.allowed_ip,
        )
        print(f"Staged install kit for '{args.client}' at {args.stage_dir}")

    elif args.command in ("run-fleet", "maintenance"):
        import asyncio
        import json
        import os

        from remote_fleet_agent.config import FleetConfig
        from remote_fleet_agent.runtime import FleetRuntime

        key = args.key_path or Path(os.environ.get("REMOTE_FLEET_AGENT_KEY_PATH", ""))
        config = FleetConfig.load(args.config)
        if (
            not config.simulation
            and not (args.command == "maintenance" and args.mode == "show")
            and not key.is_file()
        ):
            parser.error(
                "--key-path or REMOTE_FLEET_AGENT_KEY_PATH must identify an existing private key"
            )
        if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
            from dataclasses import replace

            config = replace(config, maintenance_actions=())
        if args.command == "maintenance":
            if args.mode == "preview":
                if not args.host or not args.action or args.plan_id or args.digest:
                    parser.error(
                        "preview needs --host and --action, without --plan-id or --digest"
                    )
            elif (
                not args.plan_id
                or args.host
                or args.action
                or (args.mode != "show" and not args.digest)
            ):
                parser.error(
                    "show needs --plan-id; execute/rollback need --plan-id and --digest; no host/action overrides"
                )
        job_dir = args.job_dir or Path.home() / ".remote-fleet-agent" / "jobs"

        async def run():
            runtime = FleetRuntime(config, str(key), job_dir)
            try:
                if args.command == "maintenance":
                    if args.mode == "preview":
                        result = await runtime.maintenance.preview(
                            args.host, args.action
                        )
                    elif args.mode == "show":
                        result = runtime.maintenance.get(args.plan_id)
                    else:
                        result = await runtime.maintenance.execute(
                            args.plan_id, args.digest, rollback=args.mode == "rollback"
                        )
                    print(json.dumps(result, indent=2))
                    print("Change record:", runtime.maintenance._path(result["id"]))
                    return (
                        0
                        if result["status"] in ("preview", "complete", "rolled_back")
                        else 2
                    )
                if args.retry_job:
                    if args.hosts or args.group or args.sections:
                        parser.error(
                            "--retry-job uses its original scope and sections; don't supply overrides"
                        )
                    summary = runtime.retry(args.retry_job)
                else:
                    summary = runtime.start(
                        args.operation, args.hosts, args.group, args.sections
                    )
                print(
                    json.dumps(
                        {
                            "job_id": summary["id"],
                            "hosts": list(summary["hosts"]),
                            "status": summary["status"],
                        }
                    ),
                    flush=True,
                )
                result = await runtime.jobs.wait(summary["id"])
                print(json.dumps(runtime.jobs.summary(result), indent=2))
                print("Evidence saved:", runtime.jobs._file(summary["id"]))
                return 0 if result["status"] == "complete" else 2
            finally:
                await runtime.close()

        raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
