import json
from datetime import UTC, datetime

import pytest

from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.storage import read_json, save_json


def case(service, actor, client="northstar-demo", host="windows-01", **values):
    record = service.support.create(
        actor,
        client,
        {
            "host": host,
            "symptom": values.pop("symptom", "Test problem"),
            "profile": "general",
        },
    )
    return service.support.save(actor, client, record, values) if values else record


async def test_operations_cannot_leak_other_clients_or_sites(support_env):
    s, owner, tech, _ = support_env
    case(s, owner, "harbour-demo", symptom="SECRET_CLIENT")
    case(s, owner, host="linux-04", symptom="SECRET_SITE")
    visible = case(s, tech, symptom="Visible problem")
    result = s.operations(tech, {})
    assert [r["id"] for r in result["items"]] == [visible["id"]]
    assert result["total"] == 1
    assert sum(result["counts_by_bucket"].values()) == 1
    assert "SECRET" not in json.dumps(result)
    assert result["items"][0]["engineer_id"] is None
    assert result["items"][0]["engineer_name"] is None
    assert "script" not in json.dumps(result)


async def test_all_records_paged_and_old_attention_not_lost(support_env):
    s, owner, _, _ = support_env
    oldest = case(s, owner, state="unknown")
    for i in range(150):
        case(s, owner, symptom=f"Ordinary {i}")
    result = s.operations(owner, {"limit": 25})
    assert result["total"] == 151
    assert len(result["items"]) == 25
    assert result["items"][0]["id"] == oldest["id"]
    assert result["counts_by_bucket"]["attention"] == 1
    assert result["next_offset"] == 25
    seen = {r["id"] for r in result["items"]}
    while result["next_offset"] is not None:
        result = s.operations(owner, {"limit": 25, "offset": result["next_offset"]})
        seen.update(r["id"] for r in result["items"])
    assert len(seen) == 151


async def test_duplicate_hostnames_filters_and_safe_fields(support_env):
    s, owner, _, _ = support_env
    a = case(s, owner, symptom="Alpha", state="awaiting_approval")
    b = case(s, owner, "harbour-demo", symptom="Beta", state="future_state")
    rows = s.operations(owner, {})["items"]
    assert {(r["client"], r["id"]) for r in rows} == {
        ("northstar-demo", a["id"]),
        ("harbour-demo", b["id"]),
    }
    assert rows[0]["state"] == "future_state" and rows[0]["bucket"] == "attention"
    assert s.operations(owner, {"q": "BETA", "platform": "windows"})["total"] == 1
    assert s.operations(owner, {"bucket": "approval"})["items"][0]["id"] == a["id"]
    assert s.operations(owner, {"state": "future_state"})["total"] == 1
    assert s.operations(owner, {"platform": "macos"})["total"] == 0
    assert s.operations(owner, {"engineer": "unassigned"})["total"] == 2


@pytest.mark.parametrize(
    "filters",
    [
        {"limit": 0},
        {"limit": True},
        {"limit": 201},
        {"offset": -1},
        {"offset": False},
        {"limit": "25"},
        {"platform": "darwin"},
        {"q": "x" * 241},
        {"bucket": "nonsense"},
        {"unknown": "x"},
        {"q": None},
        {"client": ["northstar-demo"]},
    ],
)
async def test_filter_validation(support_env, filters):
    with pytest.raises(ValueError):
        support_env[0].operations(support_env[1], filters)


async def test_current_actor_and_explicit_scope_rechecked(support_env):
    s, _owner, tech, _ = support_env
    with pytest.raises(Denied):
        s.operations(tech, {"client": "harbour-demo"})
    with pytest.raises(Denied):
        s.operations(tech, {"client": "northstar-demo", "site": "warehouse"})
    s.store.db.execute("UPDATE operators SET scopes=? WHERE id=?", ("{}", tech["id"]))
    s.store.db.commit()
    assert s.operations(tech, {})["total"] == 0
    s.store.db.execute("UPDATE operators SET enabled=0 WHERE id=?", (tech["id"],))
    s.store.db.commit()
    with pytest.raises(Denied):
        s.operations(tech, {})


async def test_broken_permitted_source_is_partial_and_other_client_works(support_env):
    s, owner, tech, directory = support_env
    visible = case(s, owner, symptom="Still available")
    (directory / "harbour-demo/fleet.json").write_text("not json SECRET_PATH")
    result = s.operations(owner, {})
    assert [r["id"] for r in result["items"]] == [visible["id"]]
    assert result["partial_sources"] == [
        {"client": "harbour-demo", "reason": "Source unavailable"}
    ]
    assert str(directory) not in json.dumps(result)
    assert "SECRET_PATH" not in json.dumps(result)
    assert s.operations(tech, {})["partial_sources"] == []


async def test_removed_source_removed_host_and_site_change_not_visible(support_env):
    s, owner, tech, directory = support_env
    case(s, owner, "harbour-demo", symptom="REMOVED_CLIENT")
    case(s, tech, symptom="MOVED_DEVICE")
    path = directory / "workspace.json"
    registry = read_json(path)
    registry["clients"] = registry["clients"][:1]
    save_json(path, registry)
    path = directory / "northstar-demo/fleet.json"
    fleet = read_json(path)
    fleet["hosts"][0]["site"] = "warehouse"
    save_json(path, fleet)
    assert s.operations(tech, {})["total"] == 0
    assert "REMOVED_CLIENT" not in json.dumps(s.operations(owner, {}))


async def test_sort_uses_actual_timestamps_and_stable_ties(support_env):
    s, owner, _, _ = support_env
    a = case(s, owner)
    b = case(s, owner)
    for record, ts in [
        (a, "2026-11-08T13:00:00+03:00"),
        (b, "2026-11-08T11:00:00+00:00"),
    ]:
        payload = {
            k: v
            for k, v in record.items()
            if k not in {"id", "site", "revision", "owner_id"}
        }
        payload["updated_at"] = ts
        s.store.db.execute(
            "UPDATE records SET data=? WHERE id=?", (json.dumps(payload), record["id"])
        )
    s.store.db.commit()
    assert [r["id"] for r in s.operations(owner, {})["items"]] == [b["id"], a["id"]]


async def test_schedules_are_actual_collections_and_respect_complete_scope(support_env):
    s, owner, tech, _ = support_env
    s.store.save(
        "schedule",
        "northstar-demo",
        "head-office",
        owner,
        {
            "name": "Daily metadata",
            "hosts": ["windows-01"],
            "operation": "inventory",
            "enabled": True,
            "state": "scheduled",
            "next_run": datetime.now(UTC).isoformat(),
        },
    )
    s.store.save(
        "schedule",
        "northstar-demo",
        "",
        owner,
        {
            "name": "SECRET_MIXED",
            "hosts": ["windows-01", "linux-04"],
            "operation": "inventory",
            "enabled": True,
            "state": "scheduled",
            "next_run": datetime.now(UTC).isoformat(),
        },
    )
    result = s.operations(tech, {})
    assert len(result["upcoming"]) == 1
    assert result["upcoming"][0]["kind"] == "collection"
    assert result["upcoming"][0]["hosts"] == ["windows-01"]
    assert "SECRET_MIXED" not in json.dumps(result)


async def test_source_identity_must_match_validated_configuration(
    support_env, monkeypatch
):
    from dataclasses import replace

    from remote_fleet_agent.config import FleetConfig

    s, owner, _, _ = support_env
    case(s, owner)
    original = FleetConfig.load

    def changed_identity(path, **options):
        config = original(path, **options)
        if config.client == "northstar-demo":
            return replace(config, client="unexpected-client")
        return config

    monkeypatch.setattr(FleetConfig, "load", changed_identity)
    result = s.operations(owner, {"client": "northstar-demo"})
    assert result["items"] == []
    assert result["partial_sources"] == [
        {"client": "northstar-demo", "reason": "Source unavailable"}
    ]
