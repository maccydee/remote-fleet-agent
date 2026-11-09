"""Offline guard checks never open SSH or mutate an endpoint."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("validate_support", Path(__file__).parents[1] / "scripts" / "validate-support.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def manifest(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workspace.json").write_text('{"clients": []}')
    return {"schema": 1, "workspace": str(workspace), "targets": [{
        "client": "lab", "device": "linux-a", "platform": "linux", "account": "fleet",
        "address": "127.0.0.1", "port": 2222, "fingerprint": "SHA256:" + "A" * 43,
        "scratch_parent": str(tmp_path), "authorisation_reference": "test-authorisation",
        "expires_at": "2099-01-01T00:00:00+00:00"}]}


def test_manifest_requires_explicit_current_identity(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert MODULE.load_manifest(path)["targets"][0]["device"] == "linux-a"


@pytest.mark.parametrize("change", [{"fingerprint": ""}, {"fingerprint": "SHA256:bad"},
    {"expires_at": "2020-01-01T00:00:00+00:00"}, {"expires_at": "2099-01-01"},
    {"port": True}, {"platform": "simulated"}, {"scratch_parent": "/"},
    {"scratch_parent": "/tmp/../etc"}, {"authorisation_reference": ""}])
def test_manifest_rejects_unsafe_target(tmp_path, manifest, change):
    manifest["targets"][0].update(change)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        MODULE.load_manifest(path)


def test_preflight_denies_unregistered_scope_before_connect(manifest):
    with pytest.raises(MODULE.PrerequisiteError, match="not uniquely enrolled"):
        MODULE.enrolled_target(manifest, manifest["targets"][0])


@pytest.mark.parametrize("action", ["prepare", "repair", "recover", "cleanup"])
def test_generated_posix_scratch_cycle_checks_exact_state(tmp_path, action):
    target = {"platform": "linux", "scratch_parent": str(tmp_path)}
    run = "a" * 32
    p = tmp_path / ("remote-fleet-support-" + run)
    if action != "prepare":
        p.mkdir()
        (p / "sentinel").write_text(run)
        (p / "marker").write_text("healthy" if action == "recover" else "broken")
    scripts = MODULE.scratch_scripts(target, run, action)
    result = subprocess.run(["sh"], input=scripts["script"], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    check = subprocess.run(["sh"], input=scripts["verification_script"], text=True, capture_output=True, check=False)
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["check_passed"] is True
    if action == "cleanup":
        assert not p.exists()
    else:
        assert (p / "sentinel").read_text() == run


def test_cleanup_refuses_unknown_contents_and_sentinel_mismatch(tmp_path):
    target = {"platform": "linux", "scratch_parent": str(tmp_path)}
    run = "b" * 32
    p = tmp_path / ("remote-fleet-support-" + run)
    p.mkdir()
    (p / "sentinel").write_text(run)
    (p / "marker").write_text("broken")
    (p / "user-file").write_text("preserve")
    scripts = MODULE.scratch_scripts(target, run, "cleanup")
    result = subprocess.run(["sh"], input=scripts["script"], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert (p / "user-file").read_text() == "preserve"
    (p / "sentinel").write_text("different-run")
    scripts = MODULE.scratch_scripts(target, run, "repair")
    result = subprocess.run(["sh"], input=scripts["script"], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert (p / "marker").read_text() == "broken"


def test_cleanup_refuses_symlink_marker(tmp_path):
    run = "c" * 32
    outside = tmp_path / "outside"
    outside.write_text("do-not-change")
    p = tmp_path / ("remote-fleet-support-" + run)
    p.mkdir()
    (p / "sentinel").write_text(run)
    (p / "marker").symlink_to(outside)
    scripts = MODULE.scratch_scripts({"platform": "linux", "scratch_parent": str(tmp_path)}, run, "repair")
    result = subprocess.run(["sh"], input=scripts["script"], text=True, capture_output=True, check=False)
    assert result.returncode != 0
    assert outside.read_text() == "do-not-change"


@pytest.mark.parametrize("run", ["../", "short", "a" * 33, "'; Remove-Item C:\\ -Recurse"])
def test_invalid_run_never_generates_script(tmp_path, run):
    with pytest.raises(ValueError):
        MODULE.scratch_scripts({"platform": "linux", "scratch_parent": str(tmp_path)}, run, "cleanup")


def test_windows_scripts_are_explicit_and_never_recurse():
    target = {"platform": "windows", "scratch_parent": r"C:\Users\Fleet\AppData\Local\Temp"}
    scripts = MODULE.scratch_scripts(target, "d" * 32, "cleanup")
    assert "-Recurse" not in scripts["script"]
    assert "Sentinel mismatch" in scripts["script"]
    assert "ReparsePoint" in scripts["script"]
    assert "Unexpected scratch contents" in scripts["script"]


def test_report_private_and_never_overwrites(tmp_path):
    a = MODULE.write_report(tmp_path, {"status": "BLOCKED"})
    b = MODULE.write_report(tmp_path, {"status": "NOT_RUN"})
    assert a != b
    assert json.loads(a.read_text())["status"] == "BLOCKED"


def test_sentinel_guard_survives_python_optimization(tmp_path):
    import os
    run = "e" * 32
    p = tmp_path / ("remote-fleet-support-" + run)
    p.mkdir()
    (p / "sentinel").write_text("wrong-run")
    (p / "marker").write_text("broken")
    scripts = MODULE.scratch_scripts({"platform": "linux", "scratch_parent": str(tmp_path)}, run, "repair")
    result = subprocess.run(["sh"], input=scripts["script"], text=True, capture_output=True,
                            check=False, env={**os.environ, "PYTHONOPTIMIZE": "1"})
    assert result.returncode != 0
    assert (p / "marker").read_text() == "broken"


@pytest.mark.asyncio
async def test_native_adapter_cannot_self_approve(tmp_path, monkeypatch):
    class Client:
        def __init__(self, *args):
            pass

    monkeypatch.setattr("remote_fleet_agent.support_mcp.WorkspaceClient", Client)
    lab = MODULE.NativeLab({"workspace_url": "http://127.0.0.1:8877", "operator_token_file": "private"},
                           {"client": "lab", "device": "linux-a"}, output=tmp_path)
    with pytest.raises(MODULE.PrerequisiteError, match="Separate human decision pending"):
        await lab._human_gate("/repair-plans", {"id": "plan-1", "state": "awaiting_approval"}, "approved")
    assert not hasattr(lab.client, "approve")


@pytest.mark.asyncio
async def test_native_adapter_rejects_digest_change_during_human_review(tmp_path, monkeypatch):
    class Client:
        def __init__(self, *args):
            pass

    monkeypatch.setattr("remote_fleet_agent.support_mcp.WorkspaceClient", Client)
    lab = MODULE.NativeLab({"workspace_url": "http://127.0.0.1:8877", "operator_token_file": "private"},
                           {"client": "lab", "device": "linux-a"}, output=tmp_path)

    async def changed(*args):
        return {"id": "plan-1", "state": "approved", "digest": "changed", "approval": {"actor": "engineer"}}

    lab._human_gate = changed
    with pytest.raises(MODULE.PrerequisiteError, match="Plan changed"):
        await lab.execute({"id": "plan-1", "digest": "original"})


@pytest.mark.parametrize("change", [{"target": "other@127.0.0.1"}, {"port": 2223},
                                   {"platform": "macos"}, {"simulation": True},
                                   {"host_keys": []}])
def test_controller_target_must_match_private_manifest(change):
    target = {"device": "linux-a", "account": "fleet", "address": "127.0.0.1",
              "port": 2222, "platform": "linux", "fingerprint": "SHA256:" + "A" * 43}
    snapshot = {"name": "linux-a", "target": "fleet@127.0.0.1", "port": 2222,
                "platform": "linux", "simulation": False, "host_keys": [target["fingerprint"]]}
    snapshot.update(change)
    with pytest.raises(MODULE.PrerequisiteError):
        MODULE.validate_controller_target(target, [snapshot])
