"""Optional user-private, pinned Python provisioning after SSH is verified."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shlex
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath

VERSION = "3.13.18"
RELEASE = "20261026"
ARTIFACTS = {
    "arm64": (
        "aarch64",
        "8392b6ea51d94391befb2c31ebe75463f045d0b69dfcf5e1bb3234e1f5fa3405",
    ),
    "x86_64": (
        "x86_64",
        "7412c19d896c99431b74a18a5643e110449a7f9590fbab179c3e5ab27b9aa9e3",
    ),
}


def validate_archive(path):
    total = 0
    with tarfile.open(path, "r:gz") as archive:
        for count, member in enumerate(archive):
            if count > 100000:
                raise ValueError("Runtime archive has too many members")
            name = PurePosixPath(member.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not name.parts
                or name.parts[0] != "python"
            ):
                raise ValueError("Unsafe runtime archive path")
            if member.issym() or member.islnk():
                if ".." in PurePosixPath(member.linkname).parts:
                    raise ValueError(
                        "Runtime links must not traverse parent directories"
                    )
                target = (
                    (name.parent / member.linkname)
                    if member.issym()
                    else PurePosixPath(member.linkname)
                )
                if not target.parts or target.parts[0] != "python":
                    raise ValueError("Runtime link escapes Python directory")
                depth = 0
                for part in target.parts:
                    if part == "..":
                        depth -= 1
                    elif part != ".":
                        depth += 1
                    if depth < 1 or target.is_absolute():
                        raise ValueError("Unsafe runtime archive link")
            elif not member.isfile() and not member.isdir():
                raise ValueError("Unsupported runtime archive member")
            total += member.size
            if total > 300_000_000:
                raise ValueError("Runtime archive exceeds unpacked limit")


def download(architecture, path):
    arch, expected = ARTIFACTS[architecture]
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/cpython-{VERSION}%2B{RELEASE}-{arch}-apple-darwin-install_only_stripped.tar.gz"
    digest = hashlib.sha256()
    count = 0
    started = time.monotonic()
    with urllib.request.urlopen(url, timeout=30) as response, open(path, "wb") as out:
        while block := response.read(256 * 1024):
            if time.monotonic() - started > 90:
                raise TimeoutError("Runtime download exceeded time limit")
            count += len(block)
            if count > 35_000_000:
                raise ValueError("Runtime download exceeds limit")
            digest.update(block)
            out.write(block)
    if digest.hexdigest() != expected:
        raise ValueError("Runtime checksum mismatch")
    validate_archive(path)
    return expected


async def provision(conn, architecture, progress, permission):
    """Only called on the existing pinned connection; no system Python or sudo."""
    if architecture not in ARTIFACTS:
        raise ValueError("Unsupported Mac architecture")
    progress("downloading")
    fd, filename = tempfile.mkstemp(prefix="fleet-python-", suffix=".tgz")
    os.close(fd)
    task = asyncio.create_task(asyncio.to_thread(download, architecture, filename))
    try:
        # Keep the temporary file alive until the download thread has actually ended.
        expected = await asyncio.shield(task)
        permission()
        progress("uploading")
        setup = await asyncio.wait_for(
            conn.run(
                "umask 077; /usr/bin/mktemp -d /tmp/remote-fleet-python.XXXXXXXX",
                check=True,
            ),
            15,
        )
        remote = setup.stdout.strip()
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
[ "$(/usr/bin/stat -f %u "$base")" = "$(id -u)" ] || exit 1
chmod -N "$base"; chmod 700 "$base"
[ ! -L "$base/python-3.13.18" ] || exit 1
[ ! -e "$base/python-3.13.18" ] || { echo 'Private runtime path already exists; inspect before replacing.' >&2; exit 1; }
cd STAGE
actual=$(/usr/bin/shasum -a 256 runtime.tgz); [ "${actual%% *}" = DIGEST ]
/usr/bin/tar -xzf runtime.tgz
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
            except Exception:  # noqa: BLE001, S110 - retain original cancellation, remove staging file
                pass
        Path(filename).unlink(missing_ok=True)
