from zipfile import ZipFile

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect


def test_owner_adds_mac_client_downloads_kit_and_revokes_it(
    page, workspace, signed_in, tmp_path
):
    app, _, _ = workspace
    page.goto(app.url + "/?page=Settings")
    page.get_by_role("button", name="Add authorized client").click()
    page.get_by_label("Client ID").fill("mac-enrollment-lab")
    page.get_by_label("Authorization reference").fill("FICTIONAL-ENROLLMENT-TEST")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.get_by_role("button", name="Create Mac connection kit")).to_be_visible()
    page.get_by_role("button", name="Create Mac connection kit").click()
    page.get_by_label("Controller private IPv4").fill("127.0.0.1")
    expect(page.get_by_label("Set up Python after SSH is connected")).to_be_checked()
    page.get_by_label("Set up Python after SSH is connected").uncheck()
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="Save", exact=True).click()
    output = tmp_path / "private-kit.zip"
    downloaded.value.save_as(output)
    with ZipFile(output) as archive:
        assert "Connect.command" in archive.namelist()
        assert (
            "BEGIN OPENSSH PRIVATE KEY" not in archive.read("Connect.command").decode()
        )
    expect(page.get_by_role("button", name="Revoke unused kit")).to_be_visible()
    page.get_by_role("button", name="Revoke unused kit").click()
    expect(page.get_by_text("SSH: Revoked", exact=False)).to_be_visible()


def test_simulated_client_cannot_generate_real_mac_access(
    page, workspace_url, signed_in
):
    page.goto(workspace_url + "/?page=Settings&client=northstar-demo")
    expect(page.get_by_role("button", name="Add authorized client")).to_be_visible()
    expect(page.get_by_role("button", name="Create Mac connection kit")).to_have_count(
        0
    )


def test_owner_can_generate_linux_connection_kit(page, workspace, signed_in, tmp_path):
    app, _, _ = workspace
    page.goto(app.url + "/?page=Settings")
    page.get_by_role("button", name="Add authorized client").click()
    page.get_by_label("Client ID").fill("linux-enrollment-lab")
    page.get_by_label("Authorization reference").fill("FICTIONAL-LINUX-TEST")
    page.get_by_role("button", name="Save", exact=True).click()
    page.get_by_role("button", name="Create Linux connection kit").click()
    expect(page.get_by_label("Set up Python after SSH is connected")).to_be_checked()
    page.get_by_label("Controller private IPv4").fill("127.0.0.1")
    with page.expect_download() as download:
        page.get_by_role("button", name="Save", exact=True).click()
    output = tmp_path / "private-linux-kit.zip"
    download.value.save_as(output)
    with ZipFile(output) as archive:
        assert "Connect.sh" in archive.namelist()
        assert "Connect.command" not in archive.namelist()
    expect(page.get_by_text("Waiting for Linux", exact=True)).to_be_visible()
    page.get_by_role("button", name="Revoke unused kit").click()
    expect(page.get_by_text("Linux · SSH: Revoked", exact=False)).to_be_visible()
