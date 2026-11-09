import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_unknown_case_has_no_blind_retry(page, workspace, signed_in):
    app, token, cases = workspace
    c = cases[0]
    actor = app.service.store.authenticate(token)
    app.service.support.save(
        actor,
        "northstar-demo",
        c,
        {"state": "unknown", "error": "SSH outcome uncertain"},
    )
    page.goto(app.url + "/?client=northstar-demo&support_case=" + c["id"])
    expect(page.get_by_text("Outcome unknown", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Retry repair", exact=True)).to_have_count(0)
    expect(page.get_by_role("heading", name="Activity", exact=True)).to_be_visible()
