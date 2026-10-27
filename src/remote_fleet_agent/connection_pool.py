from __future__ import annotations

from collections.abc import Awaitable, Callable


class ConnectionPool:
    """Per-host SSH connection cache with coordinated shutdown.

    WHY: fan-out operations (running a check or copy across many assets)
    call the same host repeatedly within a session. Reconnecting on every
    call would add connection-setup latency to each one and churn the
    target's sshd for no benefit, since the earlier connection is still
    good. This pool opens a connection to a host once, hands the same
    object back to every later caller for that host, and gives a single
    place (`close_all`) to tear every open connection down cleanly when
    the server shuts down.
    """

    def __init__(
        self,
        connector: Callable[[str, str], Awaitable[object]],
        key_path: str,
    ) -> None:
        self._connector = connector
        self._key_path = key_path
        self._connections: dict[str, object] = {}

    async def get(self, host: str) -> object:
        """Return the cached connection for `host`, connecting on first use."""
        if host not in self._connections:
            self._connections[host] = await self._connector(host, self._key_path)
        return self._connections[host]

    async def close_all(self) -> None:
        """Close every tracked connection and clear the pool."""
        for connection in self._connections.values():
            await connection.close()
        self._connections.clear()
