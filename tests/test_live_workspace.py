import http.client
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from remote_fleet_agent.live_app import Application, add_client
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Conflict, Denied, LiveStore
from remote_fleet_agent.storage import read_json, save_json


@pytest.fixture
async def fleet(support_env):
    # Preserve the two-item tuple used by the existing workspace suite.
    service, owner, _, _ = support_env
    yield service, owner


async def collect(
    service, actor, hosts=None, operation="inventory", client="northstar-demo"
):
    plan = service.preview(
        actor, client, {"hosts": hosts or ["windows-01"], "operation": operation}
    )
    summary = await service.start(
        actor, client, {"plan_id": plan["id"], "digest": plan["digest"]}
    )
    runtime = await service.runtime(client)
    return await runtime.jobs.wait(summary["id"])


def tech(service, owner, role="technician", site="head-office"):
    return service.store.add_operator(
        "Scoped engineer", role, {"northstar-demo": [site]}, owner
    )[0]


async def test_collection_requires_explicit_single_use_preview(fleet):
    s, a = fleet
    with pytest.raises(ValueError):
        s.preview(a, "northstar-demo", {"operation": "inventory"})
    p = s.preview(
        a, "northstar-demo", {"hosts": ["windows-01"], "operation": "inventory"}
    )
    body = {"plan_id": p["id"], "digest": p["digest"]}
    await s.start(a, "northstar-demo", body)
    with pytest.raises(Denied):
        await s.start(a, "northstar-demo", body)


async def test_preview_cannot_cross_actor_client_or_policy(fleet, directory):
    s, a = fleet
    b = tech(s, a)
    p = s.preview(
        a, "northstar-demo", {"hosts": ["windows-01"], "operation": "inventory"}
    )
    body = {"plan_id": p["id"], "digest": p["digest"]}
    with pytest.raises(Denied):
        await s.start(b, "northstar-demo", body)
    with pytest.raises(Denied):
        await s.start(a, "harbour-demo", body)
    config = directory / "northstar-demo/fleet.json"
    raw = read_json(config)
    raw["query_timeout_seconds"] = 31
    save_json(config, raw)
    with pytest.raises(Conflict):
        await s.start(a, "northstar-demo", body)


async def test_site_scope_covers_snapshots_jobs_manual_tasks_and_clients(fleet):
    s, a = fleet
    b = tech(s, a)
    result = await collect(s, a, ["windows-01", "linux-04"])
    s.save_manual(
        a,
        "northstar-demo",
        {
            "site": "warehouse",
            "fields": {
                "asset": "WAREHOUSE_SECRET",
                "category": "backup",
                "control": "2500",
                "status": "open",
            },
        },
    )
    s.save_task(a, "northstar-demo", {"site": "warehouse", "title": "PRIVATE_TASK"})
    snap = s.snapshot(b, "northstar-demo")
    serialized = json.dumps(snap)
    assert (
        "WAREHOUSE_SECRET" not in serialized
        and "PRIVATE_TASK" not in serialized
        and "linux-04" not in serialized
    )
    visible = await s.job(b, "northstar-demo", result["id"])
    assert list(visible["hosts"]) == ["windows-01"]
    with pytest.raises(Denied):
        s.snapshot(b, "harbour-demo")
    with pytest.raises(Denied):
        s.preview(
            b, "northstar-demo", {"hosts": ["linux-04"], "operation": "inventory"}
        )
    with pytest.raises(Denied):
        s.save_task(b, "northstar-demo", {"site": "warehouse", "title": "No"})


async def test_roles_and_disabled_operator_rechecked_before_dispatch(fleet):
    s, a = fleet
    b = tech(s, a, "observer")
    with pytest.raises(Denied):
        s.preview(
            b, "northstar-demo", {"hosts": ["windows-01"], "operation": "inventory"}
        )
    with pytest.raises(Denied):
        s.save_manual(b, "northstar-demo", {"site": "head-office", "fields": {}})
    b = tech(s, a)
    p = s.preview(
        b, "northstar-demo", {"hosts": ["windows-01"], "operation": "inventory"}
    )
    with s.store.db:
        s.store.db.execute("UPDATE operators SET enabled=0 WHERE id=?", (b["id"],))
    with pytest.raises(Denied):
        await s.start(b, "northstar-demo", {"plan_id": p["id"], "digest": p["digest"]})


async def test_saved_evidence_only_and_expiry_block_collections(fleet, directory):
    s, a = fleet
    path = directory / "workspace.json"
    raw = read_json(path)
    raw["clients"][0]["allow_collection"] = False
    save_json(path, raw)
    with pytest.raises(Denied):
        await collect(s, a)
    raw["clients"][0]["allow_collection"] = True
    raw["clients"][0]["access_expires_at"] = (
        datetime.now(UTC) - timedelta(seconds=1)
    ).isoformat()
    save_json(path, raw)
    with pytest.raises(Denied):
        await collect(s, a)
    assert s.snapshot(a, "northstar-demo")["access"]["expired"]


async def test_task_revisions_verification_and_scope(fleet):
    s, a = fleet
    b = tech(s, a)
    data = {
        "site": "head-office",
        "host": "windows-01",
        "title": "Verify backup",
        "owner": "Alice",
        "priority": "high",
    }
    task = s.save_task(b, "northstar-demo", data)
    with pytest.raises(Conflict):
        s.save_task(b, "northstar-demo", {**data, "id": task["id"], "revision": 0})
    with pytest.raises(ValueError):
        s.save_task(b, "northstar-demo", {**data, "status": "verified"})
    result = await collect(s, a)
    saved = s.save_task(
        b,
        "northstar-demo",
        {
            **data,
            "id": task["id"],
            "revision": task["revision"],
            "status": "verified",
            "verification_job": result["id"],
        },
    )
    assert saved["status"] == "verified" and saved["revision"] == 2
    with pytest.raises(Denied):
        s.save_task(b, "harbour-demo", data)


async def test_manual_authenticated_reviewer_and_conflict(fleet):
    s, a = fleet
    b = tech(s, a)
    data = {
        "site": "head-office",
        "fields": {
            "asset": "Router",
            "category": "router",
            "control": "0001",
            "status": "open",
            "reviewer": "spoofed",
        },
    }
    record = s.save_manual(b, "northstar-demo", data)
    assert record["reviewer"] == b["name"] and record["site"] == "head-office"
    with pytest.raises(ValueError):
        s.save_manual(b, "northstar-demo", {**data, "id": record["id"], "revision": 0})


async def test_saved_views_private_and_shared_permissions(fleet):
    s, a = fleet
    b = tech(s, a)
    other = tech(s, a)
    view = s.save_view(
        b,
        "northstar-demo",
        {
            "site": "head-office",
            "name": "Needs work",
            "page": "Fleet",
            "filters": {
                "columns": ["name"],
                "column_filters": {"freshness_state": "stale"},
            },
        },
    )
    assert s.records(b, "view", "northstar-demo")[0]["id"] == view["id"]
    assert not s.records(other, "view", "northstar-demo")
    with pytest.raises(Denied):
        s.save_view(
            b,
            "northstar-demo",
            {"site": "head-office", "name": "Shared", "shared": True},
        )
    with pytest.raises(ValueError):
        s.save_view(
            b,
            "northstar-demo",
            {"site": "head-office", "name": "Bad", "page": "<script>"},
        )


async def test_baseline_comparison_reports_escape_and_redact(fleet):
    s, a = fleet
    first = await collect(s, a)
    baseline = await s.baseline(
        a, "northstar-demo", {"site": "head-office", "job_id": first["id"]}
    )
    second = await collect(s, a)
    diff = await s.compare(
        a, "northstar-demo", first["id"], second["id"], "head-office"
    )
    assert not diff["changes"]
    s.save_task(
        a,
        "northstar-demo",
        {
            "site": "head-office",
            "title": "<script>PRIVATE_TASK</script>",
            "owner": "PRIVATE_OWNER",
            "notes": "PRIVATE_NOTE",
        },
    )
    s.save_branding(
        a,
        "northstar-demo",
        {
            "name": "PRIVATE_CLIENT",
            "prepared_by": "PRIVATE_ENGINEER",
            "theme": "blue",
            "freshness_hours": 48,
        },
    )
    private = await s.report(a, "northstar-demo", "head-office", False)
    shared = await s.report(a, "northstar-demo", "head-office", True)
    assert (
        "&lt;script&gt;PRIVATE_TASK" in private
        and "<script>PRIVATE_TASK" not in private
    )
    assert all(
        secret not in shared
        for secret in (
            "PRIVATE_TASK",
            "PRIVATE_OWNER",
            "PRIVATE_NOTE",
            "PRIVATE_CLIENT",
            "PRIVATE_ENGINEER",
            "windows-01",
            "demo@",
        )
    )
    assert "Executive summary" in shared and "Open actions" in shared
    assert baseline["job_id"] == first["id"]


async def test_schedule_dispatch_pause_scope_and_policy_change(fleet, directory):
    s, a = fleet
    b = tech(s, a, "manager")
    schedule = s.save_schedule(
        b,
        "northstar-demo",
        {
            "site": "head-office",
            "name": "Inventory",
            "hosts": ["windows-01"],
            "operation": "inventory",
            "enabled": True,
            "interval_minutes": 5,
        },
    )
    await s.tick(datetime.now(UTC) + timedelta(minutes=6))
    saved = s.store.get("schedule", schedule["id"], "northstar-demo")
    assert saved["state"] == "dispatched" and saved["last_job"]
    rt = await s.runtime("northstar-demo")
    await rt.jobs.wait(saved["last_job"])
    config = directory / "northstar-demo/fleet.json"
    raw = read_json(config)
    raw["query_timeout_seconds"] = 35
    save_json(config, raw)
    await s.tick(datetime.now(UTC) + timedelta(minutes=12))
    saved = s.store.get("schedule", schedule["id"], "northstar-demo")
    assert saved["state"] == "blocked" and not saved["enabled"]


async def test_freshness_never_stale_and_clock_skew(fleet):
    s, _a = fleet
    assert s.freshness(None, "northstar-demo")["state"] == "never_collected"
    assert (
        s.freshness(
            (datetime.now(UTC) - timedelta(days=2)).isoformat(), "northstar-demo"
        )["state"]
        == "stale"
    )
    assert (
        s.freshness(
            (datetime.now(UTC) + timedelta(hours=1)).isoformat(), "northstar-demo"
        )["state"]
        == "clock_skew"
    )


async def test_incomplete_compare_never_counts_as_unchanged(fleet):
    s, a = fleet
    first = await collect(s, a, ["linux-04", "linux-05"])
    second = await collect(s, a, ["linux-04", "linux-05"])
    diff = await s.compare(a, "northstar-demo", first["id"], second["id"])
    assert diff["unavailable"]


def test_journal_tamper_blocks_writes(directory):
    s = LiveService(directory)
    actor = s.store.authenticate(
        read_json(directory / ".state/owner-access.json")["token"]
    )
    with s.store.db:
        s.store.db.execute("UPDATE activity SET event='tampered' WHERE seq=1")
    s.store.close()
    s = LiveService(directory)
    assert not s.store.integrity
    with pytest.raises(Denied):
        s.save_task(
            actor, "northstar-demo", {"site": "head-office", "title": "Blocked"}
        )
    assert s.snapshot(actor, "northstar-demo")["hosts"]
    s.store.close()


def test_record_reserved_fields_cannot_override_scope(directory):
    store = LiveStore(directory)
    actor = store.authenticate(
        read_json(directory / ".state/owner-access.json")["token"]
    )
    with pytest.raises(ValueError):
        store.save(
            "task", "northstar-demo", "head-office", actor, {"site": "warehouse"}
        )
    store.close()


@pytest.fixture
def http_app(directory):
    app = Application(directory, 0)
    thread = threading.Thread(target=app.server.serve_forever, daemon=True)
    thread.start()
    yield app
    app.server.shutdown()
    thread.join(timeout=3)
    app.close()


def request(app, path="/", method="GET", body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.server.server_port, timeout=10)
    head = {"Host": app.url.split("//")[1], **(headers or {})}
    if body is not None:
        body = json.dumps(body)
        head["Content-Type"] = "application/json"
    conn.request(method, path, body=body, headers=head)
    response = conn.getresponse()
    raw = response.read()
    result = (response.status, dict(response.getheaders()), raw)
    conn.close()
    return result


def login(app):
    token = read_json(app.directory / ".state/owner-access.json")["token"]
    status, headers, _ = request(
        app, "/api/login", "POST", {"token": token}, {"Origin": app.url}
    )
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    _, _, body = request(app, "/api/bootstrap", headers={"Cookie": cookie})
    return cookie, json.loads(body)["csrf"]


def test_http_authentication_origin_csrf_and_host_guards(http_app):
    app = http_app
    assert request(app, "/api/bootstrap")[0] == 403
    assert request(app, "/", headers={"Host": "evil.example"})[0] == 403
    assert (
        request(
            app,
            "/api/login",
            "POST",
            {"token": "invalid"},
            {"Origin": "http://evil.example"},
        )[0]
        == 403
    )
    cookie, csrf = login(app)
    path = "/api/clients/northstar-demo/tasks"
    body = {"site": "head-office", "title": "HTTP follow-up"}
    assert (
        request(app, path, "POST", body, {"Cookie": cookie, "Origin": app.url})[0]
        == 403
    )
    assert (
        request(
            app,
            path,
            "POST",
            body,
            {"Cookie": cookie, "Origin": "http://evil.example", "X-Fleet-CSRF": csrf},
        )[0]
        == 403
    )
    status, _, raw = request(
        app,
        path,
        "POST",
        body,
        {"Cookie": cookie, "Origin": app.url, "X-Fleet-CSRF": csrf},
    )
    assert status == 200 and json.loads(raw)["title"] == "HTTP follow-up"
    status, headers, raw = request(app, "/")
    assert (
        status == 200
        and "script-src 'self'" in headers["Content-Security-Policy"]
        and "no-store" == headers["Cache-Control"]
    )
    assert b"owner-access" not in raw
    assert request(app, "/.state/owner-access.json")[0] == 404


def test_http_operator_creation_scope_and_revocation(http_app):
    app = http_app
    cookie, csrf = login(app)
    headers = {"Cookie": cookie, "Origin": app.url, "X-Fleet-CSRF": csrf}
    status, _, raw = request(
        app,
        "/api/operators",
        "POST",
        {
            "name": "Viewer",
            "role": "observer",
            "scopes": {"northstar-demo": ["head-office"]},
        },
        headers,
    )
    assert status == 200
    created = json.loads(raw)
    status, res, _ = request(
        app, "/api/login", "POST", {"token": created["token"]}, {"Origin": app.url}
    )
    viewer_cookie = res["Set-Cookie"].split(";")[0]
    assert (
        request(
            app, "/api/clients/harbour-demo/snapshot", headers={"Cookie": viewer_cookie}
        )[0]
        == 403
    )
    assert request(app, "/api/operators", headers={"Cookie": viewer_cookie})[0] == 403
    assert (
        request(
            app,
            "/api/operators/" + created["operator"]["id"] + "/disable",
            "POST",
            {},
            headers,
        )[0]
        == 200
    )
    assert request(app, "/api/bootstrap", headers={"Cookie": viewer_cookie})[0] == 403


def test_real_client_registration_defaults_to_saved_only(directory, tmp_path):
    config = tmp_path / "real.json"
    save_json(config, {"client": "saved-client", "known_hosts": "missing", "hosts": []})
    result = add_client(directory, config, tmp_path / "jobs")
    assert not result["collection_enabled"]
    with pytest.raises(ValueError):
        add_client(directory, config, tmp_path / "jobs")


async def test_failed_device_comparison_is_unavailable(fleet):
    s, a = fleet
    first = await collect(s, a, ["linux-05"])
    second = await collect(s, a, ["linux-05"])
    diff = await s.compare(a, "northstar-demo", first["id"], second["id"])
    assert diff["unavailable"] == [
        {
            "host": "linux-05",
            "section": "all",
            "reason": "Collection evidence is missing or the device failed.",
        }
    ]


def test_schedule_can_be_paused_after_access_is_disabled(http_app):
    app = http_app
    cookie, csrf = login(app)
    headers = {"Cookie": cookie, "Origin": app.url, "X-Fleet-CSRF": csrf}
    body = {
        "site": "head-office",
        "name": "Optional",
        "hosts": ["windows-01"],
        "operation": "inventory",
        "enabled": True,
        "interval_minutes": 5,
    }
    status, _, raw = request(
        app, "/api/clients/northstar-demo/schedules", "POST", body, headers
    )
    assert status == 200
    schedule = json.loads(raw)
    path = app.directory / "workspace.json"
    raw = read_json(path)
    raw["clients"][0]["allow_collection"] = False
    raw["clients"][0]["allow_schedules"] = False
    save_json(path, raw)
    status, _, raw = request(
        app,
        "/api/clients/northstar-demo/pause-schedule",
        "POST",
        {"id": schedule["id"], "revision": schedule["revision"]},
        headers,
    )
    assert status == 200 and not json.loads(raw)["enabled"]


def test_brand_assets_are_served_without_unlocking_api(http_app):
    import xml.etree.ElementTree as ET

    for route in ("/logo.svg", "/favicon.svg"):
        status, headers, body = request(http_app, route)
        assert status == 200
        assert headers["Content-Type"].startswith("image/svg+xml")
        assert ET.fromstring(body).tag == "{http://www.w3.org/2000/svg}svg"
        assert request(http_app, route, headers={"Host": "evil.example"})[0] == 403
    assert request(http_app, "/api/bootstrap")[0] == 403
    assert request(http_app, "/app_assets/../fleet.json")[0] == 404


def test_export_brand_is_self_contained():
    import base64
    import xml.etree.ElementTree as ET

    from remote_fleet_agent.reporting import brand_image

    image = brand_image()
    encoded = image.split("base64,", 1)[1].split('"', 1)[0]
    svg = base64.b64decode(encoded, validate=True)
    assert ET.fromstring(svg).attrib["aria-label"] == "Remote Fleet"
    assert b"<image" not in svg and b"<script" not in svg
    assert 'src="data:image/svg+xml;' in image
