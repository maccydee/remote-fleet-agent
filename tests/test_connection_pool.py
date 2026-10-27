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
