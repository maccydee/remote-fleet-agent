"""Engineer decisions use authenticated HTTP sessions, never model arguments."""

import json
import time

from tests.test_administration import plan_body
from tests.test_live_workspace import login, request
from tests.test_operations_http import operations_http as _operations_http

operations_http = _operations_http


def post(app, cookie, csrf, path, body):
    status, _, raw = request(
        app,
        path,
        "POST",
        body,
        {"Cookie": cookie, "Origin": app.url, "X-Fleet-CSRF": csrf},
    )
    return status, json.loads(raw)


def test_http_target_confirmation_and_plan_approval_are_separate(operations_http):
    app, tech_cookie, _ = operations_http
    _, _, raw = request(app, "/api/bootstrap", headers={"Cookie": tech_cookie})
    tech_csrf = json.loads(raw)["csrf"]
    base = "/api/clients/northstar-demo"
    status, session = post(
        app,
        tech_cookie,
        tech_csrf,
        base + "/investigations",
        {"targets": ["windows-01"]},
    )
    assert status == 200 and session["state"] == "pending_target_confirmation"
    decision = {"revision": session["revision"], "digest": session["digest"]}
    assert (
        post(
            app,
            tech_cookie,
            tech_csrf,
            base + "/investigations/" + session["id"] + "/confirm",
            decision,
        )[0]
        == 403
    )
    owner_cookie, owner_csrf = login(app)
    status, session = post(
        app,
        owner_cookie,
        owner_csrf,
        base + "/investigations/" + session["id"] + "/confirm",
        decision,
    )
    assert status == 200
    status, result = post(
        app,
        tech_cookie,
        tech_csrf,
        base + "/investigations/" + session["id"] + "/run",
        {"host": "windows-01", "script": "not executed", "timeout_seconds": 10},
    )
    assert status == 200 and result["simulation"] is True
    status, plan = post(
        app, tech_cookie, tech_csrf, base + "/repair-plans", plan_body()
    )
    assert status == 200
    endpoint = base + "/repair-plans/" + plan["id"]
    decision = {"revision": plan["revision"], "digest": plan["digest"]}
    assert post(app, tech_cookie, tech_csrf, endpoint + "/approve", decision)[0] == 403
    status, plan = post(app, owner_cookie, owner_csrf, endpoint + "/approve", decision)
    assert status == 200 and plan["approval"]["channel"] == "workspace"
    status, _ = post(
        app,
        tech_cookie,
        tech_csrf,
        endpoint + "/execute",
        {"revision": plan["revision"], "digest": plan["digest"]},
    )
    assert status == 200
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _, _, raw = request(app, endpoint, headers={"Cookie": tech_cookie})
        result = json.loads(raw)
        if result["state"] == "checks_passed":
            break
        time.sleep(0.02)
    assert result["state"] == "checks_passed" and result["user_outcome"] == "pending"


def test_operations_lists_new_decisions_and_health_is_owner_only(operations_http):
    app, cookie, _ = operations_http
    _, _, raw = request(app, "/api/bootstrap", headers={"Cookie": cookie})
    csrf = json.loads(raw)["csrf"]
    assert json.loads(raw)["capabilities"]["repair_plans"] is True
    status, session = post(
        app,
        cookie,
        csrf,
        "/api/clients/northstar-demo/investigations",
        {"targets": ["windows-01"]},
    )
    assert status == 200
    _, _, raw = request(
        app, "/api/operations?bucket=approval", headers={"Cookie": cookie}
    )
    rows = json.loads(raw)["items"]
    assert any(r["kind"] == "investigation" and r["id"] == session["id"] for r in rows)
    assert request(app, "/api/health", headers={"Cookie": cookie})[0] == 403
    owner_cookie, _ = login(app)
    status, _, raw = request(app, "/api/health", headers={"Cookie": owner_cookie})
    assert status == 200 and json.loads(raw)["ready"] is True
