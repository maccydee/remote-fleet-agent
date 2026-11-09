import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_default_navigation(page, workspace_url, signed_in):
    page.goto(workspace_url)
    expect(page.get_by_role("heading", name="Operations", exact=True)).to_be_visible()
    expect(page.get_by_label("Client", exact=True)).to_have_value("")
    expect(
        page.get_by_role("navigation").get_by_role("button", name="Devices", exact=True)
    ).to_be_visible()
