from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_confirm_investigation_targets_in_workspace(page, workspace, signed_in):
    app, token, _ = workspace
    actor = app.service.store.authenticate(token)
    record = app.service.investigations.request(
        actor, "northstar-demo", {"targets": ["windows-01"]}
    )
    page.goto(
        app.url + "/?page=Approvals&client=northstar-demo&investigation=" + record["id"]
    )
    expect(
        page.get_by_role("heading", name="Confirm investigation targets", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("SIMULATED ENVIRONMENT", exact=True)).to_be_visible()
    page.get_by_role("button", name="Confirm named devices", exact=True).click()
    expect(
        page.get_by_role("button", name="Close investigation", exact=True)
    ).to_be_visible()
    observed = app.service.investigations.get(actor, "northstar-demo", record["id"])
    assert observed["state"] == "active"
    assert observed["approval"]["channel"] == "workspace"


def test_bounded_plan_review_rejects_exact_digest(page, workspace, signed_in):
    app, token, _ = workspace
    actor = app.service.store.authenticate(token)
    start = datetime.now(UTC)
    plan = {
        "schema": 1,
        "title": "Inspect and repair fictional marker",
        "rationale": "Dedicated scratch marker missing",
        "uncertainty": "Does not resolve a user application",
        "impact": "Only changes scratch marker",
        "targets": ["windows-01"],
        "window": {
            "start": start.isoformat(),
            "end": (start + timedelta(hours=1)).isoformat(),
            "timezone": "Europe/London",
        },
        "disruption": "now",
        "trial": "windows-01",
        "steps": [
            {
                "id": "change-marker",
                "kind": "change",
                "platform": "windows",
                "targets": ["windows-01"],
                "script": "Write-Output 'fictional marker'",
                "timeout_seconds": 10,
            },
            {
                "id": "check-marker",
                "kind": "check",
                "platform": "windows",
                "targets": ["windows-01"],
                "script": 'Write-Output \'{"passed":true,"detail":"fictional"}\'',
                "timeout_seconds": 10,
            },
        ],
    }
    record = app.service.repairs.propose(actor, "northstar-demo", plan)
    page.goto(
        app.url + "/?page=Approvals&client=northstar-demo&repair_plan=" + record["id"]
    )
    expect(
        page.get_by_role("heading", name="Bounded repair review", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_text("Write-Output 'fictional marker'", exact=False)
    ).to_be_visible()
    page.set_viewport_size({"width": 390, "height": 844})
    button = page.get_by_role("button", name="Reject / revoke plan", exact=True)
    button.focus()
    expect(button).to_be_focused()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    button.click()
    expect(page.get_by_text("rejected", exact=True)).to_be_visible()
    assert (
        app.service.repairs.get(actor, "northstar-demo", record["id"])["state"]
        == "rejected"
    )
