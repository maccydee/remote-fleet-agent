"""One real fictional support cycle; recordings are explicit and remain private."""

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect

from tests.test_support import finish, proposal


def test_fictional_operations_approval_and_results(page, context, workspace, signed_in):
    app, token, cases = workspace
    actor = app.service.store.authenticate(token)
    original = cases[0]

    async def arrange():
        case = app.service.support.save(
            actor,
            "northstar-demo",
            original,
            {"symptom": "Northstar printer queue is stuck", "profile": "printing"},
        )
        app.service.support.investigate(
            actor, "northstar-demo", case["id"], {"revision": case["revision"]}
        )
        await finish(app.service)
        case = app.service.support.get(actor, "northstar-demo", original["id"])
        return app.service.support.propose(
            actor, "northstar-demo", case["id"], proposal(case)
        )

    record = app.call(arrange())
    capture = getattr(context, "fleet_capture", None)

    def shot(scene):
        if capture:
            capture.screenshot(page, scene)
            page.wait_for_timeout(1800)

    page.goto(app.url)
    expect(
        page.get_by_text("Northstar printer queue is stuck", exact=True)
    ).to_be_visible()
    shot("operations-across-fictional-clients")
    page.get_by_label("Client", exact=True).select_option("northstar-demo")
    expect(page.get_by_text("Harbour test failure", exact=True)).to_have_count(0)
    shot("filtered-engineer-attention")
    page.get_by_role("navigation").get_by_role(
        "button", name="Devices", exact=True
    ).click()
    expect(page.get_by_role("heading", name="Devices", exact=True)).to_be_visible()
    expect(page.get_by_role("link", name="Open device").first).to_be_visible()
    shot("cross-platform-device-search")
    page.get_by_role("row").filter(has_text="windows-01").get_by_role(
        "link", name="Open device"
    ).click()
    expect(page.locator("#title")).to_have_text("windows-01")
    expect(
        page.get_by_role("button", name="Start investigation", exact=True)
    ).to_be_visible()
    expect(
        page.locator("#device-tab-content").get_by_text(
            "Northstar printer queue is stuck", exact=True
        )
    ).to_be_visible()
    shot("device-summary-and-support-context")
    page.get_by_role("tab", name="Access", exact=True).click()
    expect(
        page.get_by_role("heading", name="Access and identity", exact=True)
    ).to_be_visible()
    shot("device-access-and-identity")
    page.goto(app.url + "/?client=northstar-demo&support_case=" + record["id"])
    expect(
        page.get_by_role("button", name="Approve this exact repair", exact=True)
    ).to_be_visible()
    page.get_by_role(
        "heading", name="Repair review", exact=True
    ).scroll_into_view_if_needed()
    shot("engineer-exact-repair-review")
    page.get_by_role("button", name="Approve this exact repair", exact=True).click()
    expect(
        page.get_by_role("button", name="Execute approved repair", exact=True)
    ).to_be_visible()
    shot("human-approved-simulated-repair")
    page.get_by_role("button", name="Execute approved repair", exact=True).click()
    page.get_by_role("button", name="Refresh case", exact=True).click()
    expect(
        page.get_by_text(
            "Checks passed. Issue confirmation remains pending", exact=False
        )
    ).to_be_visible(timeout=10000)
    shot("checks-passed-user-confirmation-pending")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot("narrow-screen-case-timeline")
