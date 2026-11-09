import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_device_summary_leads_with_investigation(page, workspace_url, signed_in):
    page.goto(workspace_url + "/?client=northstar-demo&device=windows-01")
    expect(page.get_by_role("heading", name="windows-01", exact=True)).to_be_visible()
    expect(
        page.get_by_role("button", name="Start investigation", exact=True)
    ).to_be_visible()
    for tab in ("Summary", "Support history", "Inventory", "Evidence", "Access"):
        expect(page.get_by_role("tab", name=tab, exact=True)).to_be_visible()
    expect(
        page.get_by_role("heading", name="Evidence freshness", exact=True)
    ).to_have_count(0)
    expect(page.get_by_label("Client tools")).to_have_count(0)
    page.get_by_role("button", name="Start investigation", exact=True).click()
    expect(
        page.get_by_role("heading", name="Confirm investigation targets", exact=True)
    ).to_be_visible()
    expect(
        page.get_by_role("button", name="Confirm named devices", exact=True)
    ).to_be_visible()


def test_device_tabs_preserve_exact_context_and_history(page, workspace_url, signed_in):
    page.goto(workspace_url + "/?page=Fleet&client=harbour-demo&device=windows-01")
    page.get_by_role("tab", name="Support history", exact=True).click()
    expect(page.get_by_text("Harbour test failure", exact=True)).to_be_visible()
    expect(
        page.get_by_text("Northstar Outlook will not open", exact=True)
    ).to_have_count(0)
    page.get_by_role("tab", name="Access", exact=True).click()
    expect(
        page.locator("#device-tab-content").get_by_text(
            "demo@windows-01.invalid", exact=False
        )
    ).to_be_visible()
    page.reload()
    expect(page.get_by_role("tab", name="Access", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )
    page.go_back()
    expect(
        page.get_by_role("tab", name="Support history", exact=True)
    ).to_have_attribute("aria-selected", "true")
    page.get_by_role("link", name="Open case", exact=True).click()
    expect(page.get_by_test_id("case-client")).to_have_text("harbour-demo")


def test_platform_and_engineer_filters_survive_reload(page, workspace_url, signed_in):
    page.get_by_label("Platform", exact=True).select_option("windows")
    page.get_by_label("Engineer", exact=True).select_option("unassigned")
    page.get_by_role("button", name="Search", exact=True).click()
    page.reload()
    expect(page.get_by_label("Platform", exact=True)).to_have_value("windows")
    expect(page.get_by_label("Engineer", exact=True)).to_have_value("unassigned")
    assert "engineer=unassigned" in page.url
    page.get_by_role("navigation").get_by_role(
        "button", name="Devices", exact=True
    ).click()
    expect(page.get_by_label("Search devices", exact=True)).to_be_visible()
    page.get_by_label("Platform", exact=True).select_option("macos")
    page.get_by_role("button", name="Search", exact=True).click()
    expect(page.get_by_role("row").filter(has_text="macos-02").first).to_be_visible()
    expect(page.get_by_role("row").filter(has_text="windows-01")).to_have_count(0)
