"""Register shared workspace fixtures for service and browser suites."""

pytest_plugins = ["tests.conftest_support"]


import pytest


@pytest.fixture(autouse=True)
def isolated_endpoint_lock_directory(tmp_path, monkeypatch):
    """Never leave test exclusion claims in the engineer's real lock namespace."""
    monkeypatch.setenv(
        "REMOTE_FLEET_ENDPOINT_LOCK_DIR", str(tmp_path / "endpoint-locks")
    )
