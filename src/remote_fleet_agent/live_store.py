"""Local workspace records, scoped operator tokens and a chained activity journal."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from pathlib import Path

from remote_fleet_agent.host_platform import restrict_private
from remote_fleet_agent.storage import atomic_text, private_dir, stamp

ROLES = {
    "observer": {"read", "views"},
    "technician": {"read", "views", "collect", "manual", "tasks", "baselines"},
    "manager": {
        "approve_repairs",
        "read",
        "views",
        "collect",
        "manual",
        "tasks",
        "baselines",
        "schedules",
        "branding",
        "shared_views",
    },
    "owner": {"*"},
}


class Denied(PermissionError):
    pass


class Conflict(ValueError):
    pass


class SessionExpired(Denied):
    """An absent/expired HTTP session, distinct from a scope or role denial."""


class LiveStore:
    def __init__(self, directory):
        self.folder = private_dir(Path(directory) / ".state")
        key = self.folder / "journal-key"
        if not key.exists():
            atomic_text(key, secrets.token_hex(32))
        restrict_private(key)
        self.key = key.read_bytes()
        path = self.folder / "workspace.sqlite3"
        if path.is_symlink():
            raise ValueError("Workspace database must not be a symbolic link.")
        if path.exists():
            restrict_private(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        restrict_private(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""CREATE TABLE IF NOT EXISTS operators(id TEXT PRIMARY KEY,name TEXT,role TEXT,scopes TEXT,token_hash TEXT UNIQUE,enabled INTEGER);
CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT PRIMARY KEY,client TEXT,site TEXT,owner TEXT,revision INTEGER,data TEXT);
CREATE TABLE IF NOT EXISTS activity(seq INTEGER PRIMARY KEY,ts TEXT,actor TEXT,client TEXT,site TEXT,event TEXT,data TEXT,previous TEXT,signature TEXT);""")
        self.integrity = self.verify()

    def verify(self):
        previous = ""
        for row in self.db.execute("SELECT * FROM activity ORDER BY seq"):
            value = {
                k: row[k]
                for k in ("ts", "actor", "client", "site", "event", "data", "previous")
            }
            signature = hmac.new(
                self.key, json.dumps(value, sort_keys=True).encode(), hashlib.sha256
            ).hexdigest()
            if row["previous"] != previous or not hmac.compare_digest(
                row["signature"], signature
            ):
                return False
            previous = signature
        return True

    def audit(self, actor, event, client="", site="", data=None):
        if not self.integrity:
            raise Denied(
                "Activity journal integrity failed; writes and endpoint operations are blocked."
            )
        row = self.db.execute(
            "SELECT signature FROM activity ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        value = {
            "ts": stamp(),
            "actor": actor["id"],
            "client": client,
            "site": site,
            "event": event,
            "data": json.dumps(data or {}, sort_keys=True),
            "previous": row[0] if row else "",
        }
        signature = hmac.new(
            self.key, json.dumps(value, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        self.db.execute(
            "INSERT INTO activity(ts,actor,client,site,event,data,previous,signature) VALUES(?,?,?,?,?,?,?,?)",
            (*value.values(), signature),
        )

    def operator(self, ident):
        row = self.db.execute("SELECT * FROM operators WHERE id=?", (ident,)).fetchone()
        if not row or not row["enabled"]:
            raise Denied("Operator is disabled or unavailable.")
        return {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "scopes": json.loads(row["scopes"]),
        }

    def authenticate(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = self.db.execute(
            "SELECT id FROM operators WHERE token_hash=? AND enabled=1", (digest,)
        ).fetchone()
        if not row:
            raise Denied("Invalid access token.")
        return self.operator(row["id"])

    def add_operator(self, name, role, scopes, actor=None):
        if (
            role not in ROLES
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 100
        ):
            raise ValueError("A name and supported role are required.")
        token = secrets.token_urlsafe(32)
        ident = uuid.uuid4().hex
        with self.db:
            self.db.execute(
                "INSERT INTO operators VALUES(?,?,?,?,?,1)",
                (
                    ident,
                    name.strip(),
                    role,
                    json.dumps(scopes),
                    hashlib.sha256(token.encode()).hexdigest(),
                ),
            )
            created = self.operator(ident)
            self.audit(
                actor or created,
                "operator.created",
                data={
                    "operator": ident,
                    "name": name.strip(),
                    "role": role,
                    "scopes": scopes,
                },
            )
        return created, token

    def allowed(self, actor, action):
        return "*" in ROLES[actor["role"]] or action in ROLES[actor["role"]]

    def require(self, actor, action):
        if not self.allowed(actor, action):
            raise Denied("Your role does not allow this action.")
        if action != "read" and not self.integrity:
            raise Denied("Activity journal integrity failed; workspace is read-only.")

    def get(self, kind, ident, client):
        row = self.db.execute(
            "SELECT * FROM records WHERE kind=? AND id=? AND client=?",
            (kind, ident, client),
        ).fetchone()
        if not row:
            raise FileNotFoundError("Record not found in this client.")
        return {
            "id": row["id"],
            "site": row["site"],
            "owner_id": row["owner"],
            "revision": row["revision"],
            **{
                k: v
                for k, v in json.loads(row["data"]).items()
                if k not in ("id", "site", "owner_id", "revision")
            },
        }

    def list(self, kind, client):
        return [
            self.get(kind, row[0], client)
            for row in self.db.execute(
                "SELECT id FROM records WHERE kind=? AND client=? ORDER BY rowid DESC",
                (kind, client),
            )
        ]

    def iter_records(self, kind: str, client: str, sites: set[str]):
        """Stream only the requested client's sites; caller enforces current grants."""
        if not sites:
            return
        placeholders = ",".join("?" for _ in sites)
        cursor = self.db.execute(
            f"SELECT id FROM records WHERE kind=? AND client=? AND site IN ({placeholders}) ORDER BY rowid DESC",
            (kind, client, *sorted(sites)),
        )
        for row in cursor:
            yield self.get(kind, row[0], client)

    def save(self, kind, client, site, actor, data, ident=None, revision=None):
        if not self.integrity:
            raise Denied("Activity journal integrity failed.")
        if set(data) & {"id", "site", "owner_id", "revision"}:
            raise ValueError("Reserved record fields.")
        if ident:
            before = self.get(kind, ident, client)
            if type(revision) is not int or revision != before["revision"]:
                raise Conflict("Record changed; refresh before saving.")
            owner = before["owner_id"]
            version = revision + 1
        else:
            ident = uuid.uuid4().hex
            owner = actor["id"]
            version = 1
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?)",
                (kind, ident, client, site, owner, version, json.dumps(data)),
            )
            self.audit(
                actor,
                kind + ".saved",
                client,
                site,
                {"record": ident, "revision": version},
            )
        return self.get(kind, ident, client)

    def close(self):
        self.db.close()
