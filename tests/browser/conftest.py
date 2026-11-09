"""Real loopback application, fictional records and independently authenticated browser."""

import threading

import pytest

pytest.importorskip("playwright.sync_api")
from remote_fleet_agent.live_app import Application, seed_demo
from remote_fleet_agent.storage import read_json


@pytest.fixture(scope="module")
def workspace(tmp_path_factory):
    directory = seed_demo(tmp_path_factory.mktemp("browser-workspace") / "workspace")
    monkeypatch = pytest.MonkeyPatch()

    async def forbidden(*args, **kwargs):
        pytest.fail("Browser suite attempted endpoint SSH")

    monkeypatch.setattr("remote_fleet_agent.runtime.connect", forbidden)
    app = Application(directory, 0)
    thread = threading.Thread(target=app.server.serve_forever, daemon=True)
    thread.start()
    token = read_json(directory / ".state/owner-access.json")["token"]
    owner = app.service.store.authenticate(token)
    cases = [
        app.service.support.create(
            owner,
            client,
            {"host": "windows-01", "symptom": symptom, "profile": "general"},
        )
        for client, symptom in [
            ("northstar-demo", "Northstar Outlook will not open"),
            ("harbour-demo", "Harbour test failure"),
        ]
    ]
    try:
        yield app, token, cases
    finally:
        app.server.shutdown()
        thread.join(timeout=3)
        app.close()
        monkeypatch.undo()


@pytest.fixture
def signed_in(page, workspace, request):
    app, token, _ = workspace
    if request.config.getoption("--capture-showcase"):
        assert page.request.post(
            app.url + "/api/login", data={"token": token}, headers={"Origin": app.url}
        ).ok
        page.goto(app.url)
        page.locator("#shell").wait_for(state="visible")
        return page
    page.goto(app.url)
    page.get_by_label("Access token").fill(token)
    page.get_by_role("button", name="Open workspace", exact=True).click()
    page.locator("#shell").wait_for(state="visible")
    return page


@pytest.fixture
def workspace_url(workspace):
    return workspace[0].url


def pytest_addoption(parser):
    parser.addoption(
        "--capture-showcase",
        action="store_true",
        help="Record fictional browser scenarios privately",
    )
    parser.addoption(
        "--showcase-dir",
        default=None,
        help="Private marketing output directory outside the repository",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    setattr(item, "report_" + outcome.get_result().when, outcome.get_result())


@pytest.fixture
def context(browser, browser_context_args, request, tmp_path):
    if not request.config.getoption("--capture-showcase"):
        context = browser.new_context(**browser_context_args)
        yield context
        context.close()
        return
    import hashlib
    import importlib.util
    from datetime import UTC, datetime
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    capture_path = root / "scripts/capture-showcase.py"
    spec = importlib.util.spec_from_file_location("fleet_capture", capture_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    configured = request.config.getoption("--showcase-dir")
    output = Path(configured) if configured else tmp_path / "private-showcase"
    run_id = (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:8]
    )
    assets = root / "src/remote_fleet_agent"
    digest = hashlib.sha256(
        b"".join(
            p.read_bytes()
            for p in sorted(assets.rglob("*"))
            if p.is_file() and p.suffix in {".py", ".js", ".css", ".html", ".svg"}
        )
    ).hexdigest()
    capture = module.CaptureSession(
        browser,
        output / run_id,
        run_id,
        "source-tree-sha256:" + digest,
        environment="simulation",
        visible_simulation_label=True,
    )
    context = capture.context
    context.fleet_capture = capture
    yield context
    report = getattr(request.node, "report_call", None)
    passed = bool(report and report.passed)
    if passed:
        for i, page in enumerate(context.pages):
            if not page.is_closed():
                capture.screenshot(page, request.node.name + "-" + str(i))
    capture.finish("PASS" if passed else "FAIL")


@pytest.fixture(autouse=True)
def browser_diagnostics(page, request, tmp_path):
    """Keep failed scenes private and surface actual JavaScript exceptions."""
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    yield
    report = getattr(request.node, "report_call", None)
    if report and report.failed and not page.is_closed():
        artifact = tmp_path / "private-browser-failure.png"
        page.screenshot(path=str(artifact))
        print(f"Private browser failure screenshot: {artifact}")
    assert not errors, "Browser JavaScript errors: " + "; ".join(errors)
