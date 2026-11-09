"""Regressions for the November security review; no enrolled endpoint access."""

import json
import os
import platform
import stat
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from remote_fleet_agent import host_platform
from remote_fleet_agent.administration import EndpointClaim, claim_path, endpoint_key
from remote_fleet_agent.device_identity import resolve_device
from remote_fleet_agent.live_store import Conflict
from remote_fleet_agent.storage import digest, private_dir
from remote_fleet_agent.support_mcp import WorkspaceClient, WorkspaceError
from tests.test_support_mcp import request
from tests.test_support_mcp import workspace as _workspace

workspace = _workspace


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="Native macOS extended ACL semantics"
)
def test_native_macos_extended_grants_are_removed_and_parent_grants_rejected(tmp_path):
    folder = tmp_path / "private-acl"
    folder.mkdir()
    subprocess.run(
        ["/bin/chmod", "+a", "everyone allow read,write,delete", str(folder)],
        check=True,
    )
    assert host_platform._macos_acl(folder)
    private_dir(folder)
    assert not host_platform._macos_acl(folder)
    subprocess.run(
        ["/bin/chmod", "+a", "everyone allow delete_child", str(folder)], check=True
    )
    try:
        with pytest.raises(PermissionError, match="parent ACL"):
            private_dir(folder / "child")
    finally:
        subprocess.run(["/bin/chmod", "-N", str(folder)], check=True)


def target(**values):
    return {
        "target": "engineer@server.invalid",
        "port": 22,
        "host_keys": ["SHA256:ed25519"],
        "simulation": False,
        **values,
    }


def test_existing_private_directory_permissions_are_repaired(tmp_path):
    folder = tmp_path / "existing"
    folder.mkdir()
    if os.name != "nt":
        folder.chmod(0o777)
    private_dir(folder)
    if os.name != "nt":
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    else:
        owner, grants = host_platform._windows_security(folder)
        assert owner in {host_platform._windows_sid(), "S-1-5-32-544"}
        assert {sid for sid, _ in grants} == {host_platform._windows_sid(), "S-1-5-18"}


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX ownership and directory-entry semantics"
)
def test_untrusted_owner_and_replaceable_parent_are_rejected(tmp_path, monkeypatch):
    folder = tmp_path / "foreign"
    folder.mkdir()
    with monkeypatch.context() as patch:
        patch.setattr(host_platform.os, "geteuid", lambda: os.getuid() + 1)
        with pytest.raises(PermissionError, match="owner"):
            private_dir(folder)
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    with pytest.raises(PermissionError, match="parent"):
        private_dir(parent / "secret")
    assert not (parent / "secret").exists()
    parent.chmod(0o1777)
    assert private_dir(parent / "secret").is_dir()


def test_windows_parent_acl_is_checked_before_permission_changes(tmp_path, monkeypatch):
    folder = tmp_path / "private"
    folder.mkdir()
    monkeypatch.setattr(host_platform.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host_platform, "_windows_sid", lambda: "S-1-5-21-123")
    monkeypatch.setattr(
        host_platform,
        "_windows_security",
        lambda path: ("S-1-5-21-123", [("S-1-1-0", 0x40)]),
    )
    with pytest.raises(PermissionError, match="parent"):
        private_dir(folder)
    monkeypatch.setattr(
        host_platform, "_windows_security", lambda path: ("S-1-5-21-999", [])
    )
    with pytest.raises(PermissionError, match="owner"):
        private_dir(folder)


def test_overlapping_pins_key_rotation_and_accounts_share_one_claim():
    first = target()
    ident = resolve_device(first)
    alias = target(
        target="other@alias.invalid", host_keys=["SHA256:rsa", "SHA256:ed25519"]
    )
    assert resolve_device(alias) == ident
    assert resolve_device(target(host_keys=["SHA256:rotated"])) == ident
    assert (
        resolve_device(target(target="other@alias.invalid", host_keys=["SHA256:rsa"]))
        == ident
    )
    claim = EndpointClaim(first, "one")
    try:
        with pytest.raises(Conflict):
            EndpointClaim(alias, "two")
        claim.mark_unknown()
        with pytest.raises(Conflict):
            EndpointClaim(alias, "three")
    finally:
        claim.release()


def test_operator_enrolled_id_links_disjoint_verified_aliases():
    ident = uuid.uuid4().hex
    assert endpoint_key(target(device_id=ident)) == ident
    assert (
        endpoint_key(
            target(
                device_id=ident,
                target="user@other.invalid",
                host_keys=["SHA256:other"],
                port=2222,
            )
        )
        == ident
    )


def test_identity_conflicts_cannot_merge_locks_or_change_saved_ids():
    first = target()
    second = target(target="user@other.invalid", host_keys=["SHA256:rsa"])
    a, b = resolve_device(first), resolve_device(second)
    assert a != b
    with pytest.raises(Conflict, match="conflicting"):
        resolve_device(target(host_keys=["SHA256:ed25519", "SHA256:rsa"]))
    with pytest.raises(Conflict):
        resolve_device(target(device_id=b))
    assert resolve_device(first) == a and resolve_device(second) == b


def test_identity_registration_is_atomic_across_threads():
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: resolve_device(target()), range(12)))
    assert len(set(ids)) == 1


def test_legacy_unknown_lock_blocks_alias_but_remains_reconcilable(
    monkeypatch, tmp_path
):
    root = private_dir(tmp_path / "claims")
    monkeypatch.setenv("REMOTE_FLEET_ENDPOINT_LOCK_DIR", str(root))
    original = target()
    old = root / (digest({"identity": original["host_keys"], "port": 22}) + ".lock")
    old.write_text(json.dumps({"owner": "old-run", "active": False}))
    assert claim_path(original) == old
    with pytest.raises(Conflict, match="Legacy"):
        EndpointClaim(target(host_keys=["SHA256:ed25519", "SHA256:rsa"]), "new")
    old.unlink()  # Represents an explicit engineer reconciliation, never automatic expiry.
    assert claim_path(original).stem == resolve_device(original)


def test_mcp_validation_and_scope_errors_keep_session(workspace):
    app, owner_token, token_file = workspace
    bridge = WorkspaceClient(app.url, token_file)
    try:
        for _ in range(20):
            with pytest.raises(WorkspaceError, match="not approved"):
                bridge.call(
                    "/api/clients/northstar-demo/support-cases",
                    {"host": "missing", "symptom": "Review fixture"},
                )
        cookie = bridge.cookie
        assert len(app.sessions) == 1
        assert bridge.call("/api/bootstrap")["actor"]["role"] == "technician"
        assert bridge.cookie == cookie and not app.login_attempts
        assert request(app, "/api/login", {"token": owner_token})[0] == 200
    finally:
        bridge.close()


def test_failed_logins_do_not_lock_out_valid_tokens(workspace):
    app, owner_token, _ = workspace
    for _ in range(15):
        assert request(app, "/api/login", {"token": "invalid-review-fixture"})[0] == 403
    assert len(app.login_attempts) == 12
    assert request(app, "/api/login", {"token": owner_token})[0] == 200


def test_expired_session_is_not_replayed_and_next_explicit_call_reauthenticates(
    workspace,
):
    app, _, token_file = workspace
    bridge = WorkspaceClient(app.url, token_file)
    try:
        bridge.call("/api/bootstrap")
        session = bridge.cookie.split("=", 1)[1]

        async def expire():
            app.sessions[session]["expires"] = datetime.now(UTC) - timedelta(seconds=1)

        app.call(expire())
        with pytest.raises(WorkspaceError) as error:
            bridge.call(
                "/api/clients/northstar-demo/support-cases",
                {"host": "windows-01", "symptom": "Must not replay"},
            )
        assert error.value.code == "session_expired" and bridge.cookie is None
        assert bridge.call("/api/clients/northstar-demo/support-cases") == []
    finally:
        bridge.close()
