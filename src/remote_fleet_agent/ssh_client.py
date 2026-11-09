from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass

import asyncssh

logger = logging.getLogger("remote_fleet_agent.ssh")


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


async def connect(
    host: str,
    key_path: str,
    *,
    port: int = 22,
    timeout: float = 10.0,
    retries: int = 1,
    known_hosts: str | None = None,
):
    """Open an SSH connection to host, with one retry and structured logging.

    (WHY: known_hosts=None — targets are freshly installed each engagement
    with no prior known_hosts entry; host identity is established by the
    engineer's physical presence at install time, not by TOFU key pinning.
    See DESIGN.md's trust/pairing section.)
    """
    target = host
    options = {}
    if port != 22:
        options["port"] = port
    if "@" in host:
        username, target = host.rsplit("@", 1)
        if not username or not target:
            raise ValueError("host must be username@hostname")
        options["username"] = username
    known_hosts = (
        known_hosts or os.environ.get("REMOTE_FLEET_AGENT_KNOWN_HOSTS") or None
    )
    attempt = 0
    last_error: Exception | None = None
    while attempt <= retries:
        attempt += 1
        start = time.monotonic()
        try:
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    target, client_keys=[key_path], known_hosts=known_hosts, **options
                ),
                timeout=timeout,
            )
            elapsed_ms = (
                time.monotonic() - start
            ) * 1000  # (MEASURED, real connect latency)
            logger.info("ssh.connect.ok host=%s elapsed_ms=%.1f", host, elapsed_ms)
            return conn
        except Exception as exc:  # noqa: BLE001 - deliberately broad: always log, then retry or raise
            last_error = exc
            if attempt <= retries:
                logger.info(
                    "ssh.connect.retry host=%s attempt=%d error=%s", host, attempt, exc
                )
            else:
                logger.info(
                    "ssh.connect.failed host=%s attempts=%d error=%s",
                    host,
                    attempt,
                    exc,
                )
    assert last_error is not None
    raise last_error
