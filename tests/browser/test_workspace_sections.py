import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_primary_navigation_has_five_sections(page, signed_in):
    nav = page.get_by_role("navigation")
    expect(nav.get_by_role("button")).to_have_count(5)
    for name in ("Operations", "Devices", "Approvals", "History", "Settings"):
        expect(nav.get_by_role("button", name=name, exact=True)).to_be_visible()
    expect(nav.get_by_role("button", name="Evidence", exact=True)).to_have_count(0)


@pytest.mark.parametrize(
    "destination,heading",
    [
        ("Overview", "A clear view of your fleet"),
        ("Fleet", "Device inventory"),
        ("Evidence", "Evidence & provenance"),
        ("Jobs", "Collection history"),
        ("Follow-ups", "Follow-up queue"),
        ("Compare", "What changed?"),
        ("Onboarding", "Access & onboarding"),
        ("Schedules", "Recurring collections"),
        ("Reports", "Client handover"),
        ("Activity", "Workspace activity"),
        ("Operators", "People & access"),
    ],
)
def test_legacy_destinations_still_open(
    page, workspace_url, signed_in, destination, heading
):
    page.goto(workspace_url + "/?client=northstar-demo&page=" + destination)
    expect(
        page.get_by_role("heading", name=heading, exact=True, level=1)
    ).to_be_visible()
    expect(page.get_by_role("navigation").get_by_role("button")).to_have_count(5)
