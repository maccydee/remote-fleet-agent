import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from remote_fleet_agent.cli import main


def files(tmp_path):
    known = tmp_path / "known"
    known.write_text("verified")
    key = tmp_path / "key"
    key.write_text("fixture-not-a-key")
    config = tmp_path / "fleet.json"
    config.write_text(json.dumps({"client": "test", "known_hosts": str(known)}))
    return config, key


@pytest.mark.parametrize(
    "mode,status,expected_exit",
    [("execute", "unknown", 2), ("rollback", "rolled_back", 0)],
)
def test_cli_routes_change_and_returns_uncertainty_exit_code(
    tmp_path, mode, status, expected_exit
):
    config, key = files(tmp_path)
    runtime = Mock()
    runtime.close = AsyncMock()
    runtime.maintenance.execute = AsyncMock(
        return_value={"id": "a" * 32, "status": status}
    )
    args = [
        "remote-fleet-agent",
        "maintenance",
        mode,
        "--config",
        str(config),
        "--key-path",
        str(key),
        "--plan-id",
        "a" * 32,
        "--digest",
        "reviewed",
    ]
    with (
        patch("sys.argv", args),
        patch("remote_fleet_agent.runtime.FleetRuntime", return_value=runtime),
        pytest.raises(SystemExit) as exit,
    ):
        main()
    assert exit.value.code == expected_exit
    runtime.maintenance.execute.assert_awaited_once_with(
        "a" * 32, "reviewed", rollback=mode == "rollback"
    )
    runtime.close.assert_awaited_once()


def test_cli_rejects_missing_digest_before_runtime(tmp_path):
    config, key = files(tmp_path)
    args = [
        "remote-fleet-agent",
        "maintenance",
        "execute",
        "--config",
        str(config),
        "--key-path",
        str(key),
        "--plan-id",
        "a" * 32,
    ]
    with (
        patch("sys.argv", args),
        patch("remote_fleet_agent.runtime.FleetRuntime") as runtime,
    ):
        with pytest.raises(SystemExit) as exit:
            main()
        assert exit.value.code == 2
        runtime.assert_not_called()
