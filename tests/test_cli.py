import os
import stat

import pytest

from remote_fleet_agent.cli import init_client


def test_init_client_generates_key_and_stages_pubkey(tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", keys_dir)
    stage_dir = tmp_path / "usb"
    stage_dir.mkdir()

    init_client("acme", stage_dir=stage_dir)

    priv = keys_dir / "acme" / "id_ed25519"
    pub = keys_dir / "acme" / "id_ed25519.pub"
    assert priv.exists() and pub.exists()
    if os.name != "nt":
        assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    assert (stage_dir / "id_ed25519.pub").exists()
    assert not (
        stage_dir / "id_ed25519"
    ).exists()  # private key must never be staged to the USB


def test_init_client_refuses_to_overwrite(tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", keys_dir)
    stage_dir = tmp_path / "usb"
    stage_dir.mkdir()
    init_client("acme", stage_dir=stage_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        init_client("acme", stage_dir=stage_dir)


def test_force_restages_without_changing_existing_key(tmp_path, monkeypatch):
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    stage = tmp_path / "usb"
    init_client("acme", stage_dir=stage)
    key = tmp_path / "keys/acme/id_ed25519"
    original = key.read_bytes()
    init_client("acme", stage_dir=stage, force=True)
    assert key.read_bytes() == original
    for name in ("Install-Assessment", "Remove-Assessment", "Protect-Assessment"):
        assert (stage / (name + ".ps1")).read_bytes() == (
            stage / (name + ".txt")
        ).read_bytes()
    assert '-Engagement "acme"' in (stage / "INSTALL-COMMAND.txt").read_text()
    assert not (stage / "install.ps1").exists()


@pytest.mark.parametrize("name", ["../escape", "UPPER", "a/b", "", "a" * 49])
def test_invalid_client_never_generates_key(tmp_path, monkeypatch, name):
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    with pytest.raises(ValueError, match="client"):
        init_client(name, stage_dir=tmp_path / "usb")
    assert not (tmp_path / "keys").exists()


def test_key_directory_cannot_be_used_for_staging(tmp_path, monkeypatch):
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    with pytest.raises(ValueError, match="separate"):
        init_client("acme", stage_dir=tmp_path / "keys/acme")


def test_staged_command_can_include_valid_engineer_ip(tmp_path, monkeypatch):
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    stage = tmp_path / "usb"
    init_client("acme", stage_dir=stage, allowed_ip="192.0.2.10")
    assert '-AllowedIP "192.0.2.10"' in (stage / "INSTALL-COMMAND.txt").read_text()


@pytest.mark.parametrize("address", ["0.0.0.0/0", "::1", "999.0.0.1"])
def test_staging_rejects_invalid_engineer_ip_before_key_creation(
    tmp_path, monkeypatch, address
):
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    with pytest.raises(ValueError):
        init_client("acme", stage_dir=tmp_path / "usb", allowed_ip=address)
    assert not (tmp_path / "keys").exists()
