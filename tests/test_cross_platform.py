import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from remote_fleet_agent.config import FleetConfig, FleetHost
from remote_fleet_agent.docker_lab import init, validate
from remote_fleet_agent.lab_cli import demo
from remote_fleet_agent.reporting import findings_for, shareable_snapshot
from remote_fleet_agent.runtime import FleetRuntime
from remote_fleet_agent.server import build_server
from remote_fleet_agent.tools.unix import build_command, collect_unix
from remote_fleet_agent.workspace import EvidenceWorkspace


def scope(tmp_path, platform="linux", port=22, simulation=False):
    known = tmp_path / "known"
    known.write_text("fixture")
    return FleetConfig(
        "test",
        known,
        (FleetHost("device", "fleet@127.0.0.1", ("all",), platform, port),),
        simulation=simulation,
    )


@pytest.mark.parametrize("platform", ["windows", "linux", "macos"])
def test_explicit_platform_and_port_round_trip(tmp_path, platform):
    p = tmp_path / "fleet.json"
    (tmp_path / "known").write_text("fixture")
    p.write_text(
        json.dumps(
            {
                "client": "test",
                "known_hosts": "known",
                "hosts": [
                    {
                        "name": "a",
                        "target": "fleet@127.0.0.1",
                        "platform": platform,
                        "port": 2222,
                        "approved": True,
                    },
                    {
                        "name": "b",
                        "target": "fleet@127.0.0.1",
                        "platform": platform,
                        "port": 2223,
                        "approved": True,
                    },
                ],
            }
        )
    )
    c = FleetConfig.load(p)
    assert c.resolve("a").platform == platform and c.resolve("a").port == 2222
    with pytest.raises(ValueError, match="multiple ports"):
        c.resolve("fleet@127.0.0.1")


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "darwin"),
        ("port", True),
        ("port", 0),
        ("port", 65536),
        ("scenario", "offline"),
    ],
)
def test_invalid_native_platform_settings_are_rejected(tmp_path, field, value):
    p = tmp_path / "fleet.json"
    (tmp_path / "known").write_text("fixture")
    host = {"name": "a", "target": "fleet@127.0.0.1", "approved": True, field: value}
    p.write_text(
        json.dumps({"client": "test", "known_hosts": "known", "hosts": [host]})
    )
    with pytest.raises(ValueError):
        FleetConfig.load(p)


@pytest.mark.asyncio
async def test_native_hosts_sharing_address_use_separate_pinned_connections(tmp_path):
    c = scope(tmp_path)
    c = replace(c, hosts=(c.hosts[0], replace(c.hosts[0], name="other", port=2222)))
    rt = FleetRuntime(c, "key", tmp_path / "jobs")
    first = SimpleNamespace(close=lambda: None)
    second = SimpleNamespace(close=lambda: None)
    with patch(
        "remote_fleet_agent.runtime.connect", AsyncMock(side_effect=[first, second])
    ) as connection:
        assert await rt.connection("device") is first
        assert await rt.connection("other") is second
        assert await rt.connection("device") is first
        assert (
            connection.await_count == 2 and connection.call_args.kwargs["port"] == 2222
        )
        assert connection.call_args.kwargs["known_hosts"] == str(c.known_hosts)
    await rt.close()


@pytest.mark.asyncio
async def test_unix_unsupported_sections_and_legacy_tools_never_connect(tmp_path):
    c = scope(tmp_path)
    rt = FleetRuntime(c, "key", tmp_path / "jobs")
    with patch.object(rt.pool, "get", AsyncMock()) as connection:
        with pytest.raises(ValueError):
            await rt.collect("device", "inventory", ["defender"])
        with pytest.raises(ValueError):
            rt.start("inventory", hosts=["device"], sections=["defender"])
        with pytest.raises(ValueError, match="Windows"):
            rt.require_windows("device")
        connection.assert_not_awaited()
    server = build_server("key", config=c, job_dir=tmp_path / "jobs")
    from mcp.server.mcpserver.exceptions import ToolError

    with patch("remote_fleet_agent.runtime.connect", AsyncMock()) as connection:
        with pytest.raises(ToolError, match="Windows"):
            await server.call_tool(
                "run_check", {"host": "device", "check_name": "av_status"}
            )
        with pytest.raises(ToolError, match="Windows"):
            await server.call_tool(
                "fit_check", {"host": "device", "source_path": "/", "dest_drive": "/"}
            )
        connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_windows_maintenance_is_blocked_on_unix_before_ssh(tmp_path):
    rt = FleetRuntime(
        replace(scope(tmp_path), maintenance_actions=("clear_dns_cache",)),
        "key",
        tmp_path / "jobs",
    )
    with patch.object(rt.pool, "get", AsyncMock()) as connection:
        with pytest.raises(ValueError, match="Windows"):
            await rt.maintenance.preview("device", "clear_dns_cache")
        connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_port_invalidates_old_windows_maintenance_plan(tmp_path):
    rt = FleetRuntime(
        replace(scope(tmp_path, "windows"), maintenance_actions=("clear_dns_cache",)),
        "key",
        tmp_path / "jobs",
    )
    with patch.object(
        rt.maintenance, "_run", AsyncMock(return_value={"available": True})
    ):
        plan = await rt.maintenance.preview("device", "clear_dns_cache")
    rt.maintenance.config = replace(
        rt.config, hosts=(replace(rt.config.hosts[0], port=2222),)
    )
    with patch.object(rt.maintenance, "_run", AsyncMock()) as run:
        with pytest.raises(ValueError, match="target changed"):
            await rt.maintenance.execute(plan["id"], plan["digest"])
        run.assert_not_awaited()


@pytest.mark.asyncio
async def test_simulation_cannot_open_ssh_and_isolated_from_real_evidence(tmp_path):
    c = scope(tmp_path, "macos", simulation=True)
    rt = FleetRuntime(c, "no-real-key", tmp_path / "jobs")
    with patch(
        "remote_fleet_agent.runtime.connect",
        AsyncMock(side_effect=AssertionError("real SSH forbidden")),
    ) as connection:
        job = rt.start("inventory", group="all")
        record = await rt.jobs.wait(job["id"])
        assert record["hosts"]["device"]["result"]["simulation"] is True
        with pytest.raises(ValueError, match="never opens SSH"):
            await rt.connection("device")
        connection.assert_not_awaited()
    synthetic = EvidenceWorkspace(c, tmp_path / "jobs")
    real = EvidenceWorkspace(replace(c, simulation=False), tmp_path / "jobs")
    assert (
        synthetic.path != real.path
        and len(synthetic.jobs()[0]) == 1
        and real.jobs()[0] == []
    )
    await rt.close()


def test_demo_has_seven_platforms_scenarios_and_no_key(tmp_path):
    result = demo(tmp_path / "demo")
    c = FleetConfig.load(tmp_path / "demo/fleet.json")
    ws = EvidenceWorkspace(c, tmp_path / "demo/jobs")
    snap = ws.snapshot()
    assert result["simulation"] is True and len(snap["hosts"]) == 7
    assert {h["platform"] for h in snap["hosts"]} == {"windows", "macos", "linux"}
    assert any(h["collection_status"] == "failed" for h in snap["hosts"])
    assert any(h["collection_status"] == "partial" for h in snap["hosts"])
    assert not (tmp_path / "demo/.state/client_key").exists()
    redacted = shareable_snapshot(snap)
    assert redacted["simulation"] is True and all(
        h["simulation"] for h in redacted["hosts"]
    )
    assert "Simulation mode" in Path(result["dashboard"]).read_text()
    assert "SIMULATED / FICTIONAL" in (tmp_path / "demo/report.html").read_text()


def test_demo_does_not_overwrite_unrelated_files(tmp_path):
    (tmp_path / "precious").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        demo(tmp_path)
    assert (tmp_path / "precious").read_text() == "keep"


@pytest.mark.asyncio
async def test_unix_failures_remain_unknown_and_do_not_become_passes():
    conn = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                SimpleNamespace(exit_status=127, stderr="python3 missing", stdout=""),
                SimpleNamespace(
                    exit_status=0,
                    stderr="",
                    stdout='{"status":"collected","items":[{"DeviceID":"/","Size":100,"FreeSpace":5}]}',
                ),
            ]
        )
    )
    result = await collect_unix(conn, "linux", "inventory", ["os", "disks"])
    assert (
        result["status"] == "partial"
        and result["sections"]["os"]["status"] == "unknown"
    )
    assert result["sections"]["disks"]["items"][0]["FreeSpace"] == 5
    issues = findings_for(
        {
            "platform": "linux",
            "sections": {"inventory.disks": result["sections"]["disks"]},
        }
    )
    assert {"low_disk", "security_gap"} <= {i["code"] for i in issues}


def test_probe_allows_only_fixed_selection_and_checks_remote_kernel():
    for values in [
        ("linux", "inventory", "arbitrary;touch /tmp/pwn"),
        ("windows", "inventory", "os"),
    ]:
        with pytest.raises(ValueError):
            build_command(*values)
    # Run a Linux probe locally on this Mac: the mismatch must be unknown, not fabricated OS evidence.
    result = subprocess.run(
        build_command("linux", "inventory", "os"),
        shell=True,
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(result.stdout)
    import platform

    assert value["status"] == (
        "collected" if platform.system() == "Linux" else "unknown"
    )


def test_lab_build_context_has_only_public_key_and_no_privileged_host_access(tmp_path):
    directory = init(tmp_path / "lab")
    config = json.loads((directory / "compose.json").read_text())
    if os.name != "nt":
        assert (directory / ".state/client_key").stat().st_mode & 0o777 == 0o600
    ignore = (directory / ".dockerignore").read_text()
    assert ignore.startswith("**\n") and "!.state" not in ignore
    for service in config["services"].values():
        assert service["ports"] == ["127.0.0.1::2222"] and service["read_only"] is True
        assert not service.get("privileged") and not service.get("network_mode")
        assert all(
            "/var/run/docker.sock" not in m and "client_key" not in m
            for m in service["volumes"]
        )
    config["services"]["device-a"]["ports"] = ["0.0.0.0:22:2222"]
    (directory / "compose.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="modified"):
        validate(directory)


def test_batch_scope_binds_platform_and_ssh_port(tmp_path):
    from datetime import UTC, datetime, timedelta

    from remote_fleet_agent.batches import BatchManager
    from remote_fleet_agent.storage import digest

    config = replace(
        scope(tmp_path, "windows"), maintenance_actions=("clear_dns_cache",)
    )
    batch = BatchManager(SimpleNamespace(config=config), tmp_path / "jobs")
    spec = {
        "action": "clear_dns_cache",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "targets": {"device": "fleet@127.0.0.1"},
        "ports": {"device": 22},
        "platforms": {"device": "windows"},
    }
    record = {"spec": spec, "digest": digest(spec)}
    batch.validate(record, record["digest"])
    for host in (
        replace(config.hosts[0], port=2222),
        replace(config.hosts[0], platform="linux"),
    ):
        batch.config = replace(config, hosts=(host,))
        with pytest.raises(ValueError, match="scope changed"):
            batch.validate(record, record["digest"])


def test_simulation_never_enables_endpoint_maintenance(tmp_path):
    config = tmp_path / "fleet.json"
    config.write_text(
        json.dumps(
            {
                "client": "demo",
                "simulation": True,
                "known_hosts": "not-needed",
                "hosts": [],
                "maintenance_actions": ["clear_dns_cache"],
            }
        )
    )
    with pytest.raises(ValueError, match="collection-only"):
        FleetConfig.load(config)


def test_lab_has_bounded_writable_scratch_without_host_filesystem(tmp_path):
    """Support scratch lives in per-container tmpfs, never a host home bind."""
    directory = init(tmp_path / "support-lab")
    config = json.loads((directory / "compose.json").read_text())
    assert set(config["services"]) == {"device-a", "device-b"}
    for suffix, service in zip(("a", "b"), config["services"].values(), strict=True):
        assert service["read_only"] is True
        assert "/tmp:size=16m,mode=1777" in service["tmpfs"]
        assert service["volumes"] == [f"./.state/hostkeys-{suffix}:/hostkeys:ro"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pids_limit"] <= 64
        assert service["mem_limit"] == "256m"
        assert service["cpus"] <= 0.5
        assert service["restart"] == "no"


def test_lab_down_is_project_scoped_and_retains_keys(tmp_path, monkeypatch):
    from remote_fleet_agent import docker_lab

    directory = init(tmp_path / "isolated-lab")
    key = (directory / ".state/client_key").read_bytes()
    calls = []
    monkeypatch.setattr(docker_lab, "local_engine", lambda: None)
    monkeypatch.setattr(
        docker_lab, "docker", lambda args, **kwargs: calls.append(args) or ""
    )
    result = docker_lab.manage("down", directory)
    assert result["status"] == "stopped"
    assert calls == [
        [
            "compose",
            "--project-directory",
            str(directory),
            "--file",
            str(directory / "compose.json"),
            "down",
            "--remove-orphans",
        ]
    ]
    assert (directory / ".state/client_key").read_bytes() == key


def test_lab_rejects_remote_engine_before_any_docker_command(monkeypatch):
    from remote_fleet_agent import docker_lab

    monkeypatch.setattr(docker_lab.shutil, "which", lambda name: "/fixture/docker")
    monkeypatch.setenv("DOCKER_HOST", "tcp://example.invalid:2375")
    monkeypatch.setattr(
        docker_lab.subprocess,
        "run",
        lambda *a, **kw: pytest.fail("remote Docker invocation forbidden"),
    )
    with pytest.raises(ValueError, match="remote DOCKER_HOST"):
        docker_lab.docker(["info"])
