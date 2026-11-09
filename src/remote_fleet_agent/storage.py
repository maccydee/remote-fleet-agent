"""Small private, atomic JSON stores and non-expiring cross-process write locks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from remote_fleet_agent.host_platform import restrict_private


def stamp():
    return datetime.now(UTC).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("invalid record ID")
    return value


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("storage directory must not be a symlink")
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not existed:
        restrict_private(path, directory=True)
    return path


def atomic_text(path, text):
    path = Path(path)
    private_dir(path.parent)
    if path.is_symlink():
        raise ValueError("storage file must not be a symlink")
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            restrict_private(temporary)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_json(path, value):
    atomic_text(path, json.dumps(value, indent=2) + "\n")


def read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("storage file must not be a symlink")
    return json.loads(path.read_text(encoding="utf-8-sig"))


@contextmanager
def locked(path):
    path = Path(path)
    private_dir(path.parent)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(
            "record is locked; inspect the owning process before clearing a stale lock"
        ) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"owner_pid": os.getpid(), "created_at": stamp()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        path.unlink(missing_ok=True)
