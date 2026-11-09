"""Bounded, fixed PowerShell queries. No caller-supplied script text."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from remote_fleet_agent.ssh_client import run_powershell


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def select_queries(queries: dict[str, str], sections: list[str] | None) -> list[str]:
    selected = list(queries) if sections is None else sections
    if (
        not selected
        or len(set(selected)) != len(selected)
        or any(s not in queries for s in selected)
    ):
        raise ValueError(
            f"sections must be a nonempty unique selection from {list(queries)}"
        )
    return selected


async def collect(
    conn,
    kind: str,
    queries: dict[str, str],
    sections: list[str] | None = None,
    timeout: int = 30,
    limit: int = 500,
) -> dict:
    selected = select_queries(queries, sections)
    report = {
        "schema": "fleet-evidence-v1",
        "kind": kind,
        "started_at": timestamp(),
        "sections": {},
    }
    for section in selected:
        script = (
            "$ErrorActionPreference='Stop'; try { $rows=@(& { "
            + queries[section]
            + " }); "
            f"[pscustomobject]@{{status='collected';items=@($rows | Select-Object -First {limit});"
            f"total=$rows.Count;truncated=($rows.Count -gt {limit})}} "
            "| ConvertTo-Json -Depth 7 -Compress } catch { "
            "[pscustomobject]@{status='unknown';error=$_.Exception.Message;items=@()} "
            "| ConvertTo-Json -Depth 4 -Compress }"
        )
        try:
            result = await asyncio.wait_for(
                run_powershell(conn, script), timeout=timeout
            )
            if result.exit_code:
                raise RuntimeError(
                    result.stderr[:1000] or f"query exited with code {result.exit_code}"
                )
            data = json.loads(result.stdout.lstrip("\ufeff"))
            if (
                not isinstance(data, dict)
                or data.get("status") not in ("collected", "unknown")
                or not isinstance(data.get("items"), list)
            ):
                raise ValueError("query returned an unexpected evidence shape")
            report["sections"][section] = data
        except TimeoutError:
            report["sections"][section] = {
                "status": "unknown",
                "error": f"query exceeded {timeout} seconds",
                "items": [],
            }
        except Exception as exc:  # noqa: BLE001 - each section failure must remain unknown without losing collected evidence
            report["sections"][section] = {
                "status": "unknown",
                "error": str(exc)[:1000],
                "items": [],
            }
    report["finished_at"] = timestamp()
    report["status"] = (
        "complete"
        if all(s["status"] == "collected" for s in report["sections"].values())
        else "partial"
    )
    report["assessment"] = (
        "Evidence collection only; unknown and empty results are not compliance passes."
    )
    return report
