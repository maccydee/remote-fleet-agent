from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from remote_fleet_agent.ssh_client import ps_quote, run_powershell

# (WHY: robocopy's exit code is a bitmask, not a simple 0/nonzero — codes
# 0-7 are all "succeeded with varying detail", 8+ means at least one real
# failure. Treating any nonzero as fatal would reject perfectly good runs.)
ROBOCOPY_FAILURE_THRESHOLD = 8

_FILES_LINE = re.compile(r"Files\s*:\s*(\d+)\s+(\d+)\s+(\d+)")


@dataclass
class CopyPlan:
    files_total: int
    files_to_copy: int
    files_skipped: int


async def dry_run_copy(conn, *, source: str, dest: str) -> CopyPlan:
    script = (
        f"robocopy {ps_quote(source)} {ps_quote(dest)} /L /E /NFL /NDL /NJH; "
        "exit $LASTEXITCODE"
    )
    result = await run_powershell(conn, script)
    if result.exit_code >= ROBOCOPY_FAILURE_THRESHOLD:
        raise RuntimeError(result.stderr or f"robocopy failed with exit code {result.exit_code}")

    match = _FILES_LINE.search(result.stdout)
    if not match:
        raise RuntimeError(f"could not parse robocopy summary:\n{result.stdout}")
    total, copied, skipped = (int(g) for g in match.groups())
    return CopyPlan(files_total=total, files_to_copy=copied, files_skipped=skipped)


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _drive_letter(path: str) -> str:
    return path.split(":", 1)[0].upper()


@dataclass
class QuarantineResult:
    quarantine_dir: str
    moved_count: int


async def quarantine_move(conn, *, paths: list[str]) -> QuarantineResult:
    if not paths:
        raise ValueError("paths must not be empty")
    drives = {_drive_letter(p) for p in paths}
    if len(drives) > 1:
        raise ValueError(f"all paths must be on the same volume, got drives {sorted(drives)}")
    drive = drives.pop()

    base_dir = f"{drive}:\\_quarantine\\{_utc_timestamp()}"
    candidate = base_dir
    check = await run_powershell(conn, f"Test-Path {ps_quote(candidate)}")
    if check.stdout.strip() == "True":
        # (WHY: a quarantine folder named by UTC second can already exist
        # from an earlier run in the same second; one collision bump is
        # all the design calls for — see behavioral contract.)
        candidate = f"{base_dir}-2"

    move_lines = [f"New-Item -ItemType Directory -Force -Path {ps_quote(candidate)} | Out-Null"]
    for path in paths:
        move_lines.append(f"Move-Item -Path {ps_quote(path)} -Destination {ps_quote(candidate)} -Force")
    result = await run_powershell(conn, "; ".join(move_lines))
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or "quarantine move failed")

    return QuarantineResult(quarantine_dir=candidate, moved_count=len(paths))
