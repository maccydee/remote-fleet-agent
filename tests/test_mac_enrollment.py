import asyncio
import base64
import io
import json
import plistlib
import re
import shlex
import ssl
import subprocess
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import asyncssh
import pytest

from remote_fleet_agent.live_app import initialize
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.mac_enrollment import private_ipv4
from remote_fleet_agent.mac_runtime import validate_archive
from remote_fleet_agent.storage import read_json, save_json
from remote_fleet_agent.tools.macos_native import parse
from remote_fleet_agent.tools.unix import build_command, collect_unix


@pytest.fixture
async def enroll(tmp_path):
    path = initialize(tmp_path / "workspace")
    service = LiveService(path)
    owner = service.store.operator(
        read_json(path / ".state/owner-access.json")["operator_id"]
    )
    service.mac_enrollment.create_client(
        owner, {"client": "test-client", "authorisation_reference": "LAB-APPROVED"}
    )
    yield service, owner
    await service.close()


def contents(result):
    with ZipFile(io.BytesIO(base64.b64decode(result["zip_base64"]))) as archive:
        return {n: archive.read(n).decode() for n in archive.namelist()}


def receipt(result):
    script = contents(result)["Connect.command"]
    values = {
        k: shlex.split(v)[0]
        for k, v in re.findall(r"^(RF_\w+)=(.*)$", script, re.MULTILINE)
        if k in ("RF_KIT", "RF_TOKEN", "RF_URL")
    }
    key = asyncssh.generate_private_key("ssh-ed25519")
    return (
        {
            "kit": values["RF_KIT"],
            "token": values["RF_TOKEN"],
            "username": "engineer",
            "host_key": key.export_public_key().decode(),
        },
        key,
        script,
        values["RF_URL"],
    )


async def make_kit(service, actor, **overrides):
    return await service.mac_enrollment.kit(
        actor,
        "test-client",
        {"controller_address": "127.0.0.1", "install_python": False, **overrides},
    )


async def test_kit_is_native_private_executable_and_no_controller_secrets(enroll):
    s, owner = enroll
    result = await make_kit(s, owner)
    body, _, script, _ = receipt(result)
    assert "BEGIN OPENSSH PRIVATE KEY" not in script
    assert "BEGIN EC PRIVATE KEY" not in script
    assert "python3" not in script
    assert "--cacert" in script and "--insecure" not in script
    assert 'from="127.0.0.1"' in script
    assert body["token"] not in json.dumps(
        s.mac_enrollment.status(owner, "test-client")
    )
    assert (
        body["token"]
        not in s.store.db.execute(
            "SELECT data FROM records WHERE kind='mac_enrollment'"
        ).fetchone()[0]
    )
    with ZipFile(io.BytesIO(base64.b64decode(result["zip_base64"]))) as archive:
        assert archive.getinfo("Connect.command").external_attr >> 16 & 0o100
    if sys.platform != "win32":
        for text in (script, contents(result)["Remove.command"]):
            await asyncio.to_thread(
                subprocess.run, ["/bin/sh", "-n"], input=text, text=True, check=True
            )


async def test_roles_simulation_and_audit_only_are_enforced(enroll, monkeypatch):
    s, owner = enroll
    for role in ("manager", "technician", "observer"):
        actor, _ = s.store.add_operator(role, role, {"test-client": "*"}, owner)
        with pytest.raises(Denied):
            await make_kit(s, actor)
    entry = s.descriptor("test-client")
    config = read_json(entry["config"])
    save_json(entry["config"], {**config, "simulation": True})
    with pytest.raises(Denied):
        await make_kit(s, owner)
    save_json(entry["config"], config)
    monkeypatch.setenv("REMOTE_FLEET_AGENT_AUDIT_ONLY", "1")
    with pytest.raises(Denied):
        await make_kit(s, owner)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "0.0.0.0", "224.0.0.1", "169.254.1.2", "::1", "localhost"]
)
def test_listener_addresses_are_private_ipv4(address):
    with pytest.raises(ValueError):
        private_ipv4(address)


async def test_receipt_is_one_use_peer_bound_and_policy_checked(enroll, monkeypatch):
    s, owner = enroll
    result = await make_kit(s, owner)
    body, _, _, _ = receipt(result)
    record = s.store.get("mac_enrollment", result["enrollment"]["id"], "test-client")
    connect = AsyncMock()
    monkeypatch.setattr(s.mac_enrollment, "connect", connect)
    with pytest.raises(Denied):
        await s.mac_enrollment.accept(record, {**body, "token": "wrong"}, "127.0.0.1")
    with pytest.raises(ValueError):
        await s.mac_enrollment.accept(record, body, "8.8.8.8")
    with pytest.raises(ValueError):
        await s.mac_enrollment.accept(
            record, {**body, "username": "a; touch /tmp/no"}, "127.0.0.1"
        )
    await s.mac_enrollment.accept(record, {**body, "address": "8.8.8.8"}, "127.0.0.1")
    with pytest.raises(Denied):
        await s.mac_enrollment.accept(record, body, "127.0.0.1")
    await asyncio.sleep(0)
    assert connect.call_args.args[3] == "127.0.0.1"
    assert not s.config("test-client").hosts


async def test_expiry_revocation_and_changed_authorization(enroll):
    s, owner = enroll
    for mode in ("expired", "revoked", "policy"):
        result = await make_kit(s, owner)
        body, _, _, _ = receipt(result)
        record = s.store.get(
            "mac_enrollment", result["enrollment"]["id"], "test-client"
        )
        if mode == "expired":
            s.mac_enrollment.update(
                record,
                expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            )
        elif mode == "revoked":
            await s.mac_enrollment.revoke(owner, "test-client", record["id"])
        else:
            catalog = read_json(s.directory / "workspace.json")
            catalog["clients"][0]["authorisation_reference"] = "CHANGED"
            save_json(s.directory / "workspace.json", catalog)
        with pytest.raises(Denied):
            await s.mac_enrollment.accept(record, body, "127.0.0.1")


async def test_tls_listener_uses_certificate_pin_and_rejects_workspace_routes(
    enroll, monkeypatch
):
    s, owner = enroll
    result = await make_kit(s, owner)
    body, _, script, url = receipt(result)
    monkeypatch.setattr(s.mac_enrollment, "connect", AsyncMock())
    pem = re.search(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", script, re.DOTALL
    )[0]
    ctx = ssl.create_default_context(cadata=pem)
    port = int(url.split(":")[2].split("/")[0])

    async def request(path, payload):
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
        data = json.dumps(payload).encode()
        writer.write(
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode()
            + data
        )
        await writer.drain()
        result = await reader.read()
        writer.close()
        await writer.wait_closed()
        return result

    assert b"400 Bad Request" in await request("/api/operators", body)
    assert b"202 Accepted" in await request("/enroll", body)


async def test_python_only_after_verified_ssh_and_saved_device(enroll, monkeypatch):
    s, owner = enroll
    result = await make_kit(s, owner, install_python=True)
    body, key, _, _ = receipt(result)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Darwin\nengineer\narm64\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )

    async def connect(address, **kwargs):
        assert not s.config("test-client").hosts
        assert kwargs["known_hosts"][0][0].get_fingerprint() == key.get_fingerprint()
        assert kwargs["config"] is None
        return conn

    async def provision(connection, architecture, progress, permission):
        permission()
        record = s.store.list("mac_enrollment", "test-client")[0]
        assert record["connection_state"] == "connected"
        assert s.config("test-client").hosts[0].platform == "macos"
        assert (
            key.export_public_key().decode().strip()
            in s.config("test-client").known_hosts.read_text()
        )
        progress("downloading")
        raise ValueError("secret remote details never exposed")

    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr("remote_fleet_agent.mac_runtime.provision", provision)
    record = s.store.get("mac_enrollment", result["enrollment"]["id"], "test-client")
    await s.mac_enrollment.accept(record, body, "127.0.0.1")
    await asyncio.gather(*list(s.mac_enrollment.tasks))
    record = s.store.list("mac_enrollment", "test-client")[0]
    assert record["connection_state"] == "connected"
    assert record["python_state"] == "failed"
    assert "secret remote" not in json.dumps(record)


async def test_failed_ssh_never_registers_device_or_starts_python(enroll, monkeypatch):
    s, owner = enroll
    result = await make_kit(s, owner, install_python=True)
    body, _, _, _ = receipt(result)
    provision = AsyncMock()
    monkeypatch.setattr(
        asyncssh, "connect", AsyncMock(side_effect=ValueError("host key mismatch"))
    )
    monkeypatch.setattr("remote_fleet_agent.mac_runtime.provision", provision)
    record = s.store.get("mac_enrollment", result["enrollment"]["id"], "test-client")
    await s.mac_enrollment.accept(record, body, "127.0.0.1")
    await asyncio.gather(*list(s.mac_enrollment.tasks))
    assert not s.config("test-client").hosts
    assert not provision.called
    assert (
        s.store.list("mac_enrollment", "test-client")[0]["connection_state"] == "failed"
    )


@pytest.mark.parametrize(
    "name,link",
    [
        ("../evil", ""),
        ("/tmp/evil", ""),
        ("python/evil", "../../evil"),
        ("python/link", "/tmp/evil"),
    ],
)
def test_runtime_archive_rejects_escaping_members(tmp_path, name, link):
    path = tmp_path / "runtime.tgz"
    with tarfile.open(path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = link
        archive.addfile(member)
    with pytest.raises(ValueError):
        validate_archive(path)


def test_native_parser_keeps_unknowns_and_readiness_shape():
    assert parse("antivirus_products", "")["status"] == "unknown"
    assert parse("permissions", "501\n")["items"][0]["uid"] == 501
    assert (
        parse(
            "disk_space",
            "Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/disk 1000 100 900 10% /\n",
        )["items"][0]["free_bytes"]
        == 921600
    )
    assert (
        parse("disks", plistlib.dumps({"VolumeName": "Macintosh HD"}).decode())[
            "items"
        ][0]["VolumeName"]
        == "Macintosh HD"
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Mac read-only commands")
async def test_local_mac_evidence_needs_no_python():
    class Native:
        async def run(self, command, check=False):
            assert command == build_command(
                "macos", "readiness", "permissions"
            ) or command == build_command("macos", "inventory", "os")
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            out, err = await proc.communicate()
            return SimpleNamespace(
                stdout=out.decode(), stderr=err.decode(), exit_status=proc.returncode
            )

    for operation, section in [("inventory", "os"), ("readiness", "permissions")]:
        result = await collect_unix(Native(), "macos", operation, [section])
        assert result["sections"][section]["status"] == "collected"


async def test_controller_restart_revokes_unconsumed_kits(enroll):
    from remote_fleet_agent.mac_enrollment import MacEnrollment

    s, owner = enroll
    result = await make_kit(s, owner)
    # A fresh service cannot retain the old listener/certificate context.
    recovered = MacEnrollment(s)
    assert (
        recovered.status(owner, "test-client")["devices"][0]["connection_state"]
        == "revoked"
    )
    body, _, _, _ = receipt(result)
    with pytest.raises(Denied):
        await recovered.accept(result["enrollment"], body, "127.0.0.1")
    await recovered.close()


async def test_remote_install_interruption_retains_device_claim(enroll, monkeypatch):
    from remote_fleet_agent.administration import claim_path

    s, owner = enroll
    result = await make_kit(s, owner, install_python=True)
    body, key, _, _ = receipt(result)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Darwin\nengineer\narm64\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

    async def interrupted(conn, arch, progress, permission):
        progress("installing")
        raise ConnectionError("lost after dispatch")

    monkeypatch.setattr("remote_fleet_agent.mac_runtime.provision", interrupted)
    await s.mac_enrollment.accept(result["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*list(s.mac_enrollment.tasks))
    record = s.store.list("mac_enrollment", "test-client")[0]
    assert record["connection_state"] == "connected"
    assert record["python_state"] == "unknown"
    path = claim_path(
        {
            "target": "engineer@127.0.0.1",
            "port": 22,
            "host_keys": [key.get_fingerprint()],
            "device_id": record["device_id"],
            "simulation": False,
        }
    )
    assert path.exists()
    assert read_json(path)["active"] is False


@pytest.mark.skipif(
    sys.platform != "darwin"
    or __import__("os").environ.get("REMOTE_FLEET_TEST_MAC_RUNTIME") != "1",
    reason="Opt-in real runtime download and isolated local installation",
)
async def test_real_private_runtime_in_scratch_home(tmp_path):
    """Exercise real Mac installation in a temporary HOME without enabling SSH."""
    import os
    import shutil

    from remote_fleet_agent.mac_runtime import provision

    class SFTP:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def put(self, source, destination):
            await asyncio.to_thread(shutil.copyfile, source, destination)

    class LocalTransport:
        def start_sftp_client(self):
            return SFTP()

        async def run(self, command, input=None, check=False):
            env = {**os.environ, "HOME": str(tmp_path)}
            p = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await p.communicate(input.encode() if input else None)
            if check and p.returncode:
                raise RuntimeError(err.decode())
            return SimpleNamespace(
                stdout=out.decode(), stderr=err.decode(), exit_status=p.returncode
            )

    states = []
    await provision(
        LocalTransport(),
        "arm64" if __import__("platform").machine() == "arm64" else "x86_64",
        states.append,
        lambda: None,
    )
    assert states == ["downloading", "uploading", "installing", "ready"]
    assert (tmp_path / ".remote-fleet-agent/python-3.13.18/bin/python3").exists()


async def test_public_mac_setup_defaults_to_automatic_pairing_then_python(enroll):
    s, owner = enroll
    state = s.mac_enrollment.status(owner, "test-client")
    assert state["defaults"] == {
        "method": "connection_kit",
        "identity": "automatic",
        "python_required_for_connection": False,
        "install_python": True,
        "python_install_stage": "after_verified_ssh",
    }
    # Omit the option as a public API caller would: Python follows SSH by default.
    result = await s.mac_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    assert result["enrollment"]["install_python"] is True
    assert result["enrollment"]["connection_state"] == "waiting"
    assert result["enrollment"]["python_state"] == "pending"
