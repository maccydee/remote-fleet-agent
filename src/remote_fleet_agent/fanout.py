from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def run_on_hosts(
    hosts: list[str], fn: Callable[[str], Awaitable[object]]
) -> dict[str, object]:
    """Run fn(host) concurrently across every host.

    (WHY: return_exceptions=True so one unreachable machine in a 30-host
    sweep doesn't take down the other 29 results - the caller gets the
    exception back as a value and decides what to report for that host.)
    """
    if not hosts:
        return {}
    results = await asyncio.gather(*(fn(h) for h in hosts), return_exceptions=True)
    return dict(zip(hosts, results))


class ScopeMismatchError(Exception):
    """Raised when the confirmed host scope does not exactly match what was requested."""


async def run_on_hosts_confirmed(
    *,
    requested: list[str],
    confirmed: list[str],
    fn: Callable[[str], Awaitable[object]],
) -> dict[str, object]:
    """Run fn(host) across requested hosts, but only after confirmed matches exactly.

    (WHY: for heavy/destructive operations the engineer must see and confirm
    the EXACT host list before anything runs - a subset or superset is a
    scope mismatch, not a close-enough approval, so nothing executes.)
    """
    if set(requested) != set(confirmed):
        raise ScopeMismatchError(
            f"confirmed scope {sorted(confirmed)} did not match requested scope {sorted(requested)}"
        )
    return await run_on_hosts(requested, fn)
