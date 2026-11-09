import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_operation_opens_exact_client_case(page, workspace_url, signed_in):
    page.goto(workspace_url)
    page.get_by_role("row").filter(has_text="Harbour test failure").get_by_role(
        "link", name="Open case"
    ).click()
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")
    expect(page.get_by_role("heading", name="Activity", exact=True)).to_be_visible()
    page.reload()
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")
    page.go_back()
    expect(page.get_by_role("heading", name="Operations", exact=True)).to_be_visible()


def test_search_empty_and_clear(page, workspace_url, signed_in):
    page.get_by_label("Search work").fill("no-such-fictional-issue")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_text("No matching work.", exact=False)).to_be_visible()
    page.get_by_role("button", name="Clear filters", exact=True).click()
    expect(page.get_by_text("Harbour test failure", exact=True)).to_be_visible()


def test_case_race_never_overwrites_current_target(page, workspace, signed_in):
    app, _, cases = workspace
    a, b = cases
    held = []
    page.route("**/support-cases/" + a["id"], lambda route: held.append(route))
    page.goto(app.url + "/?client=northstar-demo&support_case=" + a["id"])
    page.wait_for_function("window.FleetOperations !== undefined")
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "Old case read must be in flight before navigating"
    page.evaluate(
        "(id)=>goRoute({page:'Case',client:'harbour-demo',id,site:''})", b["id"]
    )
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")
    assert held
    held[0].continue_()
    page.wait_for_timeout(150)
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")
    expect(
        page.get_by_role("heading", name="Harbour test failure", exact=True)
    ).to_be_visible()


def test_hostile_issue_is_plain_text(page, workspace, signed_in):
    app, token, _ = workspace
    owner = app.service.store.authenticate(token)
    app.service.support.create(
        owner,
        "northstar-demo",
        {
            "host": "windows-01",
            "symptom": "<img src=x onerror=alert(1)>",
            "profile": "general",
        },
    )
    page.reload()
    expect(page.get_by_text("<img src=x onerror=alert(1)>", exact=True)).to_be_visible()
    expect(page.locator("#view img")).to_have_count(0)
