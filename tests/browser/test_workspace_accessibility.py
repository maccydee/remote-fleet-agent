import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


@pytest.mark.parametrize("width,height", [(1440, 900), (390, 844)])
def test_narrow_workspace_has_no_document_overflow(
    page, workspace_url, signed_in, width, height
):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(workspace_url)
    expect(page.get_by_role("heading", name="Operations", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_label("Search work").focus()
    expect(page.get_by_label("Search work")).to_be_focused()
    page.keyboard.press("Tab")
    expect(page.get_by_label("State", exact=True)).to_be_focused()
