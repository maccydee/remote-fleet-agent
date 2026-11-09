"""Baseline contracts for the operations projection before its implementation."""

import json
from typing import Any

import pytest

from remote_fleet_agent import runtime
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.storage import digest
from tests.conftest_support import SupportEnvironment
from tests.test_support import investigated, proposed


async def test_projection_fixture_never_connects(
    support_env: SupportEnvironment,
) -> None:
    service, owner, tech, _ = support_env
    case = service.support.create(
        tech,
        "northstar-demo",
        {"host": "windows-01", "symptom": "Example failure", "profile": "general"},
    )
    assert service.support.get(owner, "northstar-demo", case["id"])["revision"] == 1


async def test_shared_fixture_forbids_endpoint_ssh(
    support_env: SupportEnvironment,
) -> None:
    with pytest.raises(pytest.fail.Exception, match="never connect to endpoint SSH"):
        await runtime.connect("must-not-resolve.invalid")


async def test_shared_fixture_uses_real_scoped_roles(
    support_env: SupportEnvironment,
    support_technician_credentials: dict[str, str],
) -> None:
    service, owner, tech, _ = support_env
    assert service.store.authenticate(support_technician_credentials["token"]) == tech
    assert owner["role"] == "owner" and tech["role"] == "technician"
    assert service.store.integrity
    case = service.support.create(
        owner,
        "northstar-demo",
        {"host": "linux-04", "symptom": "Private warehouse case", "profile": "general"},
    )
    with pytest.raises(Denied):
        service.support.get(tech, "northstar-demo", case["id"])
    with pytest.raises(Denied):
        service.store.require(tech, "approve_repairs")


@pytest.mark.parametrize("omit_optional_fields", [False, True])
async def test_existing_case_survives_reopen_without_rewriting_plan(
    support_env: SupportEnvironment,
    omit_optional_fields: bool,
) -> None:
    service, owner, tech, directory = support_env
    case = proposed(service, tech, await investigated(service, tech, "general"))
    assert case["evidence"]["simulation"]
    assert not service.support.failures
    if omit_optional_fields:
        # Model an older stored record, with no prospective operations metadata.
        row = service.store.db.execute(
            "SELECT data FROM records WHERE kind='support' AND id=?", (case["id"],)
        ).fetchone()
        data: dict[str, Any] = json.loads(row["data"])
        for key in (
            "assignee",
            "provider",
            "engineer_id",
            "engineer_name",
            "investigation_history",
            "repair_history",
        ):
            data.pop(key, None)
        with service.store.db:
            service.store.db.execute(
                "UPDATE records SET data=? WHERE id=?", (json.dumps(data), case["id"])
            )
    original_id, original_revision = case["id"], case["revision"]
    original_repair = case["repair"]
    reopened = LiveService(directory)
    try:
        fresh_owner = reopened.store.operator(owner["id"])
        loaded = reopened.support.get(fresh_owner, "northstar-demo", original_id)
        assert loaded["id"] == original_id
        assert loaded["revision"] == original_revision
        assert loaded["repair"] == original_repair
        assert digest(loaded["repair"]["payload"]) == original_repair["digest"]
        assert reopened.support.validate_plan(
            fresh_owner, "northstar-demo", loaded, original_repair["digest"]
        ).simulation
        presented = reopened.support.present(loaded)
        assert presented["repair_history_count"] == 0
        assert presented["investigation_history_count"] == 0
        summaries = reopened.support.list(fresh_owner, "northstar-demo")
        assert summaries[0]["id"] == original_id
        assert summaries[0]["revision"] == original_revision
    finally:
        await reopened.close()
