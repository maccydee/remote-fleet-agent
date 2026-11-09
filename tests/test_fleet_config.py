import json

import pytest

from remote_fleet_agent.config import FleetConfig


def config_file(tmp_path, **overrides):
    (tmp_path / "known_hosts").write_text("verified-test-fixture")
    data = {
        "client": "example",
        "known_hosts": "known_hosts",
        "hosts": [
            {
                "name": "office-01",
                "target": "Admin@192.0.2.10",
                "approved": True,
                "groups": ["office"],
            },
            {"name": "unapproved", "target": "Admin@192.0.2.11", "approved": False},
        ],
    }
    data.update(overrides)
    path = tmp_path / "fleet.json"
    path.write_text(json.dumps(data))
    return path


def test_only_approved_hosts_are_resolved_and_groups_are_explicit(tmp_path):
    config = FleetConfig.load(config_file(tmp_path))
    assert config.known_hosts == tmp_path / "known_hosts"
    assert [h.name for h in config.select(group="office")] == ["office-01"]
    assert config.resolve("Admin@192.0.2.10").name == "office-01"
    with pytest.raises(ValueError, match="approved scope"):
        config.resolve("unapproved")
    for hosts, group in [
        (None, None),
        (["office-01"], "office"),
        (None, "missing"),
        (["office-01", "office-01"], None),
    ]:
        with pytest.raises(ValueError):
            config.select(hosts, group)


@pytest.mark.parametrize(
    "change",
    [
        {"client": "../other"},
        {"concurrency": 0},
        {"concurrency": True},
        {"query_timeout_seconds": 10000},
        {"known_hosts": ""},
        {"hosts": [{"name": "x", "target": "no-user", "approved": True}]},
        {"hosts": [{"name": "x", "target": "u@bad;host", "approved": True}]},
        {
            "hosts": [
                {"name": "x", "target": "u@192.0.2.1", "approved": True},
                {"name": "X", "target": "u@192.0.2.2", "approved": True},
            ]
        },
    ],
)
def test_invalid_scope_is_rejected(tmp_path, change):
    with pytest.raises(ValueError):
        FleetConfig.load(config_file(tmp_path, **change))


def test_missing_host_key_file_is_rejected(tmp_path):
    path = config_file(tmp_path)
    (tmp_path / "known_hosts").unlink()
    with pytest.raises(ValueError, match="known_hosts"):
        FleetConfig.load(path)
