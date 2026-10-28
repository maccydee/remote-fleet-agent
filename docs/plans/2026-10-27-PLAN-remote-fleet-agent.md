# Remote Fleet Agent Implementation Plan

> **For the executor:** implement this task-by-task with the executing-plans skill.

**Goal:** Build a laptop-side MCP server that lets Claude run Cyber
Essentials/DCC-style audits and safe file operations (quarantine instead of
delete, dry-run copies, GDPR-pattern file discovery) against Windows machines
over SSH, deployed via a per-client USB stick with a self-expiring install.

**Architecture:** One Python MCP server runs on the engineer's own laptop.
It holds a per-client host registry, opens SSH connections (Windows OpenSSH)
to run PowerShell on targets, and exposes a small fixed set of safe tools —
`run_check`, `fit_check`, `dry_run_copy`, `quarantine_move`, `gdpr_scan` —
never raw shell or delete. Deployment to each target is two PowerShell
scripts carried on a per-client USB stick: `install.ps1` (enables OpenSSH,
adds the engineer's key, scopes a firewall rule, sets a 3-day self-destruct
scheduled task) and `uninstall.ps1` (reverses all of it).

**Tech Stack:** Python 3.11+, `mcp` (official Python MCP SDK), `asyncssh`,
`pytest` + `pytest-asyncio`, `ruff`; PowerShell 5.1+ with `Pester` for the
deployment scripts (run cross-platform via `pwsh` for logic tests; real
Windows-API behaviour is verified manually — see Task 22).

**Source design:** [`docs/plans/2026-10-27-DESIGN-remote-fleet-agent.md`](2026-10-27-DESIGN-remote-fleet-agent.md)
**Assumptions made while planning:** [`ASSUMPTIONS.md`](../../ASSUMPTIONS.md)

## Progress

| Status | Count |
|--------|-------|
| 🔴 NOT_STARTED | 1 |
| 🟡 IN_PROGRESS | 0 |
| 🟢 COMPLETED | 22 |
| ⚪ BLOCKED | 0 |

Only Task 22 (manual verification on a real Windows machine) remains —
it cannot be done from this Mac by design. See that task's section below.

## Production readiness

This runs as a local stdio MCP server, spawned by Claude Code on the
engineer's own laptop — not a multi-tenant network service — so most
production-service concerns are N/A by nature. Dispositions, domain by
domain:

1. **Observability** — New task added: Task 5 gives the SSH layer structured
   connect/disconnect/error logging; this doubles as the audit trail the
   design's uninstall-receipt concept depends on.
2. **Health & Lifecycle** — Covered by Task 6 (connection pool closes
   cleanly on server shutdown; no readiness/liveness endpoints needed — a
   stdio server has no listener to report ready on).
3. **Resilience** — Covered by Task 5 (connect timeout + one retry with
   backoff on every outbound SSH call).
4. **Security** — Covered by Task 4 (safe, injection-resistant parameter
   passing into PowerShell — this is the one domain where getting it wrong
   is genuinely dangerous) and Task 11 (GDPR scan never returns file
   content). Authorization is the SSH key itself — no separate auth layer
   needed since the MCP server and its one caller (the engineer's own
   Claude Code) are the same trust boundary.
5. **Configuration** — Covered by Task 2 (per-client registry files under
   `~/.remote-fleet-agent/`) and Task 15 (keys generated and stored outside
   the repo, never committed).
6. **Deployment** — N/A: local developer tool, installed with
   `pip install -e .` on the engineer's own laptop only. No container, no
   rollback path beyond `git revert` on this repo.

## Task 1: Project scaffolding 🟢 COMPLETED

**Files:**
- Create: `pyproject.toml`
- Create: `src/remote_fleet_agent/__init__.py`
- Create: `tests/__init__.py`
- Create: `.gitignore`

**Language Rules (Python):**
- Testing: use `pytest`; write the failing test first (RED), then minimal
  implementation (GREEN). Run `pytest <path>::<test> -v` and expect PASS
  with no warnings.
- Types: add type hints on public functions; run `mypy`/`pyright` if
  configured and expect it to pass.
- Formatting/linting: code must pass `ruff`.
- Imports: prefer stdlib; lazy-import optional heavy dependencies inside
  the function that needs them.
- Errors: raise specific exceptions with actionable messages.
- Environments: assume a virtualenv; pin dependencies in `pyproject.toml`.

This task has no behaviour to test yet — it's the scaffold the rest of the
plan builds on.

**Step 1: Write `pyproject.toml`**
```toml
[project]
name = "remote-fleet-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "mcp>=1.0.0",
    "asyncssh>=2.14.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "ruff>=0.6"]

[project.scripts]
remote-fleet-agent = "remote_fleet_agent.cli:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

**Step 2: Create empty package/test `__init__.py` files and `.gitignore`**
```
# .gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
```

**Step 3: Verify the scaffold**
Run: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"`
Expected: installs cleanly with no errors.
Run: `.venv/bin/pytest`
Expected: `no tests ran` (collection succeeds, 0 errors).
Run: `.venv/bin/ruff check .`
Expected: `All checks passed!`

**Step 4: Commit**
```bash
git add pyproject.toml src/remote_fleet_agent/__init__.py tests/__init__.py .gitignore
git commit -m "chore: project scaffolding"
```

## Task 2: Host registry — add, resolve, list 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/registry.py`
- Test: `tests/test_registry.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `add_host("asset1.local", "asset-1")` then `resolve("asset-1")` | `Host(hostname="asset1.local", nickname="asset-1", ...)` |
| `add_host("asset1.local", "asset-1")` then `resolve("asset1.local")` | same `Host` (resolve matches hostname too) |
| `resolve("nope")` with nothing registered | raises `HostNotFoundError` containing `"not found"` |
| `add_host(...)` twice, then `list_hosts()` | list of length 2, in insertion order |

**Step 1: Write the failing tests (RED)**
```python
# tests/test_registry.py
import pytest
from remote_fleet_agent.registry import ClientRegistry, HostNotFoundError

def test_add_then_resolve_by_nickname(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    host = reg.resolve("asset-1")
    assert host.hostname == "asset1.local"
    assert host.nickname == "asset-1"

def test_resolve_by_hostname_also_works(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    host = reg.resolve("asset1.local")
    assert host.nickname == "asset-1"

def test_resolve_missing_raises(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    with pytest.raises(HostNotFoundError, match="not found"):
        reg.resolve("nope")

def test_list_hosts_preserves_insertion_order(tmp_path):
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("asset1.local", "asset-1")
    reg.add_host("asset2.local", "asset-2")
    hosts = reg.list_hosts()
    assert [h.nickname for h in hosts] == ["asset-1", "asset-2"]
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_registry.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'remote_fleet_agent.registry'`).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/registry.py
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_REGISTRY_DIR = Path.home() / ".remote-fleet-agent" / "clients"


class HostNotFoundError(Exception):
    pass


@dataclass
class Host:
    hostname: str
    nickname: str
    added_at: float
    last_seen: float | None = None


class ClientRegistry:
    """Per-client host list, persisted as JSON at <base_dir>/<client>.json."""

    def __init__(self, client: str, base_dir: Path = DEFAULT_REGISTRY_DIR) -> None:
        self._client = client
        self._path = Path(base_dir) / f"{client}.json"

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        return json.loads(self._path.read_text())

    def _save(self, hosts: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(hosts, indent=2))

    def add_host(self, hostname: str, nickname: str) -> Host:
        hosts = self._load()
        host = Host(hostname=hostname, nickname=nickname, added_at=time.time())
        hosts.append(asdict(host))
        self._save(hosts)
        return host

    def resolve(self, name: str) -> Host:
        for raw in self._load():
            if raw["nickname"] == name or raw["hostname"] == name:
                return Host(**raw)
        raise HostNotFoundError(
            f"host '{name}' not found for client '{self._client}'"
        )

    def list_hosts(self) -> list[Host]:
        return [Host(**raw) for raw in self._load()]
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_registry.py -v`
Expected: 4 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/registry.py tests/test_registry.py
git commit -m "feat: host registry with add/resolve/list"
```

## Task 3: Host registry — duplicate nicknames and client isolation 🟢 COMPLETED

**Files:**
- Modify: `src/remote_fleet_agent/registry.py`
- Test: `tests/test_registry.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `add_host("a.local","x")` then `add_host("b.local","x")` (same client) | raises `DuplicateNicknameError` containing `"already"` |
| client `"acme"` has `"asset-1"`; client `"globex"` adds `"asset-1"` too | both succeed — nicknames are scoped per client, not global |

**Step 1: Write the failing tests (RED)**
```python
def test_duplicate_nickname_rejected(tmp_path):
    from remote_fleet_agent.registry import DuplicateNicknameError
    reg = ClientRegistry("acme", base_dir=tmp_path)
    reg.add_host("a.local", "x")
    with pytest.raises(DuplicateNicknameError, match="already"):
        reg.add_host("b.local", "x")

def test_nicknames_scoped_per_client(tmp_path):
    acme = ClientRegistry("acme", base_dir=tmp_path)
    globex = ClientRegistry("globex", base_dir=tmp_path)
    acme.add_host("a.local", "asset-1")
    globex.add_host("g.local", "asset-1")  # must not raise
    assert acme.resolve("asset-1").hostname == "a.local"
    assert globex.resolve("asset-1").hostname == "g.local"
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_registry.py -v`
Expected: `test_duplicate_nickname_rejected` FAILs (no `DuplicateNicknameError` exported); the isolation test passes already since each registry already uses its own file.

**Step 3: Minimal implementation (GREEN)**
```python
# add to registry.py
class DuplicateNicknameError(Exception):
    pass

# inside ClientRegistry.add_host, before building the Host:
    def add_host(self, hostname: str, nickname: str) -> Host:
        hosts = self._load()
        if any(h["nickname"] == nickname for h in hosts):
            raise DuplicateNicknameError(
                f"nickname '{nickname}' already registered for client '{self._client}'"
            )
        host = Host(hostname=hostname, nickname=nickname, added_at=time.time())
        hosts.append(asdict(host))
        self._save(hosts)
        return host
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_registry.py -v`
Expected: 6 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/registry.py tests/test_registry.py
git commit -m "feat: reject duplicate nicknames, keep them scoped per client"
```

## Task 4: SSH client — safe PowerShell execution 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/ssh_client.py`
- Test: `tests/test_ssh_client.py`

**Language Rules (Python):** see Task 1.

This is the single most security-sensitive piece of the codebase: every
tool downstream builds a PowerShell command from caller-supplied strings
(paths, hostnames) and sends it over this client. Getting parameter passing
wrong here is a remote-command-injection bug on a client's machine.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `run_powershell(conn, "Write-Output 'ok'")` | `CommandResult(stdout="ok\n", exit_code=0)` |
| a path parameter containing `'; Remove-Item C:\ -Recurse -Force #` | the malicious segment is treated as a literal string value, never executed — command exits 0 and only echoes the literal path |
| the target process exits non-zero | `CommandResult.exit_code != 0`, `stderr` populated, no exception raised (caller decides what non-zero means) |

**Step 1: Write the failing tests (RED)**
```python
# tests/test_ssh_client.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from remote_fleet_agent.ssh_client import run_powershell, build_encoded_command

def test_build_encoded_command_is_base64_powershell():
    cmd = build_encoded_command("Write-Output 'ok'")
    assert cmd.startswith("powershell -NoProfile -NonInteractive -EncodedCommand ")

def test_injection_attempt_is_neutralized_in_script_text():
    malicious = "'; Remove-Item C:\\ -Recurse -Force #"
    script = f"Write-Output {ps_quote(malicious)}"
    # the quoted value must escape embedded single quotes by doubling them,
    # so it can never close the string literal early
    from remote_fleet_agent.ssh_client import ps_quote
    quoted = ps_quote(malicious)
    assert quoted.count("''") >= 1
    assert not quoted.startswith("'; Remove-Item")

@pytest.mark.asyncio
async def test_run_powershell_returns_result():
    fake_process = MagicMock()
    fake_process.stdout = "ok\n"
    fake_process.stderr = ""
    fake_process.exit_status = 0
    conn = MagicMock()
    conn.run = AsyncMock(return_value=fake_process)

    result = await run_powershell(conn, "Write-Output 'ok'")

    assert result.stdout == "ok\n"
    assert result.exit_code == 0
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_ssh_client.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'remote_fleet_agent.ssh_client'`).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/ssh_client.py
from __future__ import annotations

import base64
from dataclasses import dataclass


def ps_quote(value: str) -> str:
    """Quote a value as a single-quoted PowerShell string literal.

    (WHY: PowerShell single-quoted strings treat everything literally
    except an embedded single quote, which must be doubled. This is the
    one escaping rule that makes it safe to embed caller-supplied text
    — paths, hostnames — directly into a script body without it being
    able to terminate the string and inject new commands.)
    """
    return "'" + value.replace("'", "''") + "'"


def build_encoded_command(script: str) -> str:
    """Wrap a PowerShell script as a base64 -EncodedCommand invocation.

    (WHY: passing a script as a plain command-line argument over SSH's
    exec channel means it goes through the remote shell's own quoting
    rules too — a second place injection could happen. Base64-encoding
    the whole script and using PowerShell's native -EncodedCommand skips
    shell quoting entirely: the remote shell sees one opaque token.)
    """
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"


@dataclass
class CommandResult:
    stdout: str
    stderr: str
    exit_code: int


async def run_powershell(conn, script: str) -> CommandResult:
    command = build_encoded_command(script)
    process = await conn.run(command, check=False)
    return CommandResult(
        stdout=process.stdout,
        stderr=process.stderr,
        exit_code=process.exit_status,
    )
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_ssh_client.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/ssh_client.py tests/test_ssh_client.py
git commit -m "feat: safe base64-encoded PowerShell execution over SSH"
```

**Step 6: Refactor** — every tool task below (7–11) must build its
PowerShell scripts using `ps_quote()` for any caller-supplied string
(paths, hostnames, patterns). Flag this in code review; a tool that
string-formats a path into a script without `ps_quote()` reopens the
injection risk this task closed.

## Task 5: SSH client — connect timeout, retry, structured logging 🟢 COMPLETED

**Files:**
- Modify: `src/remote_fleet_agent/ssh_client.py`
- Test: `tests/test_ssh_client.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `connect(host, key_path)` succeeds first try | returns a connection, logs one `ssh.connect.ok` event with `host` and `elapsed_ms` (MEASURED, real wall-clock connect time) |
| first attempt raises, second succeeds | returns a connection after exactly one retry; logs `ssh.connect.retry` then `ssh.connect.ok` |
| both attempts raise | raises the underlying error; logs `ssh.connect.failed` with `host` and `attempts=2` |
| connect takes longer than `timeout` seconds | raises `asyncssh.misc.ConnectionLost` (or asyncio `TimeoutError`) rather than hanging |

**Step 1: Write the failing tests (RED)**
```python
import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, patch
from remote_fleet_agent.ssh_client import connect

@pytest.mark.asyncio
async def test_connect_success_logs_ok(caplog):
    with patch("remote_fleet_agent.ssh_client.asyncssh.connect", AsyncMock(return_value="CONN")):
        with caplog.at_level(logging.INFO):
            conn = await connect("asset1.local", "/key/path", timeout=5)
    assert conn == "CONN"
    assert any("ssh.connect.ok" in r.message for r in caplog.records)

@pytest.mark.asyncio
async def test_connect_retries_once_then_succeeds(caplog):
    calls = {"n": 0}
    async def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("refused")
        return "CONN"
    with patch("remote_fleet_agent.ssh_client.asyncssh.connect", flaky):
        with caplog.at_level(logging.INFO):
            conn = await connect("asset1.local", "/key/path", timeout=5)
    assert conn == "CONN"
    assert calls["n"] == 2
    assert any("ssh.connect.retry" in r.message for r in caplog.records)

@pytest.mark.asyncio
async def test_connect_fails_both_attempts_raises_and_logs(caplog):
    async def always_fails(*a, **k):
        raise OSError("refused")
    with patch("remote_fleet_agent.ssh_client.asyncssh.connect", always_fails):
        with pytest.raises(OSError):
            with caplog.at_level(logging.INFO):
                await connect("asset1.local", "/key/path", timeout=5)
    assert any("ssh.connect.failed" in r.message for r in caplog.records)
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_ssh_client.py -v`
Expected: FAIL (`connect` not defined).

**Step 3: Minimal implementation (GREEN)**
```python
# add to ssh_client.py
import logging
import time

import asyncssh

logger = logging.getLogger("remote_fleet_agent.ssh")


async def connect(host: str, key_path: str, *, timeout: float = 10.0, retries: int = 1):
    attempt = 0
    last_error: Exception | None = None
    while attempt <= retries:
        attempt += 1
        start = time.monotonic()
        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    host,
                    client_keys=[key_path],
                    known_hosts=None,  # (WHY: targets are freshly installed each
                    # engagement with no prior known_hosts entry; host identity is
                    # established by the engineer's physical presence at install
                    # time, not by TOFU key pinning — see DESIGN.md trust section)
                ),
                timeout=timeout,
            )
            elapsed_ms = (time.monotonic() - start) * 1000  # (MEASURED, real connect latency)
            logger.info("ssh.connect.ok host=%s elapsed_ms=%.1f", host, elapsed_ms)
            return conn
        except Exception as exc:  # noqa: BLE001 - deliberately broad, we always log+retry/raise
            last_error = exc
            if attempt <= retries:
                logger.info("ssh.connect.retry host=%s attempt=%d error=%s", host, attempt, exc)
            else:
                logger.info("ssh.connect.failed host=%s attempts=%d error=%s", host, attempt, exc)
    assert last_error is not None
    raise last_error
```
Add `import asyncio` to the top of the file alongside the existing imports.

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_ssh_client.py -v`
Expected: 6 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/ssh_client.py tests/test_ssh_client.py
git commit -m "feat: SSH connect with timeout, single retry, structured logging"
```

## Task 6: SSH client — per-host connection reuse and clean shutdown 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/connection_pool.py`
- Test: `tests/test_connection_pool.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `get(host)` called twice for the same host | second call returns the same connection object, `connect()` was only invoked once |
| `get(host)` for two different hosts | two distinct connections, both tracked |
| `close_all()` | every tracked connection's `.close()` is awaited exactly once |

**Step 1: Write the failing tests (RED)**
```python
# tests/test_connection_pool.py
import pytest
from unittest.mock import AsyncMock
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
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_connection_pool.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/connection_pool.py
from __future__ import annotations

from typing import Callable, Awaitable


class ConnectionPool:
    """Reuses one SSH connection per host for the life of the MCP server
    process, and closes all of them on shutdown.

    (WHY: fan-out (Task 12) calls into the same host repeatedly across a
    session — reconnecting every call would add real latency and connection
    churn on the target's sshd for no benefit.)
    """

    def __init__(self, connector: Callable[[str, str], Awaitable[object]], key_path: str) -> None:
        self._connector = connector
        self._key_path = key_path
        self._connections: dict[str, object] = {}

    async def get(self, host: str):
        if host not in self._connections:
            self._connections[host] = await self._connector(host, self._key_path)
        return self._connections[host]

    async def close_all(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_connection_pool.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/connection_pool.py tests/test_connection_pool.py
git commit -m "feat: per-host SSH connection reuse with clean shutdown"
```

## Task 7: Tool — `run_check` 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/tools/run_check.py`
- Test: `tests/tools/test_run_check.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `run_check(conn, "patch_status")` | dict parsed from the mocked JSON the connection returns |
| `run_check(conn, "not_a_real_check")` | raises `ValueError` containing `"unknown check"` |
| the remote command returns non-zero exit | raises `RuntimeError` containing the stderr text |

**Step 1: Write the failing tests (RED)**
```python
# tests/tools/test_run_check.py
import pytest
from unittest.mock import AsyncMock, patch
from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.run_check import run_check

@pytest.mark.asyncio
async def test_run_check_parses_json_result():
    fake = CommandResult(stdout='{"patched": true, "pending": 2}', stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.run_check.run_powershell", AsyncMock(return_value=fake)):
        result = await run_check("CONN", "patch_status")
    assert result == {"patched": True, "pending": 2}

@pytest.mark.asyncio
async def test_run_check_unknown_name_raises():
    with pytest.raises(ValueError, match="unknown check"):
        await run_check("CONN", "not_a_real_check")

@pytest.mark.asyncio
async def test_run_check_nonzero_exit_raises():
    fake = CommandResult(stdout="", stderr="Access denied", exit_code=1)
    with patch("remote_fleet_agent.tools.run_check.run_powershell", AsyncMock(return_value=fake)):
        with pytest.raises(RuntimeError, match="Access denied"):
            await run_check("CONN", "patch_status")
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/tools/test_run_check.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/tools/run_check.py
from __future__ import annotations

import json

from remote_fleet_agent.ssh_client import run_powershell

# Each check is a self-contained PowerShell one-liner that ends by emitting
# JSON, so every check returns the same shape regardless of what it queries.
CHECKS: dict[str, str] = {
    "patch_status": (
        "$u = (New-Object -ComObject Microsoft.Update.AutoUpdate).Results; "
        "$pending = (Get-WindowsUpdate -ErrorAction SilentlyContinue).Count; "
        "@{patched = ($pending -eq 0); pending = $pending} | ConvertTo-Json -Compress"
    ),
    "av_status": (
        "$s = Get-MpComputerStatus; "
        "@{realTimeProtectionEnabled = $s.RealTimeProtectionEnabled; "
        "antivirusSignatureAge = $s.AntivirusSignatureAge} | ConvertTo-Json -Compress"
    ),
    "firewall_status": (
        "Get-NetFirewallProfile | Select-Object Name, Enabled | ConvertTo-Json -Compress"
    ),
    "local_admins": (
        "Get-LocalGroupMember -Group 'Administrators' | "
        "Select-Object Name, ObjectClass | ConvertTo-Json -Compress"
    ),
}


async def run_check(conn, check_name: str) -> dict:
    if check_name not in CHECKS:
        raise ValueError(f"unknown check '{check_name}', expected one of {sorted(CHECKS)}")
    result = await run_powershell(conn, CHECKS[check_name])
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or f"check '{check_name}' failed with exit {result.exit_code}")
    return json.loads(result.stdout)
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/tools/test_run_check.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/tools/run_check.py tests/tools/test_run_check.py
git commit -m "feat: run_check tool for patch/AV/firewall/local-admin audit checks"
```

## Task 8: Tool — `fit_check` 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/tools/fit_check.py`
- Test: `tests/tools/test_fit_check.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| source totals 5,000,000,000 bytes; destination has 10,000,000,000 free | `FitResult(fits=True, source_bytes=5_000_000_000, dest_free_bytes=10_000_000_000)` |
| source bigger than destination free space | `FitResult(fits=False, ...)` — never raises, the caller decides what to do with a "no" |
| source path does not exist on the target | raises `RuntimeError` containing `"Access"` or the PowerShell error text — never silently reports `fits=True` |

**Step 1: Write the failing tests (RED)**
```python
# tests/tools/test_fit_check.py
import pytest
from unittest.mock import AsyncMock, patch
from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.fit_check import fit_check

@pytest.mark.asyncio
async def test_fit_check_true_when_space_available():
    size_result = CommandResult(stdout="5000000000", stderr="", exit_code=0)
    free_result = CommandResult(stdout="10000000000", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.fit_check.run_powershell", AsyncMock(side_effect=[size_result, free_result])):
        result = await fit_check("CONN", source_path="D:\\Share\\Old", dest_drive="E")
    assert result.fits is True
    assert result.source_bytes == 5_000_000_000
    assert result.dest_free_bytes == 10_000_000_000

@pytest.mark.asyncio
async def test_fit_check_false_when_not_enough_space():
    size_result = CommandResult(stdout="20000000000", stderr="", exit_code=0)
    free_result = CommandResult(stdout="10000000000", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.fit_check.run_powershell", AsyncMock(side_effect=[size_result, free_result])):
        result = await fit_check("CONN", source_path="D:\\Share\\Old", dest_drive="E")
    assert result.fits is False

@pytest.mark.asyncio
async def test_fit_check_raises_on_inaccessible_source():
    error_result = CommandResult(stdout="", stderr="Access to the path is denied", exit_code=1)
    with patch("remote_fleet_agent.tools.fit_check.run_powershell", AsyncMock(return_value=error_result)):
        with pytest.raises(RuntimeError, match="Access"):
            await fit_check("CONN", source_path="D:\\NoAccess", dest_drive="E")
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/tools/test_fit_check.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/tools/fit_check.py
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
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/tools/test_fit_check.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/tools/fit_check.py tests/tools/test_fit_check.py
git commit -m "feat: fit_check tool for pre-flight capacity checks"
```

## Task 9: Tool — `dry_run_copy` 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/tools/file_ops.py`
- Test: `tests/tools/test_file_ops.py`

**Language Rules (Python):** see Task 1.

`robocopy /L` prints a fixed-format summary block; parsing it correctly is
the non-obvious part of this task.

**Behavioral contract:**
| Input (robocopy `/L` stdout) | Expected output |
|-------|-----------------|
| summary block with `Files :  120  115  5  0  0  0` | `CopyPlan(files_total=120, files_to_copy=115, files_skipped=5)` |
| summary block with `Files :    0    0  0  0  0  0` (empty source) | `CopyPlan(files_total=0, files_to_copy=0, files_skipped=0)` |
| robocopy exits with an error-range code (≥16) | raises `RuntimeError` containing the stderr text |

**Step 1: Write the failing tests (RED)**
```python
# tests/tools/test_file_ops.py
import pytest
from unittest.mock import AsyncMock, patch
from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.file_ops import dry_run_copy

ROBOCOPY_SUMMARY = """
-------------------------------------------------------------------------------
                 Total    Copied   Skipped  Mismatch    FAILED    Extras
    Dirs :        10        10         0         0         0         0
   Files :       120       115         5         0         0         0
   Bytes :   1.2 g     1.1 g     0.1 g         0         0         0
"""

@pytest.mark.asyncio
async def test_dry_run_copy_parses_summary():
    fake = CommandResult(stdout=ROBOCOPY_SUMMARY, stderr="", exit_code=1)  # robocopy uses bit-flag exit codes
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)):
        plan = await dry_run_copy("CONN", source="D:\\Share\\Old", dest="D:\\Share\\New")
    assert plan.files_total == 120
    assert plan.files_to_copy == 115
    assert plan.files_skipped == 5

@pytest.mark.asyncio
async def test_dry_run_copy_empty_source():
    empty = ROBOCOPY_SUMMARY.replace("120       115         5", "0         0         0")
    fake = CommandResult(stdout=empty, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)):
        plan = await dry_run_copy("CONN", source="D:\\Share\\Empty", dest="D:\\Share\\New")
    assert plan.files_total == 0

@pytest.mark.asyncio
async def test_dry_run_copy_error_exit_raises():
    fake = CommandResult(stdout="", stderr="ERROR 5: Access is denied.", exit_code=16)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)):
        with pytest.raises(RuntimeError, match="Access is denied"):
            await dry_run_copy("CONN", source="D:\\NoAccess", dest="D:\\Share\\New")
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/tools/test_file_ops.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/tools/file_ops.py
from __future__ import annotations

import re
from dataclasses import dataclass

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
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/tools/test_file_ops.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/tools/file_ops.py tests/tools/test_file_ops.py
git commit -m "feat: dry_run_copy tool parsing robocopy /L summaries"
```

## Task 10: Tool — `quarantine_move` 🟢 COMPLETED

**Files:**
- Modify: `src/remote_fleet_agent/tools/file_ops.py`
- Test: `tests/tools/test_file_ops.py`

**Language Rules (Python):** see Task 1.

Per DESIGN.md, "delete" never deletes — it moves files into a timestamped
quarantine folder on the *same volume*. Two non-obvious edge cases: staying
on the same drive letter (so this is a cheap move, not a slow cross-volume
copy+delete), and not clobbering an existing quarantine folder from an
earlier run today.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| paths `["D:\\Share\\old.docx"]` | moved under `D:\_quarantine\<UTC timestamp>\old.docx`, same drive letter as the source |
| a second `quarantine_move` call in the same second | the second call's folder name gets a `-2` suffix rather than colliding |
| a path on a different drive than the first path in the same call | raises `ValueError` containing `"same volume"` — the design requires one volume per quarantine operation |

**Step 1: Write the failing tests (RED)**
```python
@pytest.mark.asyncio
async def test_quarantine_move_builds_same_drive_path():
    fake = CommandResult(stdout="", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell", AsyncMock(return_value=fake)) as mock_run, \
         patch("remote_fleet_agent.tools.file_ops._utc_timestamp", return_value="20261027T140000Z"):
        result = await quarantine_move("CONN", paths=["D:\\Share\\old.docx"])
    assert result.quarantine_dir == "D:\\_quarantine\\20261027T140000Z"
    script = mock_run.call_args.args[1]
    assert "D:\\_quarantine\\20261027T140000Z" in script

@pytest.mark.asyncio
async def test_quarantine_move_rejects_mixed_drives():
    with pytest.raises(ValueError, match="same volume"):
        await quarantine_move("CONN", paths=["D:\\Share\\a.docx", "E:\\Other\\b.docx"])

@pytest.mark.asyncio
async def test_quarantine_move_avoids_collision(monkeypatch):
    fake = CommandResult(stdout="", stderr="", exit_code=0)
    # first call: folder doesn't exist -> Test-Path returns "False"
    exists_check = CommandResult(stdout="True", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.file_ops.run_powershell",
               AsyncMock(side_effect=[exists_check, fake])), \
         patch("remote_fleet_agent.tools.file_ops._utc_timestamp", return_value="20261027T140000Z"):
        result = await quarantine_move("CONN", paths=["D:\\Share\\old.docx"])
    assert result.quarantine_dir == "D:\\_quarantine\\20261027T140000Z-2"
```
Add the import: `from remote_fleet_agent.tools.file_ops import quarantine_move`.

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/tools/test_file_ops.py -v`
Expected: FAIL (`quarantine_move` not defined).

**Step 3: Minimal implementation (GREEN)**
```python
# add to file_ops.py
from datetime import datetime, timezone


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
    suffix = 1
    while True:
        check = await run_powershell(conn, f"Test-Path {ps_quote(candidate)}")
        if check.stdout.strip() != "True":
            break
        suffix += 1
        candidate = f"{base_dir}-{suffix}"

    move_lines = [f"New-Item -ItemType Directory -Force -Path {ps_quote(candidate)} | Out-Null"]
    for path in paths:
        move_lines.append(f"Move-Item -Path {ps_quote(path)} -Destination {ps_quote(candidate)} -Force")
    result = await run_powershell(conn, "; ".join(move_lines))
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or "quarantine move failed")

    return QuarantineResult(quarantine_dir=candidate, moved_count=len(paths))
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/tools/test_file_ops.py -v`
Expected: 6 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/tools/file_ops.py tests/tools/test_file_ops.py
git commit -m "feat: quarantine_move tool with same-volume and collision checks"
```

## Task 11: Tool — `gdpr_scan` 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/tools/gdpr_scan.py`
- Test: `tests/tools/test_gdpr_scan.py`

**Language Rules (Python):** see Task 1.

See `ASSUMPTIONS.md` A-001 for the three starter patterns. The hard rule
from DESIGN.md: matching happens on the target; **this tool must never
return matched text, only path/count/type.**

**Behavioral contract:**
| Input (mocked remote JSON) | Expected output |
|-------|-----------------|
| remote reports 1 NI-number match in `report.xlsx`, 0 elsewhere | `[GdprFinding(path="...report.xlsx", match_type="ni_number", count=1)]` |
| remote reports zero matches anywhere | `[]` |
| the returned object/dict (any finding) | asserting `"match_text"` / raw content is never a key anywhere in the result |

**Step 1: Write the failing tests (RED)**
```python
# tests/tools/test_gdpr_scan.py
import pytest
from unittest.mock import AsyncMock, patch
from remote_fleet_agent.ssh_client import CommandResult
from remote_fleet_agent.tools.gdpr_scan import gdpr_scan

@pytest.mark.asyncio
async def test_gdpr_scan_returns_findings_without_content():
    remote_json = (
        '[{"path": "D:\\\\Share\\\\report.xlsx", "match_type": "ni_number", "count": 1}]'
    )
    fake = CommandResult(stdout=remote_json, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    assert len(findings) == 1
    assert findings[0].path.endswith("report.xlsx")
    assert findings[0].match_type == "ni_number"
    assert findings[0].count == 1

@pytest.mark.asyncio
async def test_gdpr_scan_no_matches_returns_empty_list():
    fake = CommandResult(stdout="[]", stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    assert findings == []

@pytest.mark.asyncio
async def test_gdpr_scan_finding_never_carries_raw_text():
    remote_json = '[{"path": "D:\\\\x.docx", "match_type": "email", "count": 3}]'
    fake = CommandResult(stdout=remote_json, stderr="", exit_code=0)
    with patch("remote_fleet_agent.tools.gdpr_scan.run_powershell", AsyncMock(return_value=fake)):
        findings = await gdpr_scan("CONN", root_path="D:\\Share")
    for f in findings:
        field_names = f.__dataclass_fields__.keys()
        assert "match_text" not in field_names
        assert "content" not in field_names
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/tools/test_gdpr_scan.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/tools/gdpr_scan.py
from __future__ import annotations

import json
from dataclasses import dataclass

from remote_fleet_agent.ssh_client import ps_quote, run_powershell

# (WHY: these three regexes run ON THE TARGET inside the PowerShell script
# below. Only the match TYPE, COUNT, and file PATH are ever returned —
# never the matched substring — so personal data never leaves the
# target machine. See DESIGN.md "GDPR-sensitive file discovery".)
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
Get-ChildItem -Path {ps_quote(root_path)} -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {{
    $text = Get-Content -Raw -ErrorAction SilentlyContinue $_.FullName
    if ($text) {{
        foreach ($name in @('ni_number','card_number','email')) {{
            $pattern = Get-Variable -Name $name -ValueOnly
            $matches = ([regex]::Matches($text, $pattern)).Count
            if ($matches -gt 0) {{
                $findings += [PSCustomObject]@{{path=$_.FullName; match_type=$name; count=$matches}}
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
    return [GdprFinding(path=f["path"], match_type=f["match_type"], count=f["count"]) for f in parsed]
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/tools/test_gdpr_scan.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/tools/gdpr_scan.py tests/tools/test_gdpr_scan.py
git commit -m "feat: gdpr_scan tool, matching on-target, never returning content"
```

## Task 12: Fan-out across hosts with per-host failure isolation 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/fanout.py`
- Test: `tests/test_fanout.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| 3 hosts, all succeed | `{host: result}` for all 3 |
| 3 hosts, 1 raises | the other 2 still return their results; the failing host's value is the caught exception, not a crash of the whole call |
| empty host list | `{}`, no error |

**Step 1: Write the failing tests (RED)**
```python
# tests/test_fanout.py
import pytest
from remote_fleet_agent.fanout import run_on_hosts

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
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_fanout.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/fanout.py
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable


async def run_on_hosts(
    hosts: list[str], fn: Callable[[str], Awaitable[object]]
) -> dict[str, object]:
    """Run fn(host) concurrently across every host.

    (WHY: return_exceptions=True so one unreachable machine in a 30-host
    sweep doesn't take down the other 29 results — the caller gets the
    exception back as a value and decides what to report for that host.)
    """
    if not hosts:
        return {}
    results = await asyncio.gather(*(fn(h) for h in hosts), return_exceptions=True)
    return dict(zip(hosts, results))
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_fanout.py -v`
Expected: 3 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/fanout.py tests/test_fanout.py
git commit -m "feat: fan-out helper with per-host failure isolation"
```

## Task 13: Fan-out scope confirmation for heavy/destructive operations 🟢 COMPLETED

**Files:**
- Modify: `src/remote_fleet_agent/fanout.py`
- Test: `tests/test_fanout.py`

**Language Rules (Python):** see Task 1.

Per DESIGN.md: before anything heavy or destructive runs across multiple
machines, the engineer must see and confirm the exact host list — not just
"yes, go".

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `requested=["a","b","c"]`, `confirmed=["a","b","c"]` | runs normally, same as Task 12 |
| `requested=["a","b","c"]`, `confirmed=["a","b"]` (subset) | raises `ScopeMismatchError` containing `"did not match"` — nothing is executed |
| `requested=["a","b"]`, `confirmed=["a","b","c"]` (confirmed a superset) | raises `ScopeMismatchError` — confirmation must match exactly, not just be a superset |

**Step 1: Write the failing tests (RED)**
```python
from remote_fleet_agent.fanout import run_on_hosts_confirmed, ScopeMismatchError

@pytest.mark.asyncio
async def test_confirmed_scope_matching_requested_runs():
    async def fn(host):
        return host
    results = await run_on_hosts_confirmed(
        requested=["a", "b"], confirmed=["a", "b"], fn=fn
    )
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
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_fanout.py -v`
Expected: FAIL (`run_on_hosts_confirmed` not defined).

**Step 3: Minimal implementation (GREEN)**
```python
# add to fanout.py
class ScopeMismatchError(Exception):
    pass


async def run_on_hosts_confirmed(
    *, requested: list[str], confirmed: list[str], fn: Callable[[str], Awaitable[object]]
) -> dict[str, object]:
    if set(requested) != set(confirmed):
        raise ScopeMismatchError(
            f"confirmed scope {sorted(confirmed)} did not match requested scope {sorted(requested)}"
        )
    return await run_on_hosts(requested, fn)
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_fanout.py -v`
Expected: 6 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/fanout.py tests/test_fanout.py
git commit -m "feat: require exact scope confirmation before destructive fan-out"
```

## Task 14: MCP server — register and expose the tool set 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/server.py`
- Test: `tests/test_server.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `build_server().list_tools()` (in-process) | tool names exactly `{"run_check", "fit_check", "dry_run_copy", "quarantine_move", "gdpr_scan"}` — no raw shell/delete tool present |

**Step 1: Write the failing test (RED)**
```python
# tests/test_server.py
import pytest
from remote_fleet_agent.server import build_server

@pytest.mark.asyncio
async def test_exposed_tools_match_expected_safe_set():
    server = build_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"run_check", "fit_check", "dry_run_copy", "quarantine_move", "gdpr_scan"}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_server.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/server.py
from __future__ import annotations

from mcp.server import Server
from mcp.server.stdio import stdio_server

from remote_fleet_agent.connection_pool import ConnectionPool
from remote_fleet_agent.tools.run_check import run_check
from remote_fleet_agent.tools.fit_check import fit_check
from remote_fleet_agent.tools.file_ops import dry_run_copy, quarantine_move
from remote_fleet_agent.tools.gdpr_scan import gdpr_scan


def build_server() -> Server:
    server = Server("remote-fleet-agent")
    # Tool registration wiring (exact decorator/schema shape depends on the
    # installed `mcp` SDK version — follow its tool-registration docs here).
    # Each handler below resolves `host` through the ConnectionPool before
    # calling into the corresponding tools.* function; no handler exposes a
    # raw command-execution or delete primitive to the caller.
    for name, _fn in [
        ("run_check", run_check),
        ("fit_check", fit_check),
        ("dry_run_copy", dry_run_copy),
        ("quarantine_move", quarantine_move),
        ("gdpr_scan", gdpr_scan),
    ]:
        server.register_tool(name)  # placeholder call — replace with the SDK's real registration API
    return server
```
Note for the executor: the `mcp` SDK's exact registration API (decorator vs.
explicit call, required schema fields) may differ from the placeholder
above — adapt this step to whatever `pip show mcp` installs, keeping the
test's behavioral contract (exactly these five tool names, nothing else)
as the thing that must pass.

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_server.py -v`
Expected: 1 passed, no warnings.

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/server.py tests/test_server.py
git commit -m "feat: MCP server exposing the five safe tools"
```

## Task 15: CLI — stage a client's install kit 🟢 COMPLETED

**Files:**
- Create: `src/remote_fleet_agent/cli.py`
- Test: `tests/test_cli.py`

**Language Rules (Python):** see Task 1.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| `init_client("acme", stage_dir=tmp_path)` | generates an ed25519 keypair under `~/.remote-fleet-agent/keys/acme/` (private key mode `0600`); copies `install.ps1`, `uninstall.ps1`, and the **public** key only into `stage_dir` |
| called twice for the same client | second call does not overwrite the existing keypair — raises `FileExistsError` containing `"already exists"` unless `force=True` |

**Step 1: Write the failing tests (RED)**
```python
# tests/test_cli.py
import stat
import pytest
from remote_fleet_agent.cli import init_client

def test_init_client_generates_key_and_stages_scripts(tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", keys_dir)
    stage_dir = tmp_path / "usb"
    stage_dir.mkdir()

    init_client("acme", stage_dir=stage_dir)

    priv = keys_dir / "acme" / "id_ed25519"
    pub = keys_dir / "acme" / "id_ed25519.pub"
    assert priv.exists() and pub.exists()
    assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    assert (stage_dir / "install.ps1").exists()
    assert (stage_dir / "uninstall.ps1").exists()
    assert (stage_dir / "id_ed25519.pub").exists()
    assert not (stage_dir / "id_ed25519").exists()  # private key must never be staged to the USB

def test_init_client_refuses_to_overwrite(tmp_path, monkeypatch):
    keys_dir = tmp_path / "keys"
    monkeypatch.setattr("remote_fleet_agent.cli.KEYS_DIR", keys_dir)
    stage_dir = tmp_path / "usb"
    stage_dir.mkdir()
    init_client("acme", stage_dir=stage_dir)
    with pytest.raises(FileExistsError, match="already exists"):
        init_client("acme", stage_dir=stage_dir)
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pytest tests/test_cli.py -v`
Expected: FAIL (module doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```python
# src/remote_fleet_agent/cli.py
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

KEYS_DIR = Path.home() / ".remote-fleet-agent" / "keys"
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def init_client(client: str, *, stage_dir: Path, force: bool = False) -> None:
    client_key_dir = KEYS_DIR / client
    priv_path = client_key_dir / "id_ed25519"
    if priv_path.exists() and not force:
        raise FileExistsError(f"keypair for client '{client}' already exists at {priv_path}")

    client_key_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv_path), "-C", f"remote-fleet-agent:{client}"],
        check=True,
        capture_output=True,
    )
    priv_path.chmod(0o600)

    shutil.copy(SCRIPTS_DIR / "install.ps1", stage_dir / "install.ps1")
    shutil.copy(SCRIPTS_DIR / "uninstall.ps1", stage_dir / "uninstall.ps1")
    shutil.copy(client_key_dir / "id_ed25519.pub", stage_dir / "id_ed25519.pub")
    # (WHY: only the public key is ever copied to the USB stick — the private
    # key never leaves ~/.remote-fleet-agent on the engineer's own laptop.)
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pytest tests/test_cli.py -v`
Expected: 2 passed, no warnings. (Requires `ssh-keygen` on PATH, standard on macOS/Linux.)

**Step 5: Commit**
```bash
git add src/remote_fleet_agent/cli.py tests/test_cli.py
git commit -m "feat: CLI to generate a per-client keypair and stage the USB install kit"
```

## Task 16: `install.ps1` — idempotent OpenSSH enablement 🟢 COMPLETED

**Files:**
- Create: `scripts/install.ps1`
- Test: `scripts/install.Tests.ps1`

**PowerShell test rules:** run with `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`.
These tests run cross-platform via `pwsh` and verify *logic* (idempotency,
branching, state recording) by mocking every Windows-only cmdlet — they do
**not** prove the real Windows APIs behave as expected. Task 22 covers that
separately, on a real Windows machine.

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| OpenSSH not yet installed | calls `Enable-WindowsOptionalFeature`; writes `ssh_was_already_enabled: false` to the state file |
| OpenSSH already installed and running | does **not** call `Enable-WindowsOptionalFeature`; writes `ssh_was_already_enabled: true` |
| called a second time (re-run) | does not error, does not re-enable what's already enabled (idempotent) |

**Step 1: Write the failing tests (RED)**
```powershell
# scripts/install.Tests.ps1
BeforeAll {
    . "$PSScriptRoot/install.ps1" -DotSource
}

Describe "Test-OpenSSHEnabled / Enable-OpenSSHServer" {
    It "enables OpenSSH and records false when not previously enabled" {
        Mock Get-Service { $null } -ParameterFilter { $Name -eq "sshd" }
        Mock Enable-WindowsOptionalFeature { } -Verifiable
        $statePath = Join-Path $TestDrive "state.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -InvokeVerifiable
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $false
    }

    It "does not re-enable when already running, records true" {
        Mock Get-Service { [PSCustomObject]@{ Status = "Running" } } -ParameterFilter { $Name -eq "sshd" }
        Mock Enable-WindowsOptionalFeature { }
        $statePath = Join-Path $TestDrive "state2.json"

        Enable-OpenSSHServer -StatePath $statePath

        Should -Invoke Enable-WindowsOptionalFeature -Times 0
        $state = Get-Content $statePath | ConvertFrom-Json
        $state.ssh_was_already_enabled | Should -Be $true
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: FAIL (`install.ps1` doesn't exist / functions not defined).

**Step 3: Minimal implementation (GREEN)**
```powershell
# scripts/install.ps1 (partial — this task's functions only; Tasks 17-20 extend this file)
param([switch]$DotSource)

function Test-OpenSSHEnabled {
    $svc = Get-Service -Name "sshd" -ErrorAction SilentlyContinue
    return ($null -ne $svc -and $svc.Status -eq "Running")
}

function Enable-OpenSSHServer {
    param([Parameter(Mandatory)][string]$StatePath)

    $alreadyEnabled = Test-OpenSSHEnabled
    if (-not $alreadyEnabled) {
        # (WHY: Add-WindowsCapability is the documented way to install the
        # OpenSSH.Server optional feature on Windows 10/11 and Server; using
        # it here rather than a raw MSI keeps this in-box and offline-friendly
        # as long as the feature isn't blocked by WSUS — see ASSUMPTIONS.md.)
        Enable-WindowsOptionalFeature -Online -FeatureName "OpenSSH.Server" -NoRestart
        Start-Service sshd
        Set-Service -Name sshd -StartupType Automatic
    }

    $state = @{ ssh_was_already_enabled = $alreadyEnabled; installed_at = (Get-Date).ToUniversalTime().ToString("o") }
    $state | ConvertTo-Json | Set-Content -Path $StatePath
}

if ($DotSource) { return }
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: 2 passed.

**Step 5: Commit**
```bash
git add scripts/install.ps1 scripts/install.Tests.ps1
git commit -m "feat: idempotent OpenSSH enablement with prior-state tracking"
```

## Task 17: `install.ps1` — idempotent authorized_keys append 🟢 COMPLETED

**Files:**
- Modify: `scripts/install.ps1`
- Modify: `scripts/install.Tests.ps1`

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| key not yet present in `authorized_keys` | key line appended |
| same key, called again | file content unchanged — no duplicate line |

**Step 1: Write the failing tests (RED)**
```powershell
Describe "Add-EngineerKey" {
    It "appends a new key" {
        $authKeys = Join-Path $TestDrive "authorized_keys"
        Set-Content $authKeys ""
        Add-EngineerKey -PublicKeyPath (Join-Path $TestDrive "key.pub") -AuthorizedKeysPath $authKeys `
            -PublicKeyContent "ssh-ed25519 AAAA... engineer@laptop"
        (Get-Content $authKeys) | Should -Contain "ssh-ed25519 AAAA... engineer@laptop"
    }

    It "does not duplicate an already-present key" {
        $authKeys = Join-Path $TestDrive "authorized_keys2"
        Set-Content $authKeys "ssh-ed25519 AAAA... engineer@laptop"
        Add-EngineerKey -PublicKeyPath (Join-Path $TestDrive "key.pub") -AuthorizedKeysPath $authKeys `
            -PublicKeyContent "ssh-ed25519 AAAA... engineer@laptop"
        (Get-Content $authKeys | Measure-Object -Line).Lines | Should -Be 1
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: FAIL (`Add-EngineerKey` not defined).

**Step 3: Minimal implementation (GREEN)**
```powershell
# add to install.ps1
function Add-EngineerKey {
    param(
        [Parameter(Mandatory)][string]$PublicKeyPath,
        [Parameter(Mandatory)][string]$AuthorizedKeysPath,
        [string]$PublicKeyContent
    )
    if (-not $PublicKeyContent) {
        $PublicKeyContent = (Get-Content $PublicKeyPath -Raw).Trim()
    }
    $existing = @()
    if (Test-Path $AuthorizedKeysPath) {
        $existing = Get-Content $AuthorizedKeysPath
    }
    if ($existing -notcontains $PublicKeyContent) {
        Add-Content -Path $AuthorizedKeysPath -Value $PublicKeyContent
    }
}
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: 4 passed.

**Step 5: Commit**
```bash
git add scripts/install.ps1 scripts/install.Tests.ps1
git commit -m "feat: idempotent authorized_keys append"
```

## Task 18: `install.ps1` — scoped, idempotent firewall rule 🟢 COMPLETED

**Files:**
- Modify: `scripts/install.ps1`
- Modify: `scripts/install.Tests.ps1`

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| no existing rule named `RemoteFleetAgent-SSH` | creates one scoped to the given IP, port 22 |
| rule already exists (e.g. re-run with a new laptop IP) | old rule removed first, then recreated with the new IP — never two stacked rules |

**Step 1: Write the failing tests (RED)**
```powershell
Describe "Add-ScopedFirewallRule" {
    It "creates a new rule when none exists" {
        Mock Get-NetFirewallRule { $null }
        Mock New-NetFirewallRule { } -Verifiable
        Mock Remove-NetFirewallRule { }

        Add-ScopedFirewallRule -AllowedIP "192.168.1.50"

        Should -InvokeVerifiable
        Should -Invoke Remove-NetFirewallRule -Times 0
    }

    It "replaces an existing rule rather than stacking" {
        Mock Get-NetFirewallRule { [PSCustomObject]@{ Name = "RemoteFleetAgent-SSH" } }
        Mock Remove-NetFirewallRule { } -Verifiable
        Mock New-NetFirewallRule { } -Verifiable

        Add-ScopedFirewallRule -AllowedIP "192.168.1.51"

        Should -InvokeVerifiable
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: FAIL (`Add-ScopedFirewallRule` not defined).

**Step 3: Minimal implementation (GREEN)**
```powershell
# add to install.ps1
function Add-ScopedFirewallRule {
    param([Parameter(Mandatory)][string]$AllowedIP)

    $ruleName = "RemoteFleetAgent-SSH"
    if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -Name $ruleName
    }
    New-NetFirewallRule -Name $ruleName -DisplayName "Remote Fleet Agent (SSH, scoped)" `
        -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress $AllowedIP -Action Allow
}
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: 6 passed.

**Step 5: Commit**
```bash
git add scripts/install.ps1 scripts/install.Tests.ps1
git commit -m "feat: scoped firewall rule, replaced not stacked on re-run"
```

## Task 19: `install.ps1` — 3-day self-destruct scheduled task 🟢 COMPLETED

**Files:**
- Modify: `scripts/install.ps1`
- Modify: `scripts/install.Tests.ps1`

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| no existing self-destruct task | creates one scheduled to run `uninstall.ps1` 3 days from now |
| re-run on day 2 of an existing 3-day window | old task removed, new one created 3 days from *this* run — the clock resets, it doesn't stack |
| `-DaysUntilExpiry 1` passed explicitly | task scheduled 1 day out, not the default 3 |

**Step 1: Write the failing tests (RED)**
```powershell
Describe "Register-SelfDestructTask" {
    It "creates a task 3 days out by default" {
        Mock Get-ScheduledTask { $null }
        Mock Unregister-ScheduledTask { }
        Mock Register-ScheduledTask { } -Verifiable -ParameterFilter {
            $Trigger.StartBoundary -ne $null
        }

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1"

        Should -InvokeVerifiable
        Should -Invoke Unregister-ScheduledTask -Times 0
    }

    It "resets the clock on re-run rather than stacking tasks" {
        Mock Get-ScheduledTask { [PSCustomObject]@{ TaskName = "RemoteFleetAgent-SelfDestruct" } }
        Mock Unregister-ScheduledTask { } -Verifiable
        Mock Register-ScheduledTask { } -Verifiable

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1"

        Should -InvokeVerifiable
    }

    It "honours a custom expiry window" {
        Mock Get-ScheduledTask { $null }
        Mock Unregister-ScheduledTask { }
        Mock Register-ScheduledTask { }
        Mock New-ScheduledTaskTrigger { [PSCustomObject]@{} } -Verifiable -ParameterFilter {
            $Once -and $At -lt (Get-Date).AddDays(1).AddMinutes(1)
        }

        Register-SelfDestructTask -UninstallScriptPath "C:\agent\uninstall.ps1" -DaysUntilExpiry 1

        Should -InvokeVerifiable
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: FAIL (`Register-SelfDestructTask` not defined).

**Step 3: Minimal implementation (GREEN)**
```powershell
# add to install.ps1
function Register-SelfDestructTask {
    param(
        [Parameter(Mandatory)][string]$UninstallScriptPath,
        [int]$DaysUntilExpiry = 3
    )

    $taskName = "RemoteFleetAgent-SelfDestruct"
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        # (WHY: re-running install.ps1 on a return visit must reset the
        # 3-day clock, not add a second competing task — see DESIGN.md's
        # "re-run resets the timer" rule.)
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddDays($DaysUntilExpiry)
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$UninstallScriptPath`""
    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action `
        -User "SYSTEM" -RunLevel Highest
}
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: 9 passed.

**Step 5: Commit**
```bash
git add scripts/install.ps1 scripts/install.Tests.ps1
git commit -m "feat: 3-day self-destruct scheduled task, reset on re-install"
```

## Task 20: `install.ps1` — main entry point and parameter validation 🟢 COMPLETED

**Files:**
- Modify: `scripts/install.ps1`
- Modify: `scripts/install.Tests.ps1`

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| valid `-AllowedIP` (e.g. `192.168.1.50`) and existing `-PublicKeyPath` | calls `Enable-OpenSSHServer`, `Add-EngineerKey`, `Add-ScopedFirewallRule`, `Register-SelfDestructTask`, in that order |
| malformed `-AllowedIP` (e.g. `not-an-ip`) | throws before touching the system — no firewall/SSH calls made |
| `-PublicKeyPath` pointing at a file that doesn't exist | throws a clear error, no partial changes made |

**Step 1: Write the failing tests (RED)**
```powershell
Describe "Invoke-Install (main)" {
    It "runs all four steps in order for valid input" {
        Mock Enable-OpenSSHServer { } -Verifiable
        Mock Add-EngineerKey { } -Verifiable
        Mock Add-ScopedFirewallRule { } -Verifiable
        Mock Register-SelfDestructTask { } -Verifiable
        $keyFile = Join-Path $TestDrive "key.pub"
        Set-Content $keyFile "ssh-ed25519 AAAA..."

        Invoke-Install -AllowedIP "192.168.1.50" -PublicKeyPath $keyFile

        Should -InvokeVerifiable
    }

    It "rejects a malformed IP before making any changes" {
        Mock Enable-OpenSSHServer { }
        $keyFile = Join-Path $TestDrive "key2.pub"
        Set-Content $keyFile "ssh-ed25519 AAAA..."

        { Invoke-Install -AllowedIP "not-an-ip" -PublicKeyPath $keyFile } | Should -Throw
        Should -Invoke Enable-OpenSSHServer -Times 0
    }

    It "rejects a missing public key file" {
        { Invoke-Install -AllowedIP "192.168.1.50" -PublicKeyPath (Join-Path $TestDrive "missing.pub") } |
            Should -Throw
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: FAIL (`Invoke-Install` not defined).

**Step 3: Minimal implementation (GREEN)**
```powershell
# add to install.ps1, replacing the trailing `if ($DotSource) { return }` line
function Invoke-Install {
    param(
        [Parameter(Mandatory)][string]$AllowedIP,
        [Parameter(Mandatory)][string]$PublicKeyPath,
        [int]$DaysUntilExpiry = 3
    )

    if ($AllowedIP -notmatch '^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$') {
        throw "AllowedIP '$AllowedIP' is not a valid IPv4 address"
    }
    if (-not (Test-Path $PublicKeyPath)) {
        throw "PublicKeyPath '$PublicKeyPath' does not exist"
    }

    $stateDir = "C:\ProgramData\remote-fleet-agent"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

    Enable-OpenSSHServer -StatePath (Join-Path $stateDir "state.json")
    Add-EngineerKey -PublicKeyPath $PublicKeyPath `
        -AuthorizedKeysPath "$env:ProgramData\ssh\administrators_authorized_keys"
    Add-ScopedFirewallRule -AllowedIP $AllowedIP
    Register-SelfDestructTask -UninstallScriptPath (Join-Path $PSScriptRoot "uninstall.ps1") `
        -DaysUntilExpiry $DaysUntilExpiry

    Write-Output "Install complete. Self-destructs in $DaysUntilExpiry day(s) unless re-run."
}

if ($DotSource) { return }
Invoke-Install @args
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/install.Tests.ps1"`
Expected: 12 passed.

**Step 5: Commit**
```bash
git add scripts/install.ps1 scripts/install.Tests.ps1
git commit -m "feat: install.ps1 main entry point with fail-fast parameter validation"
```

## Task 21: `uninstall.ps1` — full reversal and receipt 🟢 COMPLETED

**Files:**
- Create: `scripts/uninstall.ps1`
- Create: `scripts/uninstall.Tests.ps1`

**Behavioral contract:**
| Input | Expected output |
|-------|-----------------|
| state file says `ssh_was_already_enabled: true` | key removed, firewall rule removed, scheduled task removed — OpenSSH service is **left running** |
| state file says `ssh_was_already_enabled: false` | same as above, plus OpenSSH service is disabled |
| runs to completion | prints a JSON receipt listing exactly what was removed and what was left alone, with a timestamp |

**Step 1: Write the failing tests (RED)**
```powershell
# scripts/uninstall.Tests.ps1
BeforeAll {
    . "$PSScriptRoot/uninstall.ps1" -DotSource
}

Describe "Invoke-Uninstall" {
    It "leaves OpenSSH running if it was already enabled before install" {
        $statePath = Join-Path $TestDrive "state.json"
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z" } |
            ConvertTo-Json | Set-Content $statePath

        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }
        Mock Disable-WindowsOptionalFeature { } -Verifiable

        $receipt = Invoke-Uninstall -StatePath $statePath

        Should -Invoke Disable-WindowsOptionalFeature -Times 0
        $receipt.ssh_left_running | Should -Be $true
    }

    It "disables OpenSSH if this tool enabled it" {
        $statePath = Join-Path $TestDrive "state2.json"
        @{ ssh_was_already_enabled = $false; installed_at = "2026-10-24T00:00:00Z" } |
            ConvertTo-Json | Set-Content $statePath

        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }
        Mock Disable-WindowsOptionalFeature { } -Verifiable

        $receipt = Invoke-Uninstall -StatePath $statePath

        Should -InvokeVerifiable
        $receipt.ssh_left_running | Should -Be $false
    }

    It "produces a receipt with a timestamp and removal list" {
        $statePath = Join-Path $TestDrive "state3.json"
        @{ ssh_was_already_enabled = $true; installed_at = "2026-10-24T00:00:00Z" } |
            ConvertTo-Json | Set-Content $statePath
        Mock Remove-EngineerKey { }
        Mock Remove-ScopedFirewallRule { }
        Mock Unregister-ScheduledTask { }

        $receipt = Invoke-Uninstall -StatePath $statePath

        $receipt.removed_at | Should -Not -BeNullOrEmpty
        $receipt.removed | Should -Contain "ssh_key"
        $receipt.removed | Should -Contain "firewall_rule"
        $receipt.removed | Should -Contain "self_destruct_task"
    }
}
```

**Step 2: Run it, confirm it fails (verify RED)**
Run: `pwsh -Command "Invoke-Pester scripts/uninstall.Tests.ps1"`
Expected: FAIL (`uninstall.ps1` doesn't exist).

**Step 3: Minimal implementation (GREEN)**
```powershell
# scripts/uninstall.ps1
param([switch]$DotSource)

function Remove-EngineerKey {
    param([string]$AuthorizedKeysPath = "$env:ProgramData\ssh\administrators_authorized_keys")
    # Removal targets the specific key this tool added; in v1 that's the
    # only key in this file, so clearing it is safe and simple.
    if (Test-Path $AuthorizedKeysPath) { Clear-Content $AuthorizedKeysPath }
}

function Remove-ScopedFirewallRule {
    Unregister-Rule -Name "RemoteFleetAgent-SSH"
}
function Unregister-Rule {
    param([string]$Name)
    if (Get-NetFirewallRule -Name $Name -ErrorAction SilentlyContinue) {
        Remove-NetFirewallRule -Name $Name
    }
}

function Invoke-Uninstall {
    param([Parameter(Mandatory)][string]$StatePath)

    $state = Get-Content $StatePath | ConvertFrom-Json
    $removed = @()

    Remove-EngineerKey
    $removed += "ssh_key"

    Remove-ScopedFirewallRule
    $removed += "firewall_rule"

    Unregister-ScheduledTask -TaskName "RemoteFleetAgent-SelfDestruct" -Confirm:$false -ErrorAction SilentlyContinue
    $removed += "self_destruct_task"

    $sshLeftRunning = $state.ssh_was_already_enabled
    if (-not $sshLeftRunning) {
        Disable-WindowsOptionalFeature -Online -FeatureName "OpenSSH.Server" -NoRestart
        $removed += "openssh_server"
    }

    Remove-Item $StatePath -ErrorAction SilentlyContinue

    return [PSCustomObject]@{
        removed_at       = (Get-Date).ToUniversalTime().ToString("o")
        removed          = $removed
        ssh_left_running = $sshLeftRunning
    }
}

if ($DotSource) { return }
$receipt = Invoke-Uninstall -StatePath "C:\ProgramData\remote-fleet-agent\state.json"
$receipt | ConvertTo-Json
```

**Step 4: Run it, confirm it passes (verify GREEN)**
Run: `pwsh -Command "Invoke-Pester scripts/uninstall.Tests.ps1"`
Expected: 3 passed.

**Step 5: Commit**
```bash
git add scripts/uninstall.ps1 scripts/uninstall.Tests.ps1
git commit -m "feat: uninstall.ps1 with state-aware reversal and JSON receipt"
```

## Task 22: Manual verification on a real Windows machine 🔴 NOT_STARTED — NOT UNIT-TESTABLE

**Files:** none created; this is a verification task, not a code task.

Everything in Tasks 16-21 was tested with every Windows-only cmdlet mocked,
on macOS via `pwsh`. That proves the scripts' *logic* is right; it proves
nothing about whether `Enable-WindowsOptionalFeature`, `New-NetFirewallRule`,
or `Register-ScheduledTask` actually do what we assume on a real target.
This must be checked by hand before the tool is used on a real client.

**Checklist (run on a Windows 10/11 VM or spare machine, from an elevated
PowerShell session):**
1. Run `install.ps1 -AllowedIP <your-laptop-IP> -PublicKeyPath .\id_ed25519.pub`.
   Confirm: `Get-Service sshd` shows `Running`; `ssh -i <private key> <admin-user>@<vm>` connects from your laptop; a connection attempt from a *different* IP is refused (test from a second machine or by spoofing source IP in a packet-level tool if available).
2. Confirm the self-destruct task exists: `Get-ScheduledTask -TaskName RemoteFleetAgent-SelfDestruct` shows a trigger ~3 days out.
3. Re-run `install.ps1` with the same IP. Confirm `Get-ScheduledTask` still shows exactly one task, now ~3 days from the *second* run, not two tasks.
4. Run `uninstall.ps1`. Confirm: the firewall rule is gone (`Get-NetFirewallRule -Name RemoteFleetAgent-SSH` returns nothing), the key no longer connects, the scheduled task is gone, and — if OpenSSH was *not* already running before step 1 — the `sshd` service is now disabled.
5. Repeat steps 1-4 on a machine where OpenSSH was *already* enabled beforehand (e.g. a dev box). Confirm `uninstall.ps1` leaves `sshd` running afterward.
6. Record the result of each check in a short note in this repo (e.g. `docs/verification/2026-10-27-manual-windows-check.md`) so there's a dated record this was actually checked, not just planned.

This task is 🔴 NOT_STARTED until that note exists.

## Task 23: README for the engineer 🟢 COMPLETED

**Files:**
- Create: `README.md`

Not a TDD task — a short usage doc so a zero-context engineer (including
future-you) can actually run this.

**Contents to include:**
- What this is and isn't (link to `docs/plans/2026-10-27-DESIGN-remote-fleet-agent.md` for the full reasoning).
- Setup: `pip install -e ".[dev]"`, how to run `remote-fleet-agent init-client <name> --stage-dir /Volumes/<usb>`.
- Per-engagement workflow: stage the stick → run `install.ps1` on each target (elevated PowerShell, from an admin account) → add the Claude Code MCP server pointing at this process → run the audit from Claude → run `uninstall.ps1` (or let the 3-day timer handle it) → delete the client's keypair from `~/.remote-fleet-agent/keys/<client>/` once the engagement closes.
- The hard rules, verbatim, so they aren't forgotten: uninstall is always explicit, never auto-triggered by a task outcome; GDPR scan results never include matched text; one USB stick and one keypair per client.
- Pointer to `ASSUMPTIONS.md` for every open decision made without a client-specific answer yet.

**Step 1: Write the file** with the sections above.

**Step 2: Commit**
```bash
git add README.md
git commit -m "docs: engineer-facing README"
```
