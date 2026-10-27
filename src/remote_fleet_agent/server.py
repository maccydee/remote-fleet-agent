from __future__ import annotations

import os
from dataclasses import asdict

from mcp.server.mcpserver import MCPServer

from remote_fleet_agent.connection_pool import ConnectionPool
from remote_fleet_agent.ssh_client import connect
from remote_fleet_agent.tools.file_ops import dry_run_copy as _dry_run_copy
from remote_fleet_agent.tools.file_ops import quarantine_move as _quarantine_move
from remote_fleet_agent.tools.fit_check import fit_check as _fit_check
from remote_fleet_agent.tools.gdpr_scan import gdpr_scan as _gdpr_scan
from remote_fleet_agent.tools.run_check import run_check as _run_check


def build_server(key_path: str) -> MCPServer:
    """Build the MCP server exposing exactly the five safe tools.

    (WHY: every handler below resolves `host` through the ConnectionPool
    before calling into tools.*, and returns plain dicts/lists built from
    the tools' dataclasses - there is deliberately no tool here that runs
    an arbitrary command or deletes a file. That boundary is the whole
    point of the laptop-side server design in DESIGN.md.)
    """
    server = MCPServer("remote-fleet-agent")
    pool = ConnectionPool(connector=connect, key_path=key_path)

    @server.tool()
    async def run_check(host: str, check_name: str) -> dict:
        conn = await pool.get(host)
        return await _run_check(conn, check_name)

    @server.tool()
    async def fit_check(host: str, source_path: str, dest_drive: str) -> dict:
        conn = await pool.get(host)
        result = await _fit_check(conn, source_path=source_path, dest_drive=dest_drive)
        return asdict(result)

    @server.tool()
    async def dry_run_copy(host: str, source: str, dest: str) -> dict:
        conn = await pool.get(host)
        result = await _dry_run_copy(conn, source=source, dest=dest)
        return asdict(result)

    @server.tool()
    async def quarantine_move(host: str, paths: list[str]) -> dict:
        conn = await pool.get(host)
        result = await _quarantine_move(conn, paths=paths)
        return asdict(result)

    @server.tool()
    async def gdpr_scan(host: str, root_path: str) -> list[dict]:
        conn = await pool.get(host)
        findings = await _gdpr_scan(conn, root_path=root_path)
        return [asdict(f) for f in findings]

    return server


async def main() -> None:
    key_path = os.environ.get("REMOTE_FLEET_AGENT_KEY_PATH")
    if not key_path:
        raise RuntimeError(
            "REMOTE_FLEET_AGENT_KEY_PATH must be set to the active client's private key "
            "(see README.md for the per-engagement workflow)"
        )
    server = build_server(key_path=key_path)
    await server.run_stdio_async()
