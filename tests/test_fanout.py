import pytest

from remote_fleet_agent.fanout import (
    ScopeMismatchError,
    run_on_hosts,
    run_on_hosts_confirmed,
)


@pytest.mark.asyncio
async def test_all_hosts_succeed():
    async def fn(host):
        return f"result-{host}"

    results = await run_on_hosts(["a", "b", "c"], fn)
    assert results == {"a": "result-a", "b": "result-b", "c": "result-c"}


@pytest.mark.asyncio
async def test_one_host_failing_does_not_break_others():
    async def fn(host):
        if host == "b":
            raise RuntimeError("unreachable")
        return f"result-{host}"

    results = await run_on_hosts(["a", "b", "c"], fn)
    assert results["a"] == "result-a"
    assert results["c"] == "result-c"
    assert isinstance(results["b"], RuntimeError)


@pytest.mark.asyncio
async def test_empty_host_list():
    async def fn(host):
        return host

    assert await run_on_hosts([], fn) == {}


@pytest.mark.asyncio
async def test_confirmed_scope_matching_requested_runs():
    async def fn(host):
        return host

    results = await run_on_hosts_confirmed(requested=["a", "b"], confirmed=["a", "b"], fn=fn)
    assert results == {"a": "a", "b": "b"}


@pytest.mark.asyncio
async def test_confirmed_subset_raises():
    async def fn(host):
        return host

    with pytest.raises(ScopeMismatchError, match="did not match"):
        await run_on_hosts_confirmed(requested=["a", "b", "c"], confirmed=["a", "b"], fn=fn)


@pytest.mark.asyncio
async def test_confirmed_superset_raises():
    async def fn(host):
        return host

    with pytest.raises(ScopeMismatchError, match="did not match"):
        await run_on_hosts_confirmed(requested=["a", "b"], confirmed=["a", "b", "c"], fn=fn)
