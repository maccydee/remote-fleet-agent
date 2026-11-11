"""One-device, short-lived TLS enrollment. The workspace HTTP server stays loopback-only."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import shlex
import socket
import ssl
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import asyncssh
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from remote_fleet_agent.administration import EndpointClaim
from remote_fleet_agent.device_identity import resolve_device
from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.storage import (
    atomic_text,
    locked,
    private_dir,
    read_json,
    save_json,
)

DEFAULT_INSTALL_PYTHON = True
MAC_SETUP_DEFAULTS = {
    "method": "connection_kit",
    "identity": "automatic",
    "python_required_for_connection": False,
    "install_python": DEFAULT_INSTALL_PYTHON,
    "python_install_stage": "after_verified_ssh",
}

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def local_address():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 9))
            return probe.getsockname()[0]
        except OSError:
            return ""


def private_ipv4(value):
    ip = ipaddress.IPv4Address(value)
    if not (
        ip in ipaddress.IPv4Network("10.0.0.0/8")
        or ip in ipaddress.IPv4Network("172.16.0.0/12")
        or ip in ipaddress.IPv4Network("192.168.0.0/16")
        or ip.is_loopback
    ):
        raise ValueError("Choose a local private IPv4 address on the engineer network.")
    return str(ip)


def context(folder, address):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Remote Fleet one-device pairing")]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address(address))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    atomic_text(folder / "certificate.pem", pem)
    atomic_text(
        folder / "tls-key.pem",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(folder / "certificate.pem", folder / "tls-key.pem")
    return ctx, pem


class MacEnrollment:
    def __init__(self, service):
        self.service = service
        self.listeners = {}
        self.tasks = set()
        self.receivers = set()
        if service.store.integrity:
            for row in service.store.db.execute(
                "SELECT id,client FROM records WHERE kind='mac_enrollment'"
            ).fetchall():
                old = service.store.get("mac_enrollment", row["id"], row["client"])
                if old["connection_state"] in ("waiting", "verifying"):
                    self.update(
                        old,
                        connection_state="revoked"
                        if old["connection_state"] == "waiting"
                        else "unknown",
                        error="Controller restarted; generate a fresh connection kit or inspect incomplete registration.",
                    )
                elif old["python_state"] in (
                    "pending",
                    "downloading",
                    "uploading",
                    "installing",
                ):
                    self.update(
                        old,
                        python_state="unknown",
                        error="Controller restarted during optional setup; inspect the private runtime before retrying.",
                    )
        self.limit = asyncio.Semaphore(8)

    def permission(self, actor, client, site):
        actor = self.service.store.operator(actor["id"])
        # Enrollment changes access policy; reserve it to workspace owners.
        self.service.store.require(actor, "*")
        self.service.require_site(actor, client, site)
        entry = self.service.descriptor(client)
        config = self.service.config(client)
        if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1" or config.simulation:
            raise Denied(
                "Real enrollment is disabled for simulated clients and audit-only mode."
            )
        if not entry.get("allow_collection") or not entry.get(
            "authorisation_reference"
        ):
            raise Denied("Enable authorized collection before enrolling a device.")
        if entry.get("access_expires_at") and datetime.fromisoformat(
            entry["access_expires_at"]
        ) <= datetime.now(UTC):
            raise Denied("Client access has expired.")
        key = Path(entry.get("key_path", ""))
        if not key.is_file() or key.is_symlink():
            raise Denied("A private client SSH key is required.")
        return actor, entry, config

    def create_client(self, actor, data):
        self.service.store.require(self.service.store.operator(actor["id"]), "*")
        if os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1":
            raise Denied("Enrollment disabled in audit-only mode.")
        client = data.get("client", "")
        reference = data.get("authorisation_reference", "")
        if (
            not isinstance(client, str)
            or not NAME.fullmatch(client)
            or not isinstance(reference, str)
            or not reference.strip()
            or len(reference) > 500
        ):
            raise ValueError("Supply a valid client ID and an authorization reference.")
        root = private_dir(self.service.directory / ".state/enrollment")
        with locked(root / "catalog.lock"):
            catalog = read_json(self.service.directory / "workspace.json")
            if client.casefold() in {
                c.casefold() for c in self.service.load_registry()
            }:
                raise ValueError("Client already exists. Select it from Settings.")
            folder = root / client
            if folder.exists():
                raise ValueError(
                    "Client staging path already exists; inspect it before retrying."
                )
            private_dir(folder)
            key = asyncssh.generate_private_key("ssh-ed25519")
            atomic_text(folder / "engineer-key", key.export_private_key().decode())
            atomic_text(folder / "known_hosts", "")
            save_json(
                folder / "fleet.json",
                {
                    "client": client,
                    "known_hosts": str(folder / "known_hosts"),
                    "hosts": [],
                    "administration_enabled": False,
                },
            )
            catalog["clients"].append(
                {
                    "config": str(folder / "fleet.json"),
                    "jobs": str(folder / "jobs"),
                    "name": client,
                    "key_path": str(folder / "engineer-key"),
                    "allow_collection": True,
                    "allow_schedules": False,
                    "authorisation_reference": reference.strip(),
                }
            )
            with self.service.store.db:
                self.service.store.audit(
                    actor, "enrollment.client_created", client=client
                )
            save_json(self.service.directory / "workspace.json", catalog)
        return {"client": client}

    def status(self, actor, client):
        self.service.store.require(actor, "read")
        self.service.grants(actor, client)
        rows = [
            self.present(r)
            for r in self.service.store.list("mac_enrollment", client)
            if self.service.permitted(actor, client, r["site"])
        ]
        return {
            "controller_address": local_address(),
            "defaults": dict(MAC_SETUP_DEFAULTS),
            "devices": rows[:100],
        }

    @staticmethod
    def present(record):
        return {k: v for k, v in record.items() if k not in ("token_hash", "policy")}

    def update(self, record, **changes):
        current = self.service.store.get(
            "mac_enrollment", record["id"], record["client"]
        )
        actor = {"id": current["owner_id"]}
        value = {
            k: v
            for k, v in current.items()
            if k not in ("id", "site", "owner_id", "revision")
        }
        return self.service.store.save(
            "mac_enrollment",
            current["client"],
            current["site"],
            actor,
            {**value, **changes},
            ident=current["id"],
            revision=current["revision"],
        )

    async def kit(self, actor, client, data):
        site = data.get("site") or "main"
        if not isinstance(site, str) or not NAME.fullmatch(site):
            raise ValueError("Use a valid site name.")
        actor, entry, _ = self.permission(actor, client, site)
        install = data.get("install_python", DEFAULT_INSTALL_PYTHON)
        if type(install) is not bool:
            raise ValueError("Python setup must be an explicit boolean.")
        await self.tick()
        if len(self.listeners) >= 8:
            raise Denied(
                "Eight connection kits are already waiting. Revoke a kit or wait for expiry."
            )
        address = private_ipv4(data.get("controller_address") or local_address())
        token = secrets.token_urlsafe(32)
        record = self.service.store.save(
            "mac_enrollment",
            client,
            site,
            actor,
            {
                "client": client,
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "policy": entry,
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                "connection_state": "waiting",
                "python_state": "pending" if install else "skipped",
                "install_python": install,
                "controller_address": address,
            },
        )
        folder = private_dir(self.service.store.folder / "pairing" / record["id"])
        ctx, pem = context(folder, address)
        try:
            server = await asyncio.start_server(
                lambda r, w: self.receive(record, r, w),
                address,
                0,
                ssl=ctx,
                limit=8192,
                ssl_handshake_timeout=5,
            )
        except OSError:
            self.update(
                record,
                connection_state="failed",
                error="Cannot bind the selected controller address. Choose an address on this engineer machine.",
            )
            raise ValueError(
                "Cannot listen on that address. Select a private IPv4 address belonging to this controller."
            ) from None
        self.listeners[record["id"]] = (server, record)
        port = server.sockets[0].getsockname()[1]
        public = (
            asyncssh.read_private_key(entry["key_path"])
            .export_public_key()
            .decode()
            .strip()
        )
        # Limit this key to this controller and preserve independent authorized keys.
        expiry = ""
        if entry.get("access_expires_at"):
            expiry = (
                'expiry-time="'
                + datetime.fromisoformat(entry["access_expires_at"])
                .astimezone(UTC)
                .strftime("%Y%m%d%H%M%SZ")
                + '",'
            )
        key_line = f'{expiry}from="{address}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-user-rc,no-pty {public} remote-fleet:{client}'
        metadata = (
            "\n".join(
                f"{name}={shlex.quote(value)}"
                for name, value in {
                    "RF_KIT": record["id"],
                    "RF_TOKEN": token,
                    "RF_URL": f"https://{address}:{port}/enroll",
                    "RF_KEY_LINE": key_line,
                }.items()
            )
            + "\ncat > \"$RF_TMP/controller.pem\" <<'FLEET_CERT'\n"
            + pem
            + "FLEET_CERT\n"
        )
        script = (
            files("remote_fleet_agent")
            .joinpath("enrollment_assets/Connect.command")
            .read_text()
            .replace("@@METADATA@@", metadata)
        )
        removal = (
            "#!/bin/sh\nset -eu\n"
            + "line="
            + shlex.quote(key_line)
            + """
file="$HOME/.ssh/authorized_keys"
[ -f "$file" ] && [ ! -L "$file" ] && [ ! -L "$HOME/.ssh" ] || exit 1
[ "$(stat -f %u "$file")" = "$(id -u)" ] && [ "$(stat -f %l "$file")" = 1 ] || exit 1
umask 077
tmp=$(mktemp "$HOME/.ssh/fleet-remove.XXXXXXXX")
trap 'rm -f "$tmp"' EXIT
awk -v line="$line" '$0 != line' "$file" > "$tmp"
cat "$tmp" > "$file"
echo 'This kit’s source-bound key removed. Other keys and Remote Login settings are preserved.'
"""
        )
        out = io.BytesIO()
        with ZipFile(out, "w", ZIP_DEFLATED) as archive:
            for name, content in [
                ("Connect.command", script),
                ("Remove.command", removal),
            ]:
                item = ZipInfo(name)
                item.create_system = 3
                item.external_attr = 0o100700 << 16
                archive.writestr(item, content)
            archive.writestr(
                "READ-ME.txt",
                "Copy Connect.command to ONE Mac, double-click it and approve the administrator prompt. Keep this controller and workspace running on the same private network. Kit expires in 30 minutes and is single-use. macOS may require Remote Login approval in System Settings. No Python is needed for connection or native evidence. Optional Python setup runs only after verified SSH. Access persists until Remove.command is run or the configured client expiry is reached; deleting this kit or revoking pairing does not remove an installed SSH key. Do not share or commit the kit. Remove.command removes only this exact source-bound key. General administration remains separately controlled.\n",
            )
        return {
            "filename": f"{client}-mac-connect.zip",
            "zip_base64": base64.b64encode(out.getvalue()).decode(),
            "enrollment": self.present(record),
        }

    async def receive(self, record, reader, writer):
        task = asyncio.current_task()
        self.receivers.add(task)
        try:
            async with asyncio.timeout(10), self.limit:
                header = await reader.readuntil(b"\r\n\r\n")
                lines = header.decode("ascii").split("\r\n")
                if lines[0] != "POST /enroll HTTP/1.1" or len(header) > 8192:
                    raise ValueError("Invalid request")
                headers = {}
                for line in lines[1:]:
                    if not line:
                        continue
                    key, value = line.split(":", 1)
                    key = key.lower()
                    if key in headers:
                        raise ValueError("Duplicate header")
                    headers[key] = value.strip()
                size = int(headers.get("content-length", "0"))
                if (
                    "transfer-encoding" in headers
                    or not 0 < size <= 16384
                    or headers.get("content-type") != "application/json"
                ):
                    raise ValueError("Invalid body")
                data = json.loads(await reader.readexactly(size))
                peer = writer.get_extra_info("peername")[0]
                await self.accept(record, data, peer)
                status, payload = "202 Accepted", {"received": True}
        except Exception:  # noqa: BLE001 - never echo enrollment secrets from parsing errors
            status, payload = (
                "400 Bad Request",
                {
                    "error": "Pairing rejected. Check expiry, network, authorization and kit status in the engineer workspace."
                },
            )
        try:
            body = json.dumps(payload).encode()
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        finally:
            self.receivers.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def accept(self, record, data, peer):
        current = self.service.store.get(
            "mac_enrollment", record["id"], record["client"]
        )
        if (
            not isinstance(data, dict)
            or data.get("kit") != record["id"]
            or not isinstance(data.get("token"), str)
            or len(data["token"]) > 256
        ):
            raise Denied("Invalid receipt")
        if (
            not hmac.compare_digest(
                current["token_hash"],
                hashlib.sha256(data["token"].encode()).hexdigest(),
            )
            or current["connection_state"] != "waiting"
            or datetime.fromisoformat(current["expires_at"]) <= datetime.now(UTC)
        ):
            raise Denied("Kit expired or consumed")
        actor = self.service.store.operator(current["owner_id"])
        _, entry, _ = self.permission(actor, current["client"], current["site"])
        if entry != current["policy"]:
            raise Denied("Client policy changed; generate a new kit")
        address = private_ipv4(peer)  # Never use a caller-supplied target address.
        username = data.get("username", "")
        public = data.get("host_key", "")
        if (
            not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", username)
            or not isinstance(public, str)
            or len(public) > 1024
        ):
            raise ValueError("Invalid target identity")
        key = asyncssh.import_public_key(public)
        if key.get_algorithm() != "ssh-ed25519":
            raise ValueError("Expected an Ed25519 host key")
        public = key.export_public_key().decode().strip()
        current = self.update(
            current,
            connection_state="verifying",
            target=f"{username}@{address}",
            host_key=public,
        )
        listener = self.listeners.pop(current["id"], None)
        if listener:
            listener[0].close()
        task = asyncio.create_task(self.connect(current, actor, username, address, key))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def connect(self, record, actor, username, address, host_key):
        conn = None
        claim = None
        connected = False
        dispatched = False
        try:
            _, entry, config = self.permission(actor, record["client"], record["site"])
            if entry != record["policy"]:
                raise Denied("Client policy changed")
            conn = await asyncio.wait_for(
                asyncssh.connect(
                    address,
                    port=22,
                    username=username,
                    client_keys=[entry["key_path"]],
                    known_hosts=([host_key], [], []),
                    agent_path=None,
                    config=None,
                ),
                20,
            )
            probe = await asyncio.wait_for(
                conn.run(
                    "/usr/bin/uname -s; /usr/bin/id -un; /usr/bin/uname -m", check=True
                ),
                10,
            )
            identity = probe.stdout.strip().splitlines()
            if (
                len(identity) != 3
                or identity[:2] != ["Darwin", username]
                or identity[2] not in ("arm64", "x86_64")
            ):
                raise Denied("SSH endpoint identity did not match the Mac receipt")
            self.permission(actor, record["client"], record["site"])
            folder = private_dir(self.service.store.folder / "pairing")
            with locked(folder / "registration.lock"):
                raw = read_json(entry["config"])
                config = self.service.config(record["client"])
                if self.service.descriptor(record["client"]) != entry:
                    raise Denied("Client policy changed")
                # Never replace an established pin/target through the enrollment endpoint.
                if any(h.target.split("@")[1] == address for h in config.hosts):
                    raise ValueError(
                        "This address is already enrolled; review its existing device record."
                    )
                pins = (
                    config.known_hosts.read_text()
                    if config.known_hosts.exists()
                    else ""
                )
                if pins.strip() and any(
                    asyncssh.import_known_hosts(pins).match(address, address, 22)
                ):
                    raise ValueError(
                        "Existing host pins require reconciliation before enrollment"
                    )
                target = {
                    "target": f"{username}@{address}",
                    "port": 22,
                    "host_keys": [host_key.get_fingerprint()],
                    "simulation": False,
                }
                device_id = resolve_device(target)
                name = "mac-" + device_id[:12]
                raw.setdefault("hosts", []).append(
                    {
                        "name": name,
                        "target": target["target"],
                        "platform": "macos",
                        "approved": True,
                        "site": record["site"],
                        "device_id": device_id,
                    }
                )
                atomic_text(
                    config.known_hosts,
                    pins.rstrip() + "\n" + address + " " + record["host_key"] + "\n",
                )
                save_json(entry["config"], raw)
            record = self.update(
                record,
                connection_state="connected",
                device=name,
                device_id=device_id,
                architecture=identity[2],
            )
            connected = True
            if record["install_python"]:
                from remote_fleet_agent.mac_runtime import provision

                signature = self.service.fingerprint(record["client"])

                def permission():
                    _, latest, _ = self.permission(
                        actor, record["client"], record["site"]
                    )
                    if latest != entry:
                        raise Denied("Client policy changed")
                    self.service.endpoint_permission(
                        actor, record["client"], [name], signature
                    )

                claim = EndpointClaim(
                    {**target, "device_id": device_id}, "mac-enrollment:" + record["id"]
                )
                permission()
                dispatched = True
                await provision(
                    conn,
                    identity[2],
                    lambda state: self.update(record, python_state=state),
                    permission,
                )
        except asyncio.CancelledError:
            self.update(
                record,
                connection_state="connected" if connected else "unknown",
                python_state="unknown" if dispatched else record["python_state"],
                error="Controller stopped during pairing/setup; inspect before retrying.",
            )
            if claim and dispatched:
                claim.mark_unknown()
                claim = None
            raise
        except Exception:  # noqa: BLE001 - sanitize remote errors; never echo private details
            current = self.service.store.get(
                "mac_enrollment", record["id"], record["client"]
            )
            uncertain = current["python_state"] in ("uploading", "installing")
            if claim and uncertain:
                claim.mark_unknown()
                claim = None
            # Remote errors may contain private environment details; keep them out of UI/journal.
            self.update(
                record,
                connection_state="connected" if connected else "failed",
                python_state="unknown"
                if uncertain
                else "failed"
                if connected and record["install_python"]
                else record["python_state"],
                error="Python setup failed; native diagnostics remain available. Inspect device and runtime path before retrying."
                if connected
                else "SSH verification or registration failed. Check Remote Login, controller reachability, authorization and existing host pins. Generate a fresh kit to retry.",
            )
        finally:
            if claim:
                claim.release()
            if conn:
                conn.close()
                await conn.wait_closed()

    async def tick(self):
        for ident, (server, record) in list(self.listeners.items()):
            if datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC):
                server.close()
                await server.wait_closed()
                self.listeners.pop(ident, None)
                self.update(record, connection_state="expired")

    async def revoke(self, actor, client, ident):
        self.service.store.require(self.service.store.operator(actor["id"]), "*")
        record = self.service.store.get("mac_enrollment", ident, client)
        if record["connection_state"] != "waiting":
            raise ValueError(
                "Only waiting kits can be revoked. Use Remove.command to remove installed access."
            )
        listener = self.listeners.pop(ident, None)
        if listener:
            listener[0].close()
            await listener[0].wait_closed()
        return self.present(self.update(record, connection_state="revoked"))

    async def close(self):
        for server, record in list(self.listeners.values()):
            server.close()
            await server.wait_closed()
            self.update(record, connection_state="revoked")
        self.listeners.clear()
        tasks = list(self.tasks | self.receivers)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
