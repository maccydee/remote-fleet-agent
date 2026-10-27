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
    assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    assert (stage_dir / "id_ed25519.pub").exists()
    assert not (stage_dir / "id_ed25519").exists()  # private key must never be staged to the USB


def test_init_client_refuses_to_overwrite(tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", keys_dir)
    stage_dir = tmp_path / "usb"
    stage_dir.mkdir()
    init_client("acme", stage_dir=stage_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        init_client("acme", stage_dir=stage_dir)
