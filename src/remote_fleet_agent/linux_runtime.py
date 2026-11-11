"""Linux Python setup over verified SSH; never changes the system Python."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import tempfile
import time
import urllib.request
from pathlib import Path

from remote_fleet_agent.mac_runtime import RELEASE, VERSION, validate_archive

ARTIFACTS = {
    (
        "aarch64",
        "gnu",
    ): "578d9da1983463ffb0c8c98ab0da2f18177f44069328eb0af847860c1eb05c3e",
    (
        "aarch64",
        "musl",
    ): "61d02dd9a1b9631723c7c5d593021cde684cafc31b18a535b9584377a7b54038",
    (
        "x86_64",
        "gnu",
    ): "df15fe60d3ff55bf9dcf25824b5572756bb8816ff93865a273d3301d7066a8b7",
    (
        "x86_64",
        "musl",
    ): "d997b3bd2d0651cfba6dd43c18c31b35d3a142819015fd5c35338c2ea27d4ceb",
}
EXISTING = """if [ -x "$HOME/.remote-fleet-agent/python-3.13.18/bin/python3" ]; then
    "$HOME/.remote-fleet-agent/python-3.13.18/bin/python3" -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
elif command -v python3 >/dev/null 2>&1; then
    python3 -I -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'
else exit 1; fi"""


def download(architecture, libc, path):
    expected = ARTIFACTS[(architecture, libc)]
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/cpython-{VERSION}%2B{RELEASE}-{architecture}-unknown-linux-{libc}-install_only_stripped.tar.gz"
    digest = hashlib.sha256()
    count = 0
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=30) as source, open(path, "wb") as out:
        while block := source.read(256 * 1024):
            count += len(block)
            if count > 45_000_000 or time.monotonic() - started > 90:
                raise ValueError("Runtime download exceeded size or time limit")
            out.write(block)
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError("Runtime checksum mismatch")
    validate_archive(path)
    return expected


async def provision(conn, architecture, progress, permission):
    permission()
    progress("checking")
    existing = await asyncio.wait_for(conn.run(EXISTING, check=False), 15)
    if existing.exit_status == 0:
        progress("ready")
        return
    architecture = "aarch64" if architecture == "arm64" else architecture
    detected = await asyncio.wait_for(
        conn.run(
            "getconf GNU_LIBC_VERSION 2>/dev/null || ldd --version 2>&1", check=False
        ),
        15,
    )
    description = detected.stdout.lower()
    libc = "gnu" if "glibc" in description else "musl" if "musl" in description else ""
    if (architecture, libc) not in ARTIFACTS:
        raise ValueError(
            "Supported private runtimes require x86_64/aarch64 and glibc/musl"
        )
    permission()
    progress("downloading")
    fd, filename = tempfile.mkstemp(prefix="fleet-linux-python-", suffix=".tgz")
    os.close(fd)
    task = asyncio.create_task(
        asyncio.to_thread(download, architecture, libc, filename)
    )
    try:
        expected = await asyncio.shield(task)
        permission()
        progress("uploading")
        staging = await asyncio.wait_for(
            conn.run(
                "umask 077; mktemp -d /tmp/remote-fleet-python.XXXXXXXX", check=True
            ),
            15,
        )
        remote = staging.stdout.strip()
        if not re.fullmatch(r"/tmp/remote-fleet-python\.[A-Za-z0-9]{8}", remote):
            raise ValueError("Invalid remote staging directory")
        try:
            async with conn.start_sftp_client() as sftp:
                await asyncio.wait_for(sftp.put(filename, remote + "/runtime.tgz"), 120)
            permission()
            progress("installing")
            script = """set -eu
umask 077
base="$HOME/.remote-fleet-agent"
[ ! -L "$base" ] || exit 1
mkdir -p "$base"
[ "$(stat -c %u "$base")" = "$(id -u)" ] || exit 1
if command -v setfacl >/dev/null 2>&1; then setfacl -b -k "$base"; fi
chmod 700 "$base"
[ ! -L "$base/python-3.13.18" ] && [ ! -e "$base/python-3.13.18" ] || exit 1
cd STAGE
actual=$(sha256sum runtime.tgz); [ "${actual%% *}" = DIGEST ]
tar -xzf runtime.tgz
./python/bin/python3 -I -c 'import sys; assert sys.version_info[:3] == (3,13,18)'
mv python "$base/python-3.13.18"
""".replace("STAGE", shlex.quote(remote)).replace("DIGEST", shlex.quote(expected))
            permission()
            await asyncio.wait_for(conn.run("sh -s", input=script, check=True), 180)
            progress("ready")
        finally:
            await asyncio.wait_for(
                conn.run("rm -rf " + shlex.quote(remote), check=False), 15
            )
    finally:
        if not task.done():
            try:
                await asyncio.shield(task)
            except Exception:  # noqa: BLE001, S110 - preserve cancellation; remove download staging
                pass
        Path(filename).unlink(missing_ok=True)
