import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_fleet_agent.config import FleetConfig, FleetHost
from remote_fleet_agent.storage import save_json
from remote_fleet_agent.workspace import EvidenceWorkspace


def workspace(tmp_path):
    known = tmp_path / "known"
    known.write_text("verified-fixture")
    config = FleetConfig(
        "client-secret",
        known,
        (FleetHost("secret-pc", "Alice@192.0.2.1", ("private-group",)),),
    )
    return EvidenceWorkspace(config, tmp_path / "store")


def job(ws, ident="a" * 32, age=100, status="complete", items=None):
    when = (datetime.now(UTC) - timedelta(days=age)).isoformat()
    record = {
        "id": ident,
        "client": ws.config.client,
        "operation": "inventory",
        "created_at": when,
        "finished_at": when,
        "status": status,
        "hosts": {
            "secret-pc": {
                "status": status,
                "finished_at": when,
                "result": {
                    "sections": {
                        "firewall": {
                            "status": "collected",
                            "items": items
                            if items is not None
                            else [{"Name": "Secret profile", "Enabled": False}],
                            "total": 1,
                            "truncated": False,
                        }
                    }
                },
            }
        },
    }
    save_json(ws.path / (ident + ".json"), record)
    return record


def fields(**updates):
    return {
        "asset": "Private NAS",
        "category": "nas",
        "control": "2500",
        "status": "open",
        "reviewer": "Alice",
        "note": "private Alice@example.test 192.0.2.88 </script><script>alert(1)</script>",
        "reference": "/private/client/file",
        "job_id": "",
        **updates,
    }


def test_manual_revision_history_and_atomic_stale_import(tmp_path):
    ws = workspace(tmp_path)
    a = ws.add_manual(**fields())
    b = ws.import_manual(
        [{"id": a["id"], "expected_revision": 1, "fields": fields(status="verified")}]
    )[0]
    assert b["revision"] == 2 and b["history"][0]["fields"]["status"] == "open"
    before = ws.manual_path.read_bytes()
    with pytest.raises(ValueError, match="revision"):
        ws.import_manual(
            [
                {"fields": fields(asset="new")},
                {"id": a["id"], "expected_revision": 1, "fields": fields()},
            ]
        )
    assert ws.manual_path.read_bytes() == before
    if os.name != "nt":
        assert ws.manual_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "update",
    [
        {"category": "shell"},
        {"control": "9999"},
        {"reviewer": ""},
        {"asset": ""},
        {"note": "x" * 4001},
        {"unexpected": "x"},
    ],
)
def test_manual_validation_rejects_unsafe_shapes(tmp_path, update):
    ws = workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.add_manual(**fields(**update))
    assert ws.manual_records() == []


def test_manual_reference_must_exist_in_this_client(tmp_path):
    ws = workspace(tmp_path)
    with pytest.raises(FileNotFoundError):
        ws.add_manual(**fields(job_id="a" * 32))
    j = job(ws)
    j["client"] = "different"
    save_json(ws.path / ("a" * 32 + ".json"), j)
    with pytest.raises(ValueError, match="another client"):
        ws.add_manual(**fields(job_id="a" * 32))


def test_shareable_export_excludes_all_raw_identifiers_notes_and_script_payload(
    tmp_path,
):
    ws = workspace(tmp_path)
    job(ws)
    ws.add_manual(**fields())
    private = ws.export("dashboard", shareable=False)
    redacted = ws.export("dashboard", shareable=True)
    source = Path(private["path"]).read_text()
    safe = Path(redacted["path"]).read_text()
    assert "Alice@example.test" in source and "\\u003c/script" in source
    for marker in (
        "client-secret",
        "secret-pc",
        "Alice",
        "192.0.2.1",
        "private-group",
        "Private NAS",
        "Secret profile",
        "/private/client/file",
        "alert(1)",
    ):
        assert marker not in safe, marker
    assert "Host 001" in safe and "Asset 001" in safe
    report = Path(ws.export("report", shareable=False)["path"]).read_text()
    assert "&lt;/script&gt;" in report and "<script>alert(1)" not in report
    assert "Not a compliance verdict" in safe


def test_export_cannot_hide_malformed_job_as_success(tmp_path):
    ws = workspace(tmp_path)
    (ws.path / ("b" * 32 + ".json")).write_text("broken")
    snap = ws.snapshot()
    assert len(snap["storage_errors"]) == 1
    assert snap["hosts"][0]["collection_status"] == "not_collected"
    assert snap["hosts"][0]["findings"][0]["code"] == "security_gap"


def test_retention_preserves_active_manual_references_and_recent_jobs(tmp_path):
    ws = workspace(tmp_path)
    job(ws, "a" * 32)
    job(ws, "b" * 32, age=101)
    job(ws, "c" * 32, age=102, status="running")
    ws.add_manual(**fields(job_id="b" * 32))
    assert not ws.preview_retention(90, keep_recent=1)["files"]
    plan = ws.preview_retention(90, keep_recent=0)
    assert [r["path"] for r in plan["files"]] == ["a" * 32 + ".json"]
    result = ws.apply_retention(plan["id"], plan["digest"])
    assert (
        result["status"] == "complete" and not (ws.path / ("a" * 32 + ".json")).exists()
    )
    assert (ws.path / ("b" * 32 + ".json")).exists() and ws.manual_records()
    with pytest.raises(ValueError, match="already used"):
        ws.apply_retention(plan["id"], plan["digest"])


def test_retention_revalidates_all_files_before_deleting_any(tmp_path):
    ws = workspace(tmp_path)
    job(ws, "a" * 32)
    job(ws, "b" * 32)
    p = ws.preview_retention(90, 0)
    (ws.path / ("b" * 32 + ".json")).write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        ws.apply_retention(p["id"], p["digest"])
    assert (ws.path / ("a" * 32 + ".json")).exists()


def test_retention_new_manual_reference_blocks_previously_previewed_deletion(tmp_path):
    ws = workspace(tmp_path)
    job(ws)
    p = ws.preview_retention(90, 0)
    ws.add_manual(**fields(job_id="a" * 32))
    with pytest.raises(ValueError, match="managed"):
        ws.apply_retention(p["id"], p["digest"])
    assert (ws.path / ("a" * 32 + ".json")).exists()


def test_retention_tamper_expiry_symlink_and_unrelated_files_fail_closed(tmp_path):
    ws = workspace(tmp_path)
    job(ws)
    p = ws.preview_retention(90, 0)
    with pytest.raises(ValueError, match="changed"):
        ws.apply_retention(p["id"], "wrong")
    record_path = ws.path / "retention" / (p["id"] + ".json")
    record = json.loads(record_path.read_text())
    record["plan"]["files"][0]["path"] = "../../outside"
    save_json(record_path, record)
    with pytest.raises(ValueError, match="changed"):
        ws.apply_retention(p["id"], p["digest"])
    p = ws.preview_retention(90, 0)
    target = ws.path / ("a" * 32 + ".json")
    target.unlink()
    outside = tmp_path / "outside"
    outside.write_text("leave intact")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="unsafe"):
        ws.apply_retention(p["id"], p["digest"])
    assert outside.read_text() == "leave intact"
    assert (ws.path / "retention" / (p["id"] + ".json")).exists()


def test_retention_exports_explicitly_managed_copies_only(tmp_path):
    ws = workspace(tmp_path)
    result = ws.export("report")
    meta = Path(result["path"]).with_suffix(".json")
    data = json.loads(meta.read_text())
    data["created_at"] = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    save_json(meta, data)
    external = tmp_path / "shared-copy.html"
    external.write_text(Path(result["path"]).read_text())
    p = ws.preview_retention(90, 0)
    assert len(p["files"]) == 2
    ws.apply_retention(p["id"], p["digest"])
    assert external.exists() and not Path(result["path"]).exists()


def test_historical_manual_references_are_retained(tmp_path):
    ws = workspace(tmp_path)
    job(ws, "a" * 32)
    job(ws, "b" * 32)
    r = ws.add_manual(**fields(job_id="a" * 32))
    ws.import_manual(
        [{"id": r["id"], "expected_revision": 1, "fields": fields(job_id="b" * 32)}]
    )
    assert not ws.preview_retention(90, 0)["files"]


def test_latest_section_uses_collection_time_not_job_creation_order(tmp_path):
    ws = workspace(tmp_path)
    first = job(ws, "a" * 32, age=10)
    job(ws, "b" * 32, age=9, items=[{"Name": "New profile", "Enabled": True}])
    first["finished_at"] = first["hosts"]["secret-pc"]["finished_at"] = (
        datetime.now(UTC) - timedelta(days=1)
    ).isoformat()
    save_json(ws.path / ("a" * 32 + ".json"), first)
    host = ws.snapshot()["hosts"][0]
    assert host["sections"]["inventory.firewall"]["items"][0]["Enabled"] is False
    assert host["sources"]["inventory.firewall"]["job_id"] == "a" * 32


def test_offline_workspace_config_does_not_need_an_ssh_key_or_known_hosts(tmp_path):
    path = tmp_path / "fleet.json"
    path.write_text(
        json.dumps({"client": "offline", "known_hosts": "not-created-yet", "hosts": []})
    )
    with pytest.raises(ValueError, match="known_hosts"):
        FleetConfig.load(path)
    config = FleetConfig.load(path, require_known_hosts=False)
    ws = EvidenceWorkspace(config, tmp_path / "data")
    ws.add_manual(**fields())
    assert len(ws.snapshot()["manual"]) == 1 and ws.snapshot()["hosts"] == []


def test_retention_preserves_retry_lineage_of_retained_jobs(tmp_path):
    ws = workspace(tmp_path)
    job(ws, "a" * 32, age=100)
    child = job(ws, "b" * 32, age=1)
    child["parent_id"] = "a" * 32
    save_json(ws.path / ("b" * 32 + ".json"), child)
    assert not ws.preview_retention(90, 0)["files"]


def test_retry_created_after_retention_preview_prevents_parent_deletion(tmp_path):
    ws = workspace(tmp_path)
    job(ws, "a" * 32, age=100)
    p = ws.preview_retention(90, 0)
    child = job(ws, "b" * 32, age=1)
    child["parent_id"] = "a" * 32
    save_json(ws.path / ("b" * 32 + ".json"), child)
    with pytest.raises(ValueError, match="managed"):
        ws.apply_retention(p["id"], p["digest"])
    assert (ws.path / ("a" * 32 + ".json")).exists()
