"""Opt-in native enrollment preflight and guarded scratch plan generator.

This module never approves a repair or bypasses the controller execution service.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from remote_fleet_agent.config import FleetConfig


class PrerequisiteError(ValueError):
    """A native test cannot safely start with its current prerequisites."""


def load_manifest(path: Path) -> dict[str, Any]:
    """Read only an explicitly selected private manifest, never discover one."""
    if not path.is_file():
        raise PrerequisiteError("Native manifest does not exist")
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or not isinstance(data.get("targets"), list):
        raise PrerequisiteError("Manifest requires schema 1 and targets")
    if not data["targets"]:
        raise PrerequisiteError("Manifest has no enrolled targets")
    workspace = Path(data.get("workspace", "")).expanduser()
    if not workspace.is_absolute() or not (workspace / "workspace.json").is_file():
        raise PrerequisiteError("Manifest requires an existing absolute workspace")
    identities: set[tuple[str, str]] = set()
    for target in data["targets"]:
        required = ("client", "device", "platform", "account", "address", "fingerprint",
                    "scratch_parent", "authorisation_reference", "expires_at")
        if any(not isinstance(target.get(k), str) or not target[k].strip() for k in required):
            raise PrerequisiteError("Target lacks identity, scope, scratch or expiry fields")
        if target["platform"] not in ("windows", "macos", "linux"):
            raise PrerequisiteError("Unsupported native target platform")
        if type(target.get("port")) is not int or not 1 <= target["port"] <= 65535:
            raise PrerequisiteError("Target requires a valid explicit SSH port")
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", target["fingerprint"]):
            raise PrerequisiteError("Target requires an explicit SHA256 host-key fingerprint")
        expiry = datetime.fromisoformat(target["expires_at"])
        if expiry.tzinfo is None or expiry <= datetime.now(UTC):
            raise PrerequisiteError("Target authorisation is expired or has no timezone")
        identity = (target["client"], target["device"])
        if identity in identities:
            raise PrerequisiteError("Duplicate native target")
        identities.add(identity)
        parent = (PureWindowsPath if target["platform"] == "windows" else PurePosixPath)(target["scratch_parent"])
        if not parent.is_absolute() or ".." in parent.parts or len(parent.parts) < 2:
            raise PrerequisiteError("Scratch parent must be an explicit account temp directory")
    return data


def enrolled_target(manifest: dict[str, Any], target: dict[str, Any]) -> tuple[FleetConfig, dict[str, Any]]:
    """Match every identity field and current authority against enrollment."""
    workspace = Path(manifest["workspace"]).expanduser().resolve()
    registry = json.loads((workspace / "workspace.json").read_text())
    entries = [e for e in registry.get("clients", []) if FleetConfig.load(e["config"]).client == target["client"]]
    if len(entries) != 1:
        raise PrerequisiteError("Target client is not uniquely enrolled")
    entry = entries[0]
    config = FleetConfig.load(entry["config"])
    if config.simulation:
        raise PrerequisiteError("Simulation cannot provide native evidence")
    if not config.administration_enabled or not entry.get("allow_collection"):
        raise PrerequisiteError("Native administration/collection is not enabled")
    if entry.get("authorisation_reference") != target["authorisation_reference"]:
        raise PrerequisiteError("Authorisation reference differs from enrollment")
    if entry.get("access_expires_at") != target["expires_at"]:
        raise PrerequisiteError("Expiry policy differs from enrollment; do not extend it")
    try:
        host = config.resolve(target["device"])
    except ValueError as exc:
        raise PrerequisiteError("Target device is not enrolled") from exc
    if (host.target, host.port, host.platform) != (target["account"] + "@" + target["address"], target["port"], target["platform"]):
        raise PrerequisiteError("Manifest target identity differs from enrollment")
    if not Path(entry.get("key_path", "")).is_file():
        raise PrerequisiteError("Enrolled SSH key is unavailable")
    return config, entry


async def preflight(manifest: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Perform pinned SSH authentication without executing endpoint commands."""
    import asyncssh
    config, entry = enrolled_target(manifest, target)
    started = datetime.now(UTC)
    async with asyncssh.connect(target["address"], username=target["account"], port=target["port"],
                                client_keys=[entry["key_path"]], known_hosts=str(config.known_hosts),
                                connect_timeout=10, login_timeout=10) as connection:
        key = connection.get_server_host_key()
        actual = key.get_fingerprint("sha256") if key else None
        if actual != target["fingerprint"]:
            raise PrerequisiteError("Observed host-key fingerprint differs from manifest")
    return {"status": "PASS", "phase": "preflight", "client": target["client"],
            "device": target["device"], "platform": target["platform"], "account": target["account"],
            "expected_fingerprint": target["fingerprint"], "observed_fingerprint": actual,
            "simulation": False, "started_at": started.isoformat(),
            "elapsed_seconds": (datetime.now(UTC) - started).total_seconds(),
            "limitations": ["SSH identity only; endpoint OS/context and repair not yet tested"]}


def scratch_scripts(target: dict[str, Any], run_id: str, action: str) -> dict[str, str]:
    """Generate exact reviewable scripts; each later write validates its sentinel."""
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise PrerequisiteError("Run id must be a random UUID hex")
    if action not in {"prepare", "repair", "recover", "cleanup"}:
        raise PrerequisiteError("Unknown scratch action")
    parent = target["scratch_parent"]
    if target["platform"] == "windows":
        cls = PureWindowsPath
    else:
        cls = PurePosixPath
    p = cls(parent)
    if not p.is_absolute() or ".." in p.parts or len(p.parts) < 2:
        raise PrerequisiteError("Unsafe scratch parent")
    scratch = str(p / ("remote-fleet-support-" + run_id))
    if target["platform"] == "windows":
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        prefix = ("$ErrorActionPreference='Stop'\n$p=" + quote(scratch) + "\n$run=" + quote(run_id) +
                  "\n$s=Join-Path $p 'sentinel'\n$m=Join-Path $p 'marker'\n")
        prefix += ("$ancestor=Get-Item -LiteralPath " + quote(parent) + " -ErrorAction Stop\n"
                   "if(!$ancestor.PSIsContainer){throw 'Scratch parent is not a directory'}\n"
                   "while($null -ne $ancestor){if($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Scratch ancestor reparse point'};$ancestor=$ancestor.Parent}\n")
        guard = ("if(!(Test-Path -LiteralPath $p -PathType Container)){throw 'Missing scratch'}\n"
                 "if((Get-Item -LiteralPath $p).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Reparse point'}\n"
                 "if(!(Test-Path -LiteralPath $s -PathType Leaf) -or (Get-Content -LiteralPath $s -Raw) -cne $run){throw 'Sentinel mismatch'}\n"
                 "if((Get-Item -LiteralPath $s).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Sentinel reparse point'}\n"
                 "if((Test-Path -LiteralPath $m) -and ((Get-Item -LiteralPath $m).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Marker reparse point'}\n")
        if action == "prepare":
            script = prefix + ("if(Test-Path -LiteralPath $p){throw 'Scratch already exists'}\n"
                     "New-Item -ItemType Directory -Path $p -ErrorAction Stop | Out-Null\n"
                     "Set-Content -LiteralPath $s -Value $run -NoNewline\nSet-Content -LiteralPath $m -Value 'broken' -NoNewline\n")
            expected = "broken"
        elif action == "cleanup":
            script = prefix + guard + ("$items=@(Get-ChildItem -LiteralPath $p -Force)\n"
                     "if(@($items | Where-Object {$_.Name -notin @('sentinel','marker')}).Count){throw 'Unexpected scratch contents'}\n"
                     "Remove-Item -LiteralPath $m -ErrorAction Stop\nRemove-Item -LiteralPath $s -ErrorAction Stop\n"
                     "Remove-Item -LiteralPath $p -ErrorAction Stop\n")
            expected = None
        else:
            expected = "healthy" if action == "repair" else "broken"
            script = prefix + guard + "Set-Content -LiteralPath $m -Value " + quote(expected) + " -NoNewline\n"
        if expected is None:
            check = prefix + "@{passed=(!(Test-Path -LiteralPath $p));check_passed=(!(Test-Path -LiteralPath $p));cleanup_verified=(!(Test-Path -LiteralPath $p))} | ConvertTo-Json -Compress\n"
        else:
            check = prefix + guard + "@{passed=((Get-Content -LiteralPath $m -Raw) -ceq " + quote(expected) + ");check_passed=((Get-Content -LiteralPath $m -Raw) -ceq " + quote(expected) + ");content=[IO.File]::ReadAllText($m)} | ConvertTo-Json -Compress\n"
    else:
        # Python performs exact-child and no-symlink validation. No shell-interpolated paths.
        data = json.dumps({"parent": parent, "path": scratch, "run": run_id, "action": action})
        setup = "import json,os,pathlib,stat\nc=json.loads(" + repr(data) + ")\np=pathlib.Path(c['path']);parent=pathlib.Path(c['parent'])\nif not (parent.resolve()==parent and p.parent==parent and p.name=='remote-fleet-support-'+c['run']): raise RuntimeError('Unsafe scratch parent')\ns=p/'sentinel';m=p/'marker'\n"
        guard = "if not (p.is_dir() and not p.is_symlink() and s.is_file() and not s.is_symlink() and s.read_text()==c['run']): raise RuntimeError('Sentinel mismatch')\nif m.is_symlink(): raise RuntimeError('Marker symlink')\n"
        if action == "prepare":
            body = "p.mkdir(mode=0o700)\ns.write_text(c['run']);m.write_text('broken')\n"
            expected = "broken"
        elif action == "cleanup":
            body = guard + "if {x.name for x in p.iterdir()}!={'sentinel','marker'}: raise RuntimeError('Unexpected scratch contents')\nm.unlink();s.unlink();p.rmdir()\n"
            expected = None
        else:
            expected = "healthy" if action == "repair" else "broken"
            body = guard + "m.write_text(" + repr(expected) + ")\n"
        script = "python3 - <<'REMOTE_FLEET_NATIVE'\n" + setup + body + "REMOTE_FLEET_NATIVE\n"
        check_body = ("print(json.dumps({'passed':not p.exists(),'check_passed':not p.exists(),'cleanup_verified':not p.exists()}))\n" if expected is None else guard + "v=m.read_text();print(json.dumps({'passed':v==" + repr(expected) + ",'check_passed':v==" + repr(expected) + ",'content':v}))\n")
        check = "python3 - <<'REMOTE_FLEET_NATIVE'\n" + setup + check_body + "REMOTE_FLEET_NATIVE\n"
    return {"script": script, "verification_script": check, "scratch_path": scratch,
            "script_digest": hashlib.sha256(script.encode()).hexdigest()}


def source_identity() -> dict[str, str | None]:
    """Hash the actual tracked and untracked source, without embedding its data."""
    root = Path(__file__).resolve().parents[1]
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=False)
    diff = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=root,
                          capture_output=True, check=False)
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                               cwd=root, capture_output=True, check=False)
    hasher = hashlib.sha256(diff.stdout)
    for raw in sorted(untracked.stdout.split(b"\0")):
        if raw:
            path = root / os.fsdecode(raw)
            if path.is_file() and not path.is_symlink():
                hasher.update(raw + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {"revision": revision.stdout.strip() if revision.returncode == 0 else None,
            "diff_hash": hasher.hexdigest() if diff.returncode == 0 else None}


def write_report(directory: Path, report: dict[str, Any]) -> Path:
    """Create private evidence without replacing a previous run."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / (uuid.uuid4().hex + ".json")
    with path.open("x") as stream:
        os.chmod(path, 0o600)
        json.dump(report, stream, indent=2)
    return path


def validate_controller_target(target: dict[str, Any], snapshots: list[dict[str, Any]]) -> None:
    """Check the controller's endpoint against private native enrollment."""
    matching = [item for item in snapshots if item.get("name") == target["device"]]
    if len(matching) != 1:
        raise PrerequisiteError("Controller session does not name exactly this enrolled device")
    observed = matching[0]
    expected = (target["account"] + "@" + target["address"], target["port"], target["platform"])
    if (observed.get("target"), observed.get("port"), observed.get("platform")) != expected:
        raise PrerequisiteError("Controller target differs from private manifest")
    if observed.get("simulation") is not False or target["fingerprint"] not in observed.get("host_keys", []):
        raise PrerequisiteError("Controller target is simulated or lacks the expected pinned identity")


class NativeLab:
    """Real technician API adapter with separate, never automated human decisions."""

    def __init__(self, manifest: dict[str, Any], target: dict[str, Any],
                 *, output: Path, approval_wait_seconds: float = 0) -> None:
        from remote_fleet_agent.support_mcp import WorkspaceClient
        if not manifest.get("workspace_url") or not manifest.get("operator_token_file"):
            raise PrerequisiteError("Native workflow requires workspace_url and a private technician operator_token_file")
        self.manifest, self.target, self.output = manifest, target, output
        self.client = WorkspaceClient(manifest["workspace_url"], manifest["operator_token_file"])
        self.base = "/api/clients/" + quote(target["client"], safe="")
        self.approval_wait_seconds = min(max(approval_wait_seconds, 0), 300)
        self.session: dict[str, Any] | None = None
        self.run_id = uuid.uuid4().hex
        self.records: list[dict[str, Any]] = []

    async def call(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.client.call, self.base + path, body)

    def review_link(self, *, session: str | None = None, plan: str | None = None) -> str:
        # Canonical UI routes may expand; identity is always explicit in the link.
        from urllib.parse import urlencode
        return self.manifest["workspace_url"].rstrip("/") + "/?" + urlencode({
            "client": self.target["client"], "page": "approvals",
            **({"investigation": session} if session else {}),
            **({"repair_plan": plan} if plan else {})})

    async def _human_gate(self, route: str, record: dict[str, Any], state: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.approval_wait_seconds
        while record["state"] != state:
            if record["state"] in {"rejected", "cancelled", "closed", "unknown"}:
                raise PrerequisiteError("Engineer decision or unknown outcome prevents native execution")
            if time.monotonic() >= deadline:
                raise PrerequisiteError("Separate human decision pending; review " + self.review_link(
                    session=record["id"] if "investigations" in route else None,
                    plan=record["id"] if "repair-plans" in route else None))
            await asyncio.sleep(1)
            record = await self.call(route + "/" + record["id"])
        if not record.get("approval"):
            raise PrerequisiteError("Controller state lacks recorded authenticated human approval")
        return record

    async def confirmed_diagnostics(self, session_id: str | None = None) -> dict[str, Any]:
        await preflight(self.manifest, self.target)
        if session_id:
            session = await self.call("/investigations/" + quote(session_id, safe=""))
        else:
            session = await self.call("/investigations", {"targets": [self.target["device"]]})
        validate_controller_target(self.target, session.get("targets", []))
        self.session = session
        session = await self._human_gate("/investigations", session, "active")
        validate_controller_target(self.target, session.get("targets", []))
        self.session = session
        if self.target["platform"] == "windows":
            script = ("$ErrorActionPreference='Stop'\n@{platform='windows';kernel='Windows';account=[Security.Principal.WindowsIdentity]::GetCurrent().Name;"
                      "device=$env:COMPUTERNAME;temp=[IO.Path]::GetTempPath();powershell=$PSVersionTable.PSVersion.ToString();"
                      "free_bytes=(Get-PSDrive -Name C).Free;sshd=(Get-Service sshd).Status.ToString()} | ConvertTo-Json -Compress")
        else:
            script = "python3 - <<'RF_CONTEXT'\nimport json,os,platform,tempfile,shutil,pwd\nprint(json.dumps({'platform':'macos' if platform.system()=='Darwin' else 'linux','kernel':platform.system(),'account':pwd.getpwuid(os.getuid()).pw_name,'temp':os.path.realpath(tempfile.gettempdir()),'free_bytes':shutil.disk_usage(tempfile.gettempdir()).free}))\nRF_CONTEXT\n"
        result = await self.call("/investigations/" + session["id"] + "/run", {
            "host": self.target["device"], "script": script, "timeout_seconds": 30})
        if result.get("outcome") != "known" or result.get("simulation") is not False or result.get("truncated") or result.get("exit_code") != 0:
            raise PrerequisiteError("Native diagnostic did not establish real bounded execution context")
        observed = json.loads(result["stdout"].lstrip("\ufeff"))
        if observed.get("platform") != self.target["platform"]:
            raise PrerequisiteError("Observed endpoint platform differs from manifest")
        # Scratch directories must lie in the actual account temp area. Linux
        # Docker /tmp is accepted only when the diagnostic reports it as temp.
        cls = PureWindowsPath if self.target["platform"] == "windows" else PurePosixPath
        parent, actual_temp = cls(self.target["scratch_parent"]), cls(observed["temp"])
        if parent != actual_temp and actual_temp not in parent.parents:
            raise PrerequisiteError("Scratch parent lies outside actual account temp")
        observed_account = observed.get("account", "")
        expected_account = self.target["account"]
        if self.target["platform"] == "windows":
            observed_account = observed_account.rsplit("\\", 1)[-1].casefold()
            expected_account = expected_account.rsplit("\\", 1)[-1].casefold()
        if observed_account != expected_account:
            raise PrerequisiteError("Observed effective account differs from manifest")
        observed.update(simulation=False, diagnostic=result)
        self.records.append({"phase": "diagnostics", "result": observed})
        return observed

    async def propose_scratch_action(self, action: str) -> dict[str, Any]:
        if not self.session or self.session["state"] != "active":
            raise PrerequisiteError("Confirmed native diagnostic session required")
        scripts = scratch_scripts(self.target, self.run_id, action)
        host, platform = self.target["device"], self.target["platform"]
        now = datetime.now(UTC)
        plan = {"schema": 1, "title": "Native scratch " + action,
                "rationale": "Validate only the dedicated sentinel-bound native fixture",
                "uncertainty": "Native SSH context differs from the desktop; user outcome remains pending",
                "impact": "Changes only " + scripts["scratch_path"], "targets": [host],
                "window": {"start": now.isoformat(), "end": (now + timedelta(minutes=10)).isoformat(), "timezone": "UTC"},
                "disruption": "now", "trial": host, "batch_size": 1,
                "pause_after_trial": False, "stop_after_failures": 1,
                "steps": [{"id": action, "kind": "change", "targets": [host], "platform": platform,
                           "script": scripts["script"], "timeout_seconds": 30},
                          {"id": action + "-check", "kind": "check", "targets": [host], "platform": platform,
                           "script": scripts["verification_script"], "timeout_seconds": 30}]}
        record = await self.call("/repair-plans", plan)
        validate_controller_target(self.target, record.get("targets", []))
        self.records.append({"phase": action, "plan_id": record["id"], "digest": record["digest"],
                             "scratch_path": scripts["scratch_path"], "review_link": self.review_link(plan=record["id"])})
        return record

    async def execute(self, record: dict[str, Any]) -> dict[str, Any]:
        ident, approved_digest = record["id"], record["digest"]
        record = await self._human_gate("/repair-plans", record, "approved")
        if record["digest"] != approved_digest:
            raise PrerequisiteError("Plan changed during human review; original test will not execute")
        validate_controller_target(self.target, record.get("targets", []))
        result = await self.call("/repair-plans/" + ident + "/execute", {"revision": record["revision"], "digest": record["digest"]})
        deadline = time.monotonic() + 120
        while result["state"] in {"pending", "queued", "running", "executing", "approved", "scheduled"}:
            if time.monotonic() >= deadline:
                raise PrerequisiteError("Native plan completion not established; do not replay")
            await asyncio.sleep(0.5)
            result = await self.call("/repair-plans/" + ident)
        self.records.append({"phase": "execution", "plan_id": ident, "result": result})
        if result["state"] not in {"completed", "checks_passed"}:
            raise PrerequisiteError("Native scratch plan did not complete: " + result["state"])
        return result

    async def run_approved_scratch_cycle(self) -> dict[str, Any]:
        """Every phase needs its own external human decision; never fabricate one."""
        started = time.monotonic()
        report: dict[str, Any] = {"status": "BLOCKED", "run_id": self.run_id,
                                  "platform": self.target["platform"], "device": self.target["device"],
                                  "simulation": False, "user_outcome": "pending", "cleanup_verified": False,
                                  "source": source_identity()}
        try:
            await self.confirmed_diagnostics()
            for action in ("prepare", "repair", "recover", "cleanup"):
                record = await self.propose_scratch_action(action)
                await self.execute(record)
                report[action] = "PASS"
            report.update(status="PASS", cleanup_verified=True)
            return report
        except PrerequisiteError as exc:
            report["reason"] = str(exc)
            raise
        finally:
            report.update(records=self.records, elapsed_seconds=time.monotonic() - started)
            write_report(self.output, report)


def main() -> int:
    import asyncssh

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "prepare", "run", "recover", "cleanup"))
    parser.add_argument("--manifest", type=Path, default=os.environ.get("REMOTE_FLEET_NATIVE_MANIFEST"))
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--session-id", default=None, help="Existing separately engineer-confirmed investigation")
    parser.add_argument("--plan-id", default=None, help="Existing exact scratch plan to execute after human approval")
    parser.add_argument("--approval-wait-seconds", type=float, default=0)
    args = parser.parse_args()
    if not args.manifest:
        parser.error("Pass --manifest or set REMOTE_FLEET_NATIVE_MANIFEST")
    report: dict[str, Any] = {"status": "BLOCKED", "phase": args.phase}
    try:
        manifest = load_manifest(Path(args.manifest))
        matches = [t for t in manifest["targets"] if t["device"] == args.device]
        if len(matches) != 1:
            raise PrerequisiteError("Select exactly one manifest device")
        target = matches[0]
        report = asyncio.run(preflight(manifest, target))
        if args.phase != "preflight":
            run_id = args.run_id or (uuid.uuid4().hex if args.phase == "prepare" else None)
            if run_id is None:
                raise PrerequisiteError("Existing run id required for run, recovery or cleanup")
            action = {"run": "repair"}.get(args.phase, args.phase)
            scripts = scratch_scripts(target, run_id, action)
            report.update(status="BLOCKED", phase=args.phase, run_id=run_id, proposal=scripts,
                          source=source_identity())
            lab = NativeLab(manifest, target, output=args.output,
                            approval_wait_seconds=args.approval_wait_seconds)
            lab.run_id = run_id
            asyncio.run(lab.confirmed_diagnostics(args.session_id))
            report["session_id"] = lab.session["id"]
            if args.plan_id:
                record = asyncio.run(lab.call("/repair-plans/" + quote(args.plan_id, safe="")))
                steps = record.get("plan", {}).get("steps", [])
                if (record.get("plan", {}).get("targets") != [target["device"]] or len(steps) != 2
                        or steps[0].get("script") != scripts["script"]
                        or steps[1].get("script") != scripts["verification_script"]):
                    raise PrerequisiteError("Existing plan is not this exact native scratch phase")
                report["result"] = asyncio.run(lab.execute(record))
                report["status"] = "PASS"
                report["user_outcome"] = "pending"
            else:
                record = asyncio.run(lab.propose_scratch_action(action))
                report.update(plan_id=record["id"], digest=record["digest"],
                              review_link=lab.review_link(plan=record["id"]),
                              reason="Separate authenticated human approval required; resume with --plan-id and this run/session id")
    except (asyncssh.Error, ValueError, OSError, TimeoutError) as exc:
        report.update(status="BLOCKED", reason=str(exc), error_type=type(exc).__name__)
    path = write_report(args.output, report)
    print(json.dumps({"status": report["status"], "report": str(path)}))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
