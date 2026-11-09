"""Shared simulated workspaces; endpoint SSH is forbidden unless explicitly replaced."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from remote_fleet_agent.live_app import seed_demo
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.storage import read_json

SupportEnvironment = tuple[LiveService, dict[str, Any], dict[str, Any], Path]


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    return seed_demo(tmp_path / "workspace")


@pytest.fixture
def support_technician_credentials() -> dict[str, str]:
    """Keep the generated technician token in memory for legacy MCP callers."""
    return {}


@pytest.fixture
async def support_env(
    directory: Path,
    monkeypatch: pytest.MonkeyPatch,
    support_technician_credentials: dict[str, str],
) -> AsyncIterator[SupportEnvironment]:
    async def forbidden(*args: Any, **kwargs: Any) -> None:
        pytest.fail("Simulated workspace tests must never connect to endpoint SSH")

    monkeypatch.setattr("remote_fleet_agent.runtime.connect", forbidden)
    service = LiveService(directory)
    try:
        owner = service.store.authenticate(
            read_json(directory / ".state/owner-access.json")["token"]
        )
        tech, token = service.store.add_operator(
            "AI technician", "technician", {"northstar-demo": ["head-office"]}, owner
        )
        support_technician_credentials["token"] = token
        yield service, owner, tech, directory
    finally:
        await service.close()
