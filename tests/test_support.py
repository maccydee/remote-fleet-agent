import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.storage import read_json, save_json
from remote_fleet_agent.support import run_bounded


@pytest.fixture
async def support(support_env, support_technician_credentials):
    # Preserve the five-item tuple used by the existing support suite.
    yield (*support_env, support_technician_credentials["token"])


async def finish(service):
    while service.support.tasks:
        await asyncio.gather(*list(service.support.tasks))
    assert not service.support.failures


async def investigated(s, actor, profile="printing"):
    case = s.support.create(
        actor,
        "northstar-demo",
        {"host": "windows-01", "symptom": "Printer does not work", "profile": profile},
    )
    s.support.investigate(
        actor, "northstar-demo", case["id"], {"revision": case["revision"]}
    )
    await finish(s)
    return s.support.get(actor, "northstar-demo", case["id"])


def proposal(case):
    return {
        "revision": case["revision"],
        "title": "Restart spooler",
        "rationale": "Investigated queue requires service review; driver cause remains uncertain",
        "impact": "Printing pauses briefly; applies only to this device",
        "script": "Restart-Service -Name 'Spooler' -ErrorAction Stop",
        "verification_script": "$s=Get-Service Spooler; [pscustomobject]@{passed=($s.Status -eq 'Running');detail=$s.Status.ToString()} | ConvertTo-Json -Compress",
        "recovery": "No undo for interrupted jobs; inspect queue if checks fail",
        "timeout_seconds": 60,
    }


def proposed(s, actor, case):
    return s.support.propose(actor, "northstar-demo", case["id"], proposal(case))


def decision(s, actor, case, approve=True):
    return s.support.decide(
        actor,
        "northstar-demo",
        case["id"],
        {"revision": case["revision"], "digest": case["repair"]["digest"]},
        approve,
    )


def execute(s, actor, case):
    return s.support.execute(
        actor,
        "northstar-demo",
        case["id"],
        {"revision": case["revision"], "digest": case["repair"]["digest"]},
    )


async def test_complete_simulated_workflow_has_separate_engineer_approval(support):
    s, owner, tech, _, _ = support
    case = await investigated(s, tech)
    assert case["state"] == "investigated" and case["evidence"]["simulation"]
    case = proposed(s, tech, case)
    with pytest.raises(Denied):
        decision(s, tech, case)
    with pytest.raises(Denied):
        execute(s, tech, case)
    case = decision(s, owner, case)
    assert case["repair"]["approval"]["actor"] == owner["id"]
    case = execute(s, tech, case)
    assert case["state"] == "executing"
    with pytest.raises(Conflict):
        execute(s, tech, case)
    await finish(s)
    case = s.support.get(tech, "northstar-demo", case["id"])
    assert case["state"] == "checks_passed" and case["result"]["simulation"]
    assert "NOT run" in case["result"]["execution"]["stdout"]
    case = s.support.confirm(
        owner,
        "northstar-demo",
        case["id"],
        {
            "revision": case["revision"],
            "resolved": True,
            "note": "Fictional user confirmation only",
        },
    )
    assert case["state"] == "resolved" and case["resolution"]["operator_recorded"]
    resolution = case["resolution"]
    next_plan = proposed(s, tech, case)
    assert next_plan["repair_history"][-1]["resolution"] == resolution
    case = next_plan
    with pytest.raises((Denied, Conflict)):
        execute(s, tech, case)


async def test_scope_and_roles_cannot_read_or_approve_other_sites(support):
    s, owner, tech, _, _ = support
    case = await investigated(s, tech)
    outsider, _ = s.store.add_operator(
        "Other site", "manager", {"northstar-demo": ["warehouse"]}, owner
    )
    observer, _ = s.store.add_operator(
        "Read only", "observer", {"northstar-demo": "*"}, owner
    )
    with pytest.raises(Denied):
        s.support.get(outsider, "northstar-demo", case["id"])
    assert s.support.list(outsider, "northstar-demo") == []
    with pytest.raises(Denied):
        s.support.investigate(
            observer, "northstar-demo", case["id"], {"revision": case["revision"]}
        )
    with pytest.raises(FileNotFoundError):
        s.support.get(owner, "harbour-demo", case["id"])
    with pytest.raises(Denied):
        s.support.create(
            tech, "northstar-demo", {"host": "linux-04", "symptom": "Out of scope"}
        )


async def test_digest_policy_expiry_and_approval_revocation(support):
    s, owner, tech, directory, _ = support
    case = proposed(s, tech, await investigated(s, tech))
    with pytest.raises(Conflict):
        s.support.decide(
            owner,
            "northstar-demo",
            case["id"],
            {"revision": case["revision"], "digest": "wrong"},
            True,
        )
    approved = decision(s, owner, case)
    rejected = decision(s, owner, approved, False)
    with pytest.raises(Denied):
        execute(s, tech, rejected)
    case = proposed(s, tech, rejected)
    case["repair"]["payload"]["expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    s.support.save(owner, "northstar-demo", case, {"repair": case["repair"]})
    changed = s.support.get(tech, "northstar-demo", case["id"])
    with pytest.raises(Conflict):
        decision(s, owner, changed)
    case = proposed(s, tech, changed)
    case = decision(s, owner, case)
    raw = read_json(directory / "northstar-demo/fleet.json")
    raw["query_timeout_seconds"] = 31
    save_json(directory / "northstar-demo/fleet.json", raw)
    with pytest.raises(Conflict):
        execute(s, tech, case)


async def test_changed_script_cannot_keep_approval_and_approver_must_remain_enabled(
    support,
):
    s, owner, tech, _, _ = support
    case = proposed(s, tech, await investigated(s, tech))
    manager, _ = s.store.add_operator(
        "Approving engineer", "manager", {"northstar-demo": ["head-office"]}, owner
    )
    case = decision(s, manager, case)
    s.store.db.execute("UPDATE operators SET enabled=0 WHERE id=?", (manager["id"],))
    s.store.db.commit()
    with pytest.raises(Denied):
        execute(s, tech, case)
    new = proposed(s, tech, case)
    assert new["state"] == "awaiting_approval" and new["repair"]["approval"] is None
    assert new["repair"]["digest"] != case["repair"]["digest"]
    assert new["repair_history"][0]["repair"]["approval"]["actor"] == manager["id"]
    with pytest.raises(Conflict):
        s.support.propose(tech, "northstar-demo", case["id"], proposal(case))


async def test_audit_only_blocks_administration_but_preserves_investigation(
    support, monkeypatch
):
    s, _, tech, _, _ = support
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "1")
    case = await investigated(s, tech, "outlook")
    with pytest.raises(Denied):
        proposed(s, tech, case)


async def test_case_claim_is_atomic_across_controllers(support):
    s, owner, tech, directory, _ = support
    case = decision(s, owner, proposed(s, tech, await investigated(s, tech)))
    second = LiveService(directory)
    try:
        execute(s, tech, case)
        with pytest.raises(Conflict):
            execute(second, tech, case)
        await finish(s)
    finally:
        await second.close()


async def real_case(s, owner, tech, directory, monkeypatch):
    case = await investigated(s, tech)
    path = directory / "northstar-demo/fleet.json"
    raw = read_json(path)
    raw.update(simulation=False, administration_enabled=True)
    raw["hosts"] = [h for h in raw["hosts"] if h["name"] == "windows-01"]
    for h in raw["hosts"]:
        h["scenario"] = "healthy"
    known = directory / "known"
    known.write_text("fixture-host-key")
    raw["known_hosts"] = str(known)
    save_json(path, raw)
    registry = read_json(directory / "workspace.json")
    key = directory / "test-key"
    key.write_text("not-a-real-key")
    for item in registry["clients"]:
        if Path(item["config"]) == path:
            item.update(
                key_path=str(key), authorisation_reference="Unit test fixture only"
            )
    save_json(directory / "workspace.json", registry)

    async def connection(*args):
        return object()

    monkeypatch.setattr(
        "remote_fleet_agent.runtime.FleetRuntime.connection", connection
    )
    return decision(s, owner, proposed(s, tech, case))


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("good", "checks_passed"),
        ("bad", "verification_failed"),
        ("malformed", "verification_failed"),
        ("command_failed", "execution_failed"),
        ("timeout", "unknown"),
    ],
)
async def test_real_transport_outcomes_never_imply_user_resolution(
    support, monkeypatch, mode, expected
):
    s, owner, tech, directory, _ = support
    case = await real_case(s, owner, tech, directory, monkeypatch)
    calls = []

    async def runner(conn, platform, script, timeout):
        calls.append(script)
        assert s.support.get(tech, "northstar-demo", case["id"])["state"] == "executing"
        if mode == "timeout":
            raise TimeoutError("Outcome cannot be established")
        verify = len(calls) == 2
        return {
            "exit_code": 5 if mode == "command_failed" else 0,
            "stdout": (
                "not-json"
                if mode == "malformed"
                else json.dumps({"passed": mode == "good", "detail": "fixture check"})
            )
            if verify
            else "repair output",
            "stderr": "",
            "truncated": False,
        }

    monkeypatch.setattr("remote_fleet_agent.support.run_bounded", runner)
    execute(s, tech, case)
    await finish(s)
    result = s.support.get(tech, "northstar-demo", case["id"])
    assert result["state"] == expected
    assert len(calls) == (1 if mode in ("timeout", "command_failed") else 2)
    assert result.get("resolution") is None
    if mode == "timeout":
        with pytest.raises(ValueError):
            proposed(s, tech, result)


async def test_bounded_output_drains_streams_and_marks_truncation():
    class Stream:
        def __init__(self, text):
            self.text = text

        async def read(self, n):
            chunk, self.text = self.text[:n], self.text[n:]
            return chunk

    class Input:
        def write(self, v):
            pass

        def write_eof(self):
            pass

    class Process:
        stdout = Stream("a" * 9000)
        stderr = Stream("b" * 9000)
        stdin = Input()
        exit_status = 0
        closed = False

        async def wait_closed(self):
            pass

        def close(self):
            self.closed = True

    class Connection:
        process = Process()

        async def create_process(self, command):
            return self.process

    conn = Connection()
    result = await run_bounded(conn, "linux", "printf test", limit=100)
    assert len(result["stdout"]) == len(result["stderr"]) == 100
    assert result["truncated"] and conn.process.closed


async def test_cancellation_keeps_uncertain_intent_and_prior_results(
    support, monkeypatch
):
    s, owner, tech, directory, _ = support
    case = await real_case(s, owner, tech, directory, monkeypatch)
    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("remote_fleet_agent.support.run_bounded", blocked)
    execute(s, tech, case)
    await entered.wait()
    second = LiveService(directory)
    try:
        saved = second.support.get(tech, "northstar-demo", case["id"])
        assert saved["state"] == "executing"
        with pytest.raises(Conflict):
            execute(second, tech, saved)
        await s.support.close()
        case = s.support.get(tech, "northstar-demo", case["id"])
        assert case["state"] == "unknown"
        with pytest.raises(ValueError):
            proposed(s, tech, case)
    finally:
        await second.close()


async def test_storage_cas_rejects_a_stale_writer_and_reinvestigation_archives(support):
    s, owner, tech, directory, _ = support
    case = decision(s, owner, proposed(s, tech, await investigated(s, tech)))
    second = LiveService(directory)
    try:
        before = second.support.get(tech, "northstar-demo", case["id"])
        case = s.support.save(owner, "northstar-demo", case, {"state": "rejected"})
        with pytest.raises(Conflict):
            second.support.save(owner, "northstar-demo", before, {"state": "approved"})
        case = s.support.investigate(
            tech, "northstar-demo", case["id"], {"revision": case["revision"]}
        )
        await finish(s)
        case = s.support.get(tech, "northstar-demo", case["id"])
        assert len(case["repair_history"]) == len(case["investigation_history"]) == 1
        assert case["repair_history"][0]["state"] == "rejected"
        assert case["repair"] == {} and case["state"] == "investigated"
    finally:
        await second.close()


async def test_real_administration_requires_its_own_opt_in(support, monkeypatch):
    s, owner, tech, directory, _ = support
    case = await real_case(s, owner, tech, directory, monkeypatch)
    path = directory / "northstar-demo/fleet.json"
    raw = read_json(path)
    raw["administration_enabled"] = False
    save_json(path, raw)
    with pytest.raises(Denied):
        proposed(s, tech, case)
    raw["administration_enabled"] = "true"
    save_json(path, raw)
    with pytest.raises(ValueError, match="boolean"):
        s.config("northstar-demo")


async def test_stale_evidence_requires_new_investigation(support):
    s, _, tech, _, _ = support
    case = await investigated(s, tech)
    evidence = dict(case["evidence"])
    evidence["collected_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    case = s.support.save(tech, "northstar-demo", case, {"evidence": evidence})
    with pytest.raises(Conflict, match="stale"):
        proposed(s, tech, case)


def test_windows_investigation_scripts_parse_without_running_cmdlets():
    import shutil
    import subprocess

    from remote_fleet_agent.ssh_client import build_encoded_command, ps_quote
    from remote_fleet_agent.support import WINDOWS_PROFILES

    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable for syntax validation")
    for queries in WINDOWS_PROFILES.values():
        for name, script in queries.items():
            parse = (
                "$tokens=$null;$errors=$null;$null=[System.Management.Automation.Language.Parser]::ParseInput("
                + ps_quote(script)
                + ",[ref]$tokens,[ref]$errors); if($errors.Count){$errors | ConvertTo-Json -Compress;exit 1}"
            )
            encoded = build_encoded_command(parse).split()[-1]
            result = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            assert result.returncode == 0, (name, result.stdout, result.stderr)


async def test_actual_ssh_stream_transport_without_executing_any_script(tmp_path):
    import base64

    import asyncssh

    received = []

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return False

    async def handle(process):
        payload = await process.stdin.read()
        received.append((process.command, payload))
        process.stdout.write(
            '{"passed":true,"detail":"transport fixture; no shell executed"}'
        )
        process.stderr.write("fixture stderr")
        process.exit(0)

    key = asyncssh.generate_private_key("ssh-ed25519")
    listener = await asyncssh.create_server(
        Server, "127.0.0.1", 0, server_host_keys=[key], process_factory=handle
    )
    port = listener.get_port()
    known = tmp_path / "known-hosts"
    known.write_text(
        f"[127.0.0.1]:{port} " + key.export_public_key().decode().strip() + "\n"
    )
    try:
        async with asyncssh.connect(
            "127.0.0.1", port=port, username="transport-fixture", known_hosts=str(known)
        ) as conn:
            for platform in ("windows", "linux", "macos"):
                result = await run_bounded(conn, platform, "fixture payload only")
                assert (
                    result["exit_code"] == 0 and json.loads(result["stdout"])["passed"]
                )
                assert result["stderr"] == "fixture stderr"
        assert received[1:] == [("sh -s", "fixture payload only\n")] * 2
        command, stdin = received[0]
        assert stdin == "" and "EncodedCommand" in command
        assert (
            base64.b64decode(command.split()[-1]).decode("utf-16-le")
            == "fixture payload only"
        )
    finally:
        listener.close()
        await listener.wait_closed()
