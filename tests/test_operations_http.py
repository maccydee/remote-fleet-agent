"""Real HTTP tests: no browser rendering or endpoint SSH is substituted for auth."""

import json
import threading
from urllib.parse import urlencode

import pytest

from remote_fleet_agent.live_app import Application
from remote_fleet_agent.storage import read_json
from tests.test_live_workspace import request


@pytest.fixture
def operations_http(directory, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("HTTP operations tests must never connect to endpoints")

    monkeypatch.setattr("remote_fleet_agent.runtime.connect", forbidden)
    app = Application(directory, 0)
    thread = threading.Thread(target=app.server.serve_forever, daemon=True)
    thread.start()
    token = read_json(directory / ".state/owner-access.json")["token"]
    owner = app.service.store.authenticate(token)
    tech, tech_token = app.service.store.add_operator(
        "Scoped technician", "technician", {"northstar-demo": ["head-office"]}, owner
    )
    for client, host, symptom in [
        ("northstar-demo", "windows-01", "VISIBLE"),
        ("northstar-demo", "linux-04", "SECRET_SITE"),
        ("harbour-demo", "windows-01", "SECRET_CLIENT"),
    ]:
        app.service.support.create(
            owner, client, {"host": host, "symptom": symptom, "profile": "general"}
        )
    status, headers, _ = request(
        app, "/api/login", "POST", {"token": tech_token}, {"Origin": app.url}
    )
    assert status == 200
    cookie = headers["Set-Cookie"].split(";")[0]
    try:
        yield app, cookie, tech
    finally:
        app.server.shutdown()
        thread.join(timeout=3)
        app.close()


def get(env, query="", **headers):
    app, cookie, _ = env
    return request(
        app, "/api/operations" + query, headers={"Cookie": cookie, **headers}
    )


def test_operations_route_requires_login(operations_http):
    app, _, _ = operations_http
    assert request(app, "/api/operations")[0] == 403


def test_authenticated_http_matches_scoped_projection(operations_http):
    app, _, tech = operations_http
    status, headers, raw = get(operations_http)
    assert status == 200
    assert json.loads(raw) == app.service.operations(tech, {})
    assert json.loads(raw)["total"] == 1
    assert b"SECRET" not in raw
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=true",
        "?limit=201",
        "?offset=-1",
        "?offset=1000001",
        "?limit=1&limit=2",
        "?q=&q=",
        "?unknown=",
        "?unknown=x",
        "?limit=",
        "?limit",
        "?limit=1.0",
        "?platform=darwin",
        "?bucket=invalid",
        "?" + urlencode({"q": "x" * 241}),
    ],
)
def test_invalid_filters_are_rejected(operations_http, query):
    status, _, raw = get(operations_http, query)
    assert status == 400
    assert b"error" in raw and b"SECRET" not in raw


def test_encoded_search_and_empty_all_clients_filter(operations_http):
    status, _, raw = get(
        operations_http, "?client=&" + urlencode({"q": "VIS", "limit": 1})
    )
    assert status == 200 and json.loads(raw)["total"] == 1
    assert get(operations_http, "?" + urlencode({"q": "nothing <script>"}))[0] == 200


@pytest.mark.parametrize(
    "query", ["?client=harbour-demo", "?client=northstar-demo&site=warehouse"]
)
def test_explicit_denied_scope_is_not_exposed(operations_http, query):
    status, _, raw = get(operations_http, query)
    assert status == 403
    assert b"items" not in raw and b"SECRET" not in raw


def test_host_origin_and_revocation_guards(operations_http):
    app, _, tech = operations_http
    assert get(operations_http, Host="evil.example")[0] == 403
    assert get(operations_http, Origin="https://evil.example")[0] == 403
    app.service.store.db.execute(
        "UPDATE operators SET enabled=0 WHERE id=?", (tech["id"],)
    )
    app.service.store.db.commit()
    assert get(operations_http)[0] == 403


def test_operations_has_no_post_mutation(operations_http):
    app, cookie, _ = operations_http
    before = app.service.store.db.execute("SELECT count(*) FROM records").fetchone()[0]
    status, _, _ = request(
        app, "/api/operations", "POST", {}, {"Cookie": cookie, "Origin": app.url}
    )
    assert status == 403  # Existing CSRF protection runs before route matching.
    _, _, raw = request(app, "/api/bootstrap", headers={"Cookie": cookie})
    csrf = json.loads(raw)["csrf"]
    status, _, _ = request(
        app,
        "/api/operations",
        "POST",
        {},
        {"Cookie": cookie, "Origin": app.url, "X-Fleet-CSRF": csrf},
    )
    assert status == 404
    assert (
        app.service.store.db.execute("SELECT count(*) FROM records").fetchone()[0]
        == before
    )
