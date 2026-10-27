from __future__ import annotations

import json
from dataclasses import dataclass

from remote_fleet_agent.ssh_client import ps_quote, run_powershell

# (WHY: these regexes run ON THE TARGET inside the PowerShell script below.
# Only match TYPE, COUNT, and file PATH are ever returned - never the
# matched substring - so personal data never leaves the target machine.
# See ASSUMPTIONS.md A-001 for why these three patterns were chosen.)
_PATTERNS = {
    "ni_number": r"\b(?!BG|GB|NK|KN|TN|NT|ZZ)[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\d{6}[A-D]\b",
    "card_number": r"\b(?:\d[ -]*?){13,19}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
}


@dataclass
class GdprFinding:
    path: str
    match_type: str
    count: int


async def gdpr_scan(conn, *, root_path: str) -> list[GdprFinding]:
    pattern_assignments = "; ".join(
        f"${name} = {ps_quote(pattern)}" for name, pattern in _PATTERNS.items()
    )
    script = f"""
{pattern_assignments}
$findings = @()
Get-ChildItem -Path {ps_quote(root_path)} -Recurse -File -ErrorAction Stop | ForEach-Object {{
    $text = Get-Content -Raw -ErrorAction SilentlyContinue $_.FullName
    if ($text) {{
        foreach ($name in @('ni_number','card_number','email')) {{
            $pattern = Get-Variable -Name $name -ValueOnly
            $matchCount = ([regex]::Matches($text, $pattern)).Count
            if ($matchCount -gt 0) {{
                $findings += [PSCustomObject]@{{path=$_.FullName; match_type=$name; count=$matchCount}}
            }}
        }}
    }}
}}
$findings | ConvertTo-Json -Compress
"""
    result = await run_powershell(conn, script)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or "gdpr_scan failed")
    raw = result.stdout.strip() or "[]"
    parsed = json.loads(raw)
    if isinstance(parsed, dict):  # PowerShell emits a bare object, not a list, for a single result
        parsed = [parsed]
    return [
        GdprFinding(path=f["path"], match_type=f["match_type"], count=f["count"])
        for f in parsed
    ]
