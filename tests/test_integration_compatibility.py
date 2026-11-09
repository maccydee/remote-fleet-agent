import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from remote_fleet_agent import host_platform
from remote_fleet_agent.integration import (
    PROVIDERS,
    diagnose,
    launch_spec,
    probe,
    render,
)
from remote_fleet_agent.live_app import seed_demo
from remote_fleet_agent.storage import atomic_text, private_dir


@pytest.mark.parametrize(
    "system,python,root",
    [
        (
            "Windows",
            r"C:\Program Files\Fleet ü\Scripts\python.exe",
            r"C:\Users\Engineer\Fleet ü",
        ),
        (
            "Darwin",
            "/Users/Engineer/Fleet ü/.venv/bin/python",
            "/Users/Engineer/Fleet ü",
        ),
        ("Linux", "/home/engineer/Fleet ü/.venv/bin/python", "/home/engineer/Fleet ü"),
    ],
)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_all_client_formats_round_trip_native_paths(system, python, root, provider):
    separator = "\\" if system == "Windows" else "/"
    spec = launch_spec(
        python,
        root + separator + "fleet.json",
        root + separator + "id_ed25519",
        root + separator + "jobs",
        system=system,
    )
    _, body = render(provider, spec)
    if provider == "codex":
        entry = tomllib.loads(body)["mcp_servers"]["remote-fleet"]
    elif provider == "generic":
        entry = json.loads(body)
    else:
        entry = json.loads(body)["servers" if provider == "vscode" else "mcpServers"][
            "remote-fleet"
        ]
    assert entry["command"] == python and entry["args"] == spec["args"]
    assert "--enable-maintenance" not in entry["args"]
    assert "trust" not in entry or entry["trust"] is False
    assert "shell" not in entry and "autoApprove" not in entry


def test_no_relative_or_control_character_launch_paths():
    with pytest.raises(ValueError):
        launch_spec("python", "fleet.json", system="Linux")
    with pytest.raises(ValueError):
        launch_spec("/bin/python", "/tmp/one\nmalicious", system="Linux")
    with pytest.raises(ValueError):
        launch_spec("C:python.exe", r"C:\fleet.json", system="Windows")


def test_windows_process_check_never_signals_a_process(monkeypatch):
    monkeypatch.setattr(host_platform.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host_platform, "_windows_process_exists", lambda pid: pid == 42)
    monkeypatch.setattr(
        host_platform.os,
        "kill",
        lambda *_: pytest.fail("Windows must never use os.kill(pid,0)"),
    )
    assert host_platform.process_exists(42) and not host_platform.process_exists(41)


def test_native_process_liveness_is_safe():
    assert host_platform.process_exists(os.getpid())
    assert not host_platform.process_exists(2147483647)
    assert not host_platform.process_exists(-1)
    assert not host_platform.process_exists(None)


def test_posix_permissions_and_windows_acl_routing(tmp_path, monkeypatch):
    if os.name != "nt":
        folder = private_dir(tmp_path / "private")
        atomic_text(folder / "secret", "sample")
        assert folder.stat().st_mode & 0o777 == 0o700
        assert (folder / "secret").stat().st_mode & 0o777 == 0o600
    calls = []
    monkeypatch.setattr(host_platform.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        host_platform,
        "_windows_restrict",
        lambda path, directory: calls.append((path, directory)),
    )
    target = tmp_path / "routing"
    target.write_text("fixture")
    host_platform.restrict_private(target)
    assert calls == [(target, False)]


@pytest.mark.skipif(
    os.name != "nt", reason="Native Windows ACL verification runs in the Windows CI job"
)
def test_native_windows_private_storage_dacl(tmp_path):
    folder = private_dir(tmp_path / "private ü space")
    atomic_text(folder / "secret.json", "{}")
    for path in (folder, folder / "secret.json"):
        acl = windows_dacl(path)
        from remote_fleet_agent.host_platform import _windows_sid

        # Numeric SIDs avoid machine-specific aliases such as LA (local admin).
        assert acl["protected"]
        entries = acl["entries"]
        assert len(entries) == 2
        assert {entry["sid"] for entry in entries} == {_windows_sid(), "S-1-5-18"}
        expected_flags = 3 if path.is_dir() else 0  # OBJECT/CONTAINER_INHERIT
        assert all(entry["flags"] == expected_flags for entry in entries)
        assert all(entry["mask"] == 0x1F01FF for entry in entries)  # FILE_ALL_ACCESS


def windows_dacl(path):
    """Read actual ACEs independently of the setter and SDDL account aliases."""
    import ctypes
    from ctypes import wintypes

    class AclSize(ctypes.Structure):
        _fields_ = [
            ("count", wintypes.DWORD),
            ("used", wintypes.DWORD),
            ("free", wintypes.DWORD),
        ]

    class AllowedAce(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_ubyte),
            ("flags", ctypes.c_ubyte),
            ("size", wintypes.WORD),
            ("mask", wintypes.DWORD),
            ("sid", wintypes.DWORD),
        ]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.WORD),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetAce.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    status = advapi.GetNamedSecurityInfoW(
        str(path.resolve()),
        1,
        4,
        None,
        None,
        ctypes.byref(dacl),
        None,
        ctypes.byref(descriptor),
    )
    if status:
        raise ctypes.WinError(status)
    try:
        assert dacl.value, "A NULL DACL would permit unrestricted access"
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(
            descriptor, ctypes.byref(control), ctypes.byref(revision)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        size = AclSize()
        if not advapi.GetAclInformation(
            dacl, ctypes.byref(size), ctypes.sizeof(size), 2
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        entries = []
        for index in range(size.count):
            pointer = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(pointer)):
                raise ctypes.WinError(ctypes.get_last_error())
            ace = ctypes.cast(pointer, ctypes.POINTER(AllowedAce)).contents
            assert ace.type == 0 and ace.size >= ctypes.sizeof(AllowedAce), (
                "Unexpected non-allow ACE"
            )
            sid = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(
                pointer.value + AllowedAce.sid.offset, ctypes.byref(sid)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                entries.append({"sid": sid.value, "mask": ace.mask, "flags": ace.flags})
            finally:
                kernel.LocalFree(sid)
        return {"protected": bool(control.value & 0x1000), "entries": entries}
    finally:
        kernel.LocalFree(descriptor)


async def test_doctor_negotiates_real_stdio_and_simulated_tools(tmp_path):
    folder = seed_demo(tmp_path / "workspace with spaces ü")
    config = folder / "northstar-demo/fleet.json"
    result = await probe(config)
    assert (
        result["initialized"]
        and result["tools"] >= 21
        and result["simulated_inventory"] == "passed"
        and result["scope_rejection"] == "passed"
    )
    assert diagnose(config)["ok"]


async def test_probe_refuses_real_endpoint_configuration(tmp_path):
    config = tmp_path / "real.json"
    known = tmp_path / "known"
    known.write_text("fixture-not-contacted")
    config.write_text(
        json.dumps({"client": "real", "known_hosts": str(known), "hosts": []})
    )
    with pytest.raises(ValueError, match="explicit simulated"):
        await probe(config)


def test_launcher_failure_has_no_protocol_stdout(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "remote_fleet_agent.launcher",
            "--config",
            str(tmp_path / "missing.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert (
        result.returncode == 2
        and result.stdout == ""
        and "could not start" in result.stderr
    )


def test_key_generation_needs_no_external_openssh(tmp_path, monkeypatch):
    import asyncssh

    from remote_fleet_agent.cli import init_client

    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("Key generation must not invoke ssh-keygen"),
    )
    # Windows ACL setup legitimately uses the native SID helper, so test key generation
    # independently of that helper's subprocess implementation.
    if os.name == "nt":
        monkeypatch.setattr(host_platform, "_windows_restrict", lambda *_: None)
    init_client("example", stage_dir=tmp_path / "kit")
    private = tmp_path / "keys/example/id_ed25519"
    assert (
        asyncssh.read_private_key(private).export_public_key()
        == asyncssh.read_public_key(tmp_path / "kit/id_ed25519.pub").export_public_key()
    )


@pytest.mark.parametrize(
    "enable,inherited,expected", [(False, "0", 21), (True, "0", 26), (True, "1", 21)]
)
async def test_launcher_preserves_audit_policy_and_ignores_inherited_engagement(
    tmp_path, enable, inherited, expected
):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    known = tmp_path / "known"
    known.write_text("fixture-no-endpoint-connections")
    config = tmp_path / "approved.json"
    config.write_text(
        json.dumps(
            {
                "client": "approved",
                "known_hosts": str(known),
                "hosts": [],
                "maintenance_actions": ["start_time_service"],
            }
        )
    )
    spec = launch_spec(
        sys.executable,
        str(config),
        str(tmp_path / "unused-fixture-key"),
        str(tmp_path / "jobs"),
        enable,
    )
    env = {
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "REMOTE_FLEET_AGENT_AUDIT_ONLY": inherited,
        "REMOTE_FLEET_AGENT_CONFIG": str(tmp_path / "wrong-client.json"),
        "REMOTE_FLEET_AGENT_KEY_PATH": "wrong-inherited-key",
    }
    async with (
        stdio_client(StdioServerParameters(**spec, env=env)) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        assert len((await session.list_tools()).tools) == expected
