import asyncio
import base64
import io
import json
import re
import shlex
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import asyncssh
import pytest

from remote_fleet_agent.linux_runtime import provision
from remote_fleet_agent.live_app import initialize
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.storage import read_json
from remote_fleet_agent.tools.unix import collect_unix


@pytest.fixture
async def enroll(tmp_path):
    path = initialize(tmp_path / "workspace")
    service = LiveService(path)
    owner = service.store.operator(
        read_json(path / ".state/owner-access.json")["operator_id"]
    )
    service.linux_enrollment.create_client(
        owner, {"client": "test-client", "authorisation_reference": "LAB-APPROVED"}
    )
    yield service, owner
    await service.close()


def unpack(result):
    with ZipFile(io.BytesIO(base64.b64decode(result["zip_base64"]))) as z:
        return {name: z.read(name).decode() for name in z.namelist()}


def receipt(result):
    script = unpack(result)["Connect.sh"]
    values = {
        k: shlex.split(v)[0]
        for k, v in re.findall(r"^(RF_\w+)=(.*)$", script, re.MULTILINE)
        if k in ("RF_KIT", "RF_TOKEN")
    }
    key = asyncssh.generate_private_key("ssh-ed25519")
    return {
        "kit": values["RF_KIT"],
        "token": values["RF_TOKEN"],
        "username": "fleet",
        "host_key": key.export_public_key().decode(),
    }, key


async def test_linux_kit_has_safe_native_bootstrap_and_public_defaults(enroll):
    s, owner = enroll
    kit = await s.linux_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    scripts = unpack(kit)
    assert set(scripts) == {"Connect.sh", "Remove.sh", "READ-ME.txt"}
    assert kit["enrollment"]["platform"] == "linux"
    assert kit["enrollment"]["install_python"] is True
    assert (
        s.linux_enrollment.status(owner, "test-client")["defaults"][
            "python_required_for_connection"
        ]
        is False
    )
    script = scripts["Connect.sh"]
    assert "python3" not in script.lower()
    assert "--cacert" in script and "--insecure" not in script
    assert 'from="127.0.0.1"' in script
    assert "sshd_config" not in script
    assert script.index("sudo ssh-keygen -A") < script.index("sudo systemctl start ssh")
    assert "BEGIN OPENSSH PRIVATE KEY" not in script
    data, _ = receipt(kit)
    assert data["token"] not in json.dumps(
        s.linux_enrollment.status(owner, "test-client")
    )
    if sys.platform != "win32":
        for name in ("Connect.sh", "Remove.sh"):
            process = await asyncio.create_subprocess_exec(
                "/bin/sh", "-n", stdin=asyncio.subprocess.PIPE
            )
            await process.communicate(scripts[name].encode())
            assert process.returncode == 0


async def test_linux_pairing_is_scoped_single_use_and_not_interchangeable_with_mac(
    enroll, monkeypatch
):
    s, owner = enroll
    tech, _ = s.store.add_operator(
        "Engineer", "technician", {"test-client": "*"}, owner
    )
    with pytest.raises(Denied):
        await s.linux_enrollment.kit(
            tech, "test-client", {"controller_address": "127.0.0.1"}
        )
    kit = await s.linux_enrollment.kit(
        owner,
        "test-client",
        {"controller_address": "127.0.0.1", "install_python": False},
    )
    body, _ = receipt(kit)
    connect = AsyncMock()
    monkeypatch.setattr(s.linux_enrollment, "connect", connect)
    with pytest.raises(FileNotFoundError):
        await s.mac_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    with pytest.raises(Denied):
        await s.linux_enrollment.accept(
            kit["enrollment"], {**body, "token": "wrong"}, "127.0.0.1"
        )
    await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    with pytest.raises(Denied):
        await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*s.linux_enrollment.tasks)
    assert connect.call_args.args[3] == "127.0.0.1"


async def test_linux_registers_only_after_pinned_ssh_then_provisions(
    enroll, monkeypatch
):
    s, owner = enroll
    kit = await s.linux_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    body, key = receipt(kit)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Linux\nfleet\naarch64\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )

    async def connect(address, **kwargs):
        assert not s.config("test-client").hosts
        assert kwargs["known_hosts"][0][0].get_fingerprint() == key.get_fingerprint()
        return conn

    async def setup(conn, arch, progress, permission):
        permission()
        assert s.config("test-client").hosts[0].platform == "linux"
        assert (
            s.store.list("linux_enrollment", "test-client")[0]["connection_state"]
            == "connected"
        )
        progress("ready")

    monkeypatch.setattr(asyncssh, "connect", connect)
    monkeypatch.setattr("remote_fleet_agent.linux_runtime.provision", setup)
    await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*list(s.linux_enrollment.tasks))
    assert s.store.list("linux_enrollment", "test-client")[0]["python_state"] == "ready"


async def test_linux_wrong_kernel_never_registers_or_provisions(enroll, monkeypatch):
    s, owner = enroll
    kit = await s.linux_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    body, _ = receipt(kit)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Darwin\nfleet\narm64\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
    setup = AsyncMock()
    monkeypatch.setattr("remote_fleet_agent.linux_runtime.provision", setup)
    await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*list(s.linux_enrollment.tasks))
    assert not s.config("test-client").hosts
    assert not setup.called


async def test_linux_reuses_existing_python_without_downloading(monkeypatch):
    conn = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(exit_status=0)))
    download = AsyncMock()
    monkeypatch.setattr("remote_fleet_agent.linux_runtime.download", download)
    states = []
    await provision(conn, "x86_64", states.append, lambda: None)
    assert states == ["checking", "ready"]
    assert not download.called


async def test_linux_native_checks_work_without_python():
    calls = []

    async def run(command, check=False):
        calls.append(command)
        if "_rf_python" in command:
            return SimpleNamespace(exit_status=127, stdout="", stderr="python3 absent")
        return SimpleNamespace(
            exit_status=0,
            stdout='ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04"\nVERSION_ID="24.04"\n',
            stderr="",
        )

    result = await collect_unix(SimpleNamespace(run=run), "linux", "inventory", ["os"])
    assert result["sections"]["os"]["items"][0]["Caption"] == "Ubuntu 24.04"
    assert len(calls) == 2


def test_archive_resolves_internal_library_links_but_rejects_chained_escape(tmp_path):
    import tarfile

    from remote_fleet_agent.mac_runtime import validate_archive

    def archive(path, links):
        with tarfile.open(path, "w:gz") as out:
            for name, target in links:
                item = tarfile.TarInfo(name)
                item.type = tarfile.SYMTYPE
                item.linkname = target
                out.addfile(item)

    good = tmp_path / "good.tgz"
    archive(
        good, [("python/lib/python3.13/config/libpython.so", "../../libpython3.13.so")]
    )
    validate_archive(good)
    bad = tmp_path / "bad.tgz"
    archive(bad, [("python/a", "."), ("python/a/b", "../../outside")])
    with pytest.raises(ValueError):
        validate_archive(bad)


async def test_linux_unknown_libc_does_not_download_or_write(monkeypatch):
    conn = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                SimpleNamespace(exit_status=1),
                SimpleNamespace(stdout="unknown libc", exit_status=1),
            ]
        )
    )
    download = AsyncMock()
    monkeypatch.setattr("remote_fleet_agent.linux_runtime.download", download)
    states = []
    with pytest.raises(ValueError):
        await provision(conn, "x86_64", states.append, lambda: None)
    assert states == ["checking"]
    assert not download.called


async def test_linux_remote_setup_disconnect_retains_claim(enroll, monkeypatch):
    from remote_fleet_agent.administration import claim_path

    s, owner = enroll
    kit = await s.linux_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    body, key = receipt(kit)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Linux\nfleet\nx86_64\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))

    async def interrupted(conn, arch, progress, permission):
        progress("installing")
        raise ConnectionError("lost after dispatch")

    monkeypatch.setattr("remote_fleet_agent.linux_runtime.provision", interrupted)
    await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*list(s.linux_enrollment.tasks))
    record = s.store.list("linux_enrollment", "test-client")[0]
    assert record["connection_state"] == "connected"
    assert record["python_state"] == "unknown"
    assert claim_path(
        {
            "target": "fleet@127.0.0.1",
            "port": 22,
            "host_keys": [key.get_fingerprint()],
            "device_id": record["device_id"],
            "simulation": False,
        }
    ).exists()


async def test_other_linux_architecture_stays_connected_when_runtime_unavailable(
    enroll, monkeypatch
):
    s, owner = enroll
    kit = await s.linux_enrollment.kit(
        owner, "test-client", {"controller_address": "127.0.0.1"}
    )
    body, _ = receipt(kit)
    conn = SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(stdout="Linux\nfleet\narmv7l\n")),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    monkeypatch.setattr(asyncssh, "connect", AsyncMock(return_value=conn))
    monkeypatch.setattr(
        "remote_fleet_agent.linux_runtime.provision",
        AsyncMock(side_effect=ValueError("No private runtime")),
    )
    await s.linux_enrollment.accept(kit["enrollment"], body, "127.0.0.1")
    await asyncio.gather(*list(s.linux_enrollment.tasks))
    record = s.store.list("linux_enrollment", "test-client")[0]
    assert record["connection_state"] == "connected"
    assert record["python_state"] == "failed"
    assert s.config("test-client").hosts[0].platform == "linux"
