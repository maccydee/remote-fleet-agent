from unittest.mock import AsyncMock

import pytest

from remote_fleet_agent.connection_pool import ConnectionPool


@pytest.mark.asyncio
async def test_get_reuses_existing_connection():
    connector = AsyncMock(side_effect=lambda host, key: f"CONN-{host}")
    pool = ConnectionPool(connector=connector, key_path="/key")
    c1 = await pool.get("asset1.local")
    c2 = await pool.get("asset1.local")
    assert c1 == c2 == "CONN-asset1.local"
    assert connector.call_count == 1


@pytest.mark.asyncio
async def test_get_tracks_distinct_hosts_separately():
    connector = AsyncMock(side_effect=lambda host, key: f"CONN-{host}")
    pool = ConnectionPool(connector=connector, key_path="/key")
    c1 = await pool.get("asset1.local")
    c2 = await pool.get("asset2.local")
    assert c1 != c2


@pytest.mark.asyncio
async def test_close_all_closes_every_connection():
    conns = {}

    async def connector(host, key):
        conns[host] = AsyncMock()
        return conns[host]

    pool = ConnectionPool(connector=connector, key_path="/key")
    await pool.get("asset1.local")
    await pool.get("asset2.local")
    await pool.close_all()
    for conn in conns.values():
        conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_simultaneous_requests_share_one_connection():
    import asyncio

    async def connect(*_):
        await asyncio.sleep(0.01)
        return "one-connection"

    connector = AsyncMock(side_effect=connect)
    pool = ConnectionPool(connector=connector, key_path="/key")
    values = await asyncio.gather(*(pool.get("pc") for _ in range(10)))
    assert values == ["one-connection"] * 10
    connector.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_style_sync_close_and_async_wait_are_supported():
    from types import SimpleNamespace
    from unittest.mock import Mock

    connection = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
    pool = ConnectionPool(connector=AsyncMock(return_value=connection), key_path="/key")
    await pool.get("pc")
    await pool.close_all()
    connection.close.assert_called_once()
    connection.wait_closed.assert_awaited_once()
