from __future__ import annotations

from dataclasses import dataclass

from remote_fleet_agent.ssh_client import ps_quote, run_powershell


@dataclass
class FitResult:
    fits: bool
    source_bytes: int
    dest_free_bytes: int


async def fit_check(conn, *, source_path: str, dest_drive: str) -> FitResult:
    size_script = (
        f"(Get-ChildItem -Path {ps_quote(source_path)} -Recurse -File -ErrorAction Stop "
        "| Measure-Object -Property Length -Sum).Sum"
    )
    size_result = await run_powershell(conn, size_script)
    if size_result.exit_code != 0:
        raise RuntimeError(size_result.stderr or "failed to measure source size")

    free_script = f"(Get-PSDrive {ps_quote(dest_drive)}).Free"
    free_result = await run_powershell(conn, free_script)
    if free_result.exit_code != 0:
        raise RuntimeError(free_result.stderr or "failed to read destination free space")

    source_bytes = int(size_result.stdout.strip() or 0)
    dest_free_bytes = int(free_result.stdout.strip() or 0)
    return FitResult(
        fits=source_bytes <= dest_free_bytes,
        source_bytes=source_bytes,
        dest_free_bytes=dest_free_bytes,
    )
