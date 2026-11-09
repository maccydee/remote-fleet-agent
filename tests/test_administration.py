"""Cross-platform confirmation and immutable administration contracts."""

import copy
from datetime import UTC, datetime, timedelta

import pytest

from remote_fleet_agent.live_store import Conflict, Denied


def window():
    now = datetime.now(UTC)
    return {
        "start": (now - timedelta(minutes=1)).isoformat(),
        "end": (now + timedelta(minutes=15)).isoformat(),
        "timezone": "Europe/London",
    }


def plan_body(host="windows-01", platform="windows"):
    return {
        "schema": 1,
        "title": "Scratch repair",
        "rationale": "Evidence from dedicated fixture",
        "uncertainty": "User result pending",
        "impact": "Only dedicated fixture",
        "targets": [host],
        "window": window(),
        "disruption": "now",
        "trial": host,
        "batch_size": 1,
        "pause_after_trial": False,
        "stop_after_failures": 1,
        "steps": [
            {
                "id": "change",
                "kind": "change",
                "targets": [host],
                "platform": platform,
                "script": "echo healthy",
                "timeout_seconds": 10,
            },
            {
                "id": "verify",
                "kind": "check",
                "targets": [host],
                "platform": platform,
                "script": "echo verify",
                "timeout_seconds": 10,
            },
        ],
    }


async def test_investigation_requires_human_and_rechecks_target(support_env):
    s, owner, tech, directory = support_env
    session = s.investigations.request(
        tech, "northstar-demo", {"targets": ["windows-01"]}
    )
    assert session["state"] == "pending_target_confirmation"
    with pytest.raises(Denied):
        s.investigations.confirm(
            tech,
            "northstar-demo",
            session["id"],
            {"revision": session["revision"], "digest": session["digest"]},
        )
    session = s.investigations.confirm(
        owner,
        "northstar-demo",
        session["id"],
        {"revision": session["revision"], "digest": session["digest"]},
    )
    result = await s.investigations.run(
        tech,
        "northstar-demo",
        session["id"],
        {"host": "windows-01", "script": "never execute", "timeout_seconds": 10},
    )
    assert result["simulation"] and "NOT run" in result["stdout"]
    from remote_fleet_agent.storage import read_json, save_json

    p = directory / "northstar-demo/fleet.json"
    config = read_json(p)
    config["hosts"][0]["port"] = 2223
    save_json(p, config)
    with pytest.raises(Conflict):
        await s.investigations.run(
            tech,
            "northstar-demo",
            session["id"],
            {"host": "windows-01", "script": "echo test"},
        )


async def test_plan_approval_simulation_and_no_self_approval(support_env):
    s, owner, tech, _ = support_env
    record = s.repairs.propose(tech, "northstar-demo", plan_body())
    assert record["state"] == "awaiting_approval"
    decision = {"revision": record["revision"], "digest": record["digest"]}
    with pytest.raises(Denied):
        s.repairs.decide(tech, "northstar-demo", record["id"], decision, True)
    with pytest.raises(Denied):
        s.repairs.execute(tech, "northstar-demo", record["id"], decision)
    record = s.repairs.decide(owner, "northstar-demo", record["id"], decision, True)
    record = s.repairs.execute(
        tech,
        "northstar-demo",
        record["id"],
        {"revision": record["revision"], "digest": record["digest"]},
    )
    await s.repairs.wait()
    record = s.repairs.get(tech, "northstar-demo", record["id"])
    assert record["state"] == "checks_passed"
    assert record["results"][0]["simulation"]
    assert record["user_outcome"] == "pending"
    with pytest.raises((Denied, Conflict)):
        s.repairs.execute(
            tech,
            "northstar-demo",
            record["id"],
            {"revision": record["revision"], "digest": record["digest"]},
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p["steps"][0].update(timeout_seconds=True),
        lambda p: p["steps"][0].update(kind="arbitrary"),
        lambda p: p.update(extra="unreviewed"),
        lambda p: p["steps"][1].update(id="change"),
        lambda p: p["steps"][0].update(
            when={"step": "verify", "field": "passed", "equals": True}
        ),
        lambda p: p["steps"][0].update(platform="linux"),
        lambda p: p["window"].update(start="2026-11-08T12:00:00"),
    ],
)
async def test_invalid_executable_plan_rejected(support_env, mutator):
    s, _, tech, _ = support_env
    body = plan_body()
    mutator(body)
    with pytest.raises(ValueError):
        s.repairs.propose(tech, "northstar-demo", body)


async def test_mutated_plan_invalidates_approval(support_env):
    s, owner, tech, _ = support_env
    record = s.repairs.propose(tech, "northstar-demo", plan_body())
    record = s.repairs.decide(
        owner,
        "northstar-demo",
        record["id"],
        {"revision": record["revision"], "digest": record["digest"]},
        True,
    )
    altered = copy.deepcopy(record)
    altered["plan"]["steps"][0]["script"] += " changed"
    s.repairs.update(owner, "northstar-demo", altered, {"plan": altered["plan"]})
    record = s.repairs.get(tech, "northstar-demo", record["id"])
    with pytest.raises(Conflict):
        s.repairs.execute(
            tech,
            "northstar-demo",
            record["id"],
            {"revision": record["revision"], "digest": record["digest"]},
        )


def decision(record):
    return {"revision": record["revision"], "digest": record["digest"]}


def approved(s, owner, body):
    record = s.repairs.propose(owner, "northstar-demo", body)
    return s.repairs.decide(
        owner, "northstar-demo", record["id"], decision(record), True
    )


def linux_plan():
    p = plan_body("linux-03", "linux")
    p["targets"] = ["linux-03", "linux-04"]
    for step in p["steps"]:
        step["targets"] = list(p["targets"])
    return p


async def test_failed_canary_does_not_dispatch_other_target(support_env, monkeypatch):
    s, owner, _, _ = support_env
    calls = []

    async def dispatch(actor, client, ident, target, step):
        calls.append((target["name"], step["id"]))
        return {
            "exit_code": 0,
            "stdout": '{"passed":false}',
            "stderr": "",
            "truncated": False,
        }

    monkeypatch.setattr(s.repairs, "dispatch", dispatch)
    body = linux_plan()
    body["stop_after_failures"] = 2
    record = approved(s, owner, body)
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert {host for host, _ in calls} == {"linux-03"}
    assert s.repairs.get(owner, "northstar-demo", record["id"])["state"] == "paused"


async def test_preapproved_recovery_and_malformed_check_boundary(
    support_env, monkeypatch
):
    s, owner, _, _ = support_env
    p = plan_body()
    p["steps"].append(
        {
            "id": "recover",
            "kind": "recovery",
            "targets": ["windows-01"],
            "platform": "windows",
            "script": "echo restore",
            "timeout_seconds": 10,
            "when": {"step": "verify", "field": "passed", "equals": False},
        }
    )
    calls = []

    async def dispatch(actor, client, ident, target, step):
        calls.append(step["id"])
        return {
            "exit_code": 0,
            "stdout": '{"passed":false}',
            "stderr": "",
            "truncated": False,
        }

    monkeypatch.setattr(s.repairs, "dispatch", dispatch)
    record = approved(s, owner, p)
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert calls == ["change", "verify", "recover"]
    assert (
        s.repairs.get(owner, "northstar-demo", record["id"])["last_outcome"]
        == "recovered"
    )
    calls.clear()

    async def malformed(actor, client, ident, target, step):
        calls.append(step["id"])
        return {"exit_code": 0, "stdout": "bad JSON", "stderr": "", "truncated": False}

    monkeypatch.setattr(s.repairs, "dispatch", malformed)
    record = approved(s, owner, p)
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert calls == ["change", "verify"]


async def test_unknown_dispatch_retains_device_claim_no_replay(
    support_env, monkeypatch
):
    s, owner, _, _ = support_env
    calls = []

    async def disconnected(actor, client, ident, target, step):
        calls.append(step["id"])
        raise ConnectionError("lost after send")

    monkeypatch.setattr(s.repairs, "dispatch", disconnected)
    record = approved(s, owner, plan_body())
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    fresh = s.repairs.get(owner, "northstar-demo", record["id"])
    assert fresh["state"] == "unknown" and calls == ["change"]
    await s.repairs.tick()
    await s.repairs.wait()
    assert calls == ["change"]
    fresh = s.repairs.reconcile(
        owner,
        "northstar-demo",
        fresh["id"],
        {
            **decision(fresh),
            "note": "Fixture observation establishes stopped scratch process",
        },
    )
    assert (
        fresh["state"] == "reconciled" and fresh["results"][0]["outcome"] == "unknown"
    )


async def test_readiness_and_cancel_before_dispatch(support_env):
    s, owner, tech, _ = support_env
    p = plan_body()
    p["disruption"] = "readiness"
    record = approved(s, owner, p)
    s.repairs.execute(tech, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    record = s.repairs.get(owner, "northstar-demo", record["id"])
    assert record["state"] == "awaiting_readiness" and not record["results"]
    with pytest.raises(Denied):
        s.repairs.ready(tech, "northstar-demo", record["id"], decision(record))
    s.repairs.ready(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert (
        s.repairs.get(owner, "northstar-demo", record["id"])["state"] == "checks_passed"
    )
    record = approved(s, owner, plan_body())
    record = s.repairs.execute(tech, "northstar-demo", record["id"], decision(record))
    s.repairs.cancel(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert s.repairs.get(owner, "northstar-demo", record["id"])["results"] == []


async def test_future_window_then_expiry_never_dispatches(support_env):
    s, owner, _, _ = support_env
    p = plan_body()
    start = datetime.now(UTC) + timedelta(hours=1)
    p["window"].update(
        start=start.isoformat(), end=(start + timedelta(minutes=5)).isoformat()
    )
    record = approved(s, owner, p)
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    assert s.repairs.get(owner, "northstar-demo", record["id"])["state"] == "scheduled"
    s.repairs.clock = lambda: start + timedelta(minutes=6)
    await s.repairs.tick()
    await s.repairs.wait()
    record = s.repairs.get(owner, "northstar-demo", record["id"])
    assert record["state"] == "expired" and record["results"] == []


async def test_two_controllers_cannot_claim_same_step_or_reset_active_run(support_env):
    from remote_fleet_agent.live_service import LiveService

    s, owner, _, directory = support_env
    record = approved(s, owner, plan_body())
    target = record["targets"][0]
    step = record["plan"]["steps"][0]
    assert s.repairs.lease()
    second = LiveService(directory)
    try:
        assert s.repairs.claim_step(record["id"], target, step) is True
        assert second.repairs.claim_step(record["id"], target, step) is False
        assert second.repairs.lease() is False
        row = s.store.db.execute(
            "SELECT phase FROM execution_steps WHERE run_id=?", (record["id"],)
        ).fetchone()
        assert row["phase"] == "dispatch_intent"
    finally:
        await second.close()


async def test_disabled_approver_blocks_dispatch(support_env):
    s, owner, tech, _ = support_env
    manager, _ = s.store.add_operator(
        "Manager", "manager", {"northstar-demo": ["head-office"]}, owner
    )
    record = approved(s, manager, plan_body())
    s.store.db.execute("UPDATE operators SET enabled=0 WHERE id=?", (manager["id"],))
    s.store.db.commit()
    with pytest.raises(Denied):
        s.repairs.execute(tech, "northstar-demo", record["id"], decision(record))
    assert record["results"] == []


async def test_cross_interface_endpoint_claims_exclude_aliases(support_env):
    from remote_fleet_agent.administration import EndpointClaim, legacy_target

    s, owner, _, _ = support_env
    record = approved(s, owner, plan_body())
    target = record["targets"][0]
    claim = EndpointClaim(target, "first", s.store.folder / "endpoint-locks")
    alias = dict(
        target, name="alias", target="another@" + target["target"].split("@", 1)[1]
    )
    try:
        with pytest.raises(Conflict):
            EndpointClaim(alias, "second", s.store.folder / "endpoint-locks")
    finally:
        claim.release()
    config = s.config("northstar-demo")
    assert legacy_target(config, config.resolve("windows-01"))["simulation"]


async def test_cancellation_request_cannot_release_live_endpoint_claim(support_env):
    s, owner, _, _ = support_env
    record = approved(s, owner, plan_body())
    record = s.repairs.update(
        owner, "northstar-demo", record, {"state": "cancellation_requested"}
    )
    with pytest.raises(Conflict):
        s.repairs.reconcile(
            owner,
            "northstar-demo",
            record["id"],
            {**decision(record), "note": "Cannot establish stop while still running"},
        )


async def test_old_site_scope_required_for_detail_and_legacy_list(support_env):
    from remote_fleet_agent.storage import read_json, save_json

    s, owner, _, directory = support_env
    session = s.investigations.request(
        owner, "northstar-demo", {"targets": ["windows-01"]}
    )
    case = s.support.create(
        owner,
        "northstar-demo",
        {"host": "windows-01", "symptom": "OLD_SITE_PRIVATE", "profile": "general"},
    )
    path = directory / "northstar-demo/fleet.json"
    raw = read_json(path)
    raw["hosts"][0]["site"] = "new-site"
    save_json(path, raw)
    manager, _ = s.store.add_operator(
        "New site", "manager", {"northstar-demo": ["new-site"]}, owner
    )
    with pytest.raises(Denied):
        s.investigations.get(manager, "northstar-demo", session["id"])
    with pytest.raises(Denied):
        s.support.get(manager, "northstar-demo", case["id"])
    assert s.support.list(manager, "northstar-demo") == []


async def test_precondition_success_is_not_post_change_verification(support_env):
    s, owner, _, _ = support_env
    p = plan_body()
    p["steps"].reverse()
    record = approved(s, owner, p)
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await s.repairs.wait()
    record = s.repairs.get(owner, "northstar-demo", record["id"])
    assert record["state"] == "paused" and record["last_outcome"] == "command_completed"


async def test_expired_controller_cannot_continue_after_takeover(
    support_env, monkeypatch
):
    import asyncio

    from remote_fleet_agent.live_service import LiveService

    s, owner, _, directory = support_env
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow(actor, client, ident, target, step):
        calls.append(step["id"])
        entered.set()
        await release.wait()
        return {
            "exit_code": 0,
            "stdout": '{"passed":true}',
            "stderr": "",
            "truncated": False,
        }

    monkeypatch.setattr(s.repairs, "dispatch", slow)
    record = approved(s, owner, plan_body())
    s.repairs.execute(owner, "northstar-demo", record["id"], decision(record))
    await entered.wait()
    other = LiveService(directory)
    try:
        other.repairs.clock = lambda: datetime.now(UTC) + timedelta(seconds=31)
        assert other.repairs.lease()
        release.set()
        await s.repairs.wait()
        assert calls == ["change"]
        assert (
            s.repairs.get(owner, "northstar-demo", record["id"])["state"] == "unknown"
        )
    finally:
        release.set()
        await other.close()


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("outcome", ["unknown", "dispatch_intent"])
async def test_unknown_diagnostic_has_guarded_reconciliation(
    support_env, archived, outcome
):
    from remote_fleet_agent.administration import EndpointClaim, claim_path

    s, owner, tech, _ = support_env
    record = s.investigations.request(
        tech, "northstar-demo", {"targets": ["windows-01"]}
    )
    record = s.investigations.confirm(
        owner, "northstar-demo", record["id"], decision(record)
    )
    target = record["targets"][0]
    claim = EndpointClaim(target, record["id"], s.store.folder / "endpoint-locks")
    if outcome == "unknown":
        claim.mark_unknown()
    else:
        import json

        path = claim_path(target, s.store.folder / "endpoint-locks")
        state = json.loads(path.read_text())
        state["owner_pid"] = 2147483647
        path.write_text(json.dumps(state))
    record = s.investigations.update(
        owner,
        "northstar-demo",
        record,
        {"commands": [{"host": "windows-01", "outcome": outcome}]},
    )
    if archived:
        import json

        s.store.db.execute(
            "INSERT INTO investigation_commands VALUES(?,?,?,?)",
            (
                "archived-unknown",
                record["id"],
                "northstar-demo",
                json.dumps(record["commands"][0]),
            ),
        )
        s.store.db.commit()
        record = s.investigations.update(
            owner, "northstar-demo", record, {"commands": []}
        )
    body = {
        **decision(record),
        "host": "windows-01",
        "note": "Observed dedicated test process stopped",
    }
    with pytest.raises(Denied):
        s.investigations.reconcile(tech, "northstar-demo", record["id"], body)
    s.investigations.reconcile(owner, "northstar-demo", record["id"], body)
    assert not claim_path(target, s.store.folder / "endpoint-locks").exists()
    claim.release()


async def test_complete_diagnostic_history_is_paged(support_env):
    import json
    import uuid

    s, owner, _, _ = support_env
    record = s.investigations.request(
        owner, "northstar-demo", {"targets": ["windows-01"]}
    )
    for i in range(105):
        s.store.db.execute(
            "INSERT INTO investigation_commands VALUES(?,?,?,?)",
            (
                uuid.uuid4().hex,
                record["id"],
                "northstar-demo",
                json.dumps({"index": i}),
            ),
        )
    s.store.db.commit()
    page = s.investigations.history(owner, "northstar-demo", record["id"], 0, 10)
    assert page["items"][0]["index"] == 0 and page["next_offset"] == 10
    assert (
        s.investigations.history(owner, "northstar-demo", record["id"], 100, 10)[
            "items"
        ][-1]["index"]
        == 104
    )
