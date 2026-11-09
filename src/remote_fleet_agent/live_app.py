"""Loopback workspace application, authenticated HTTP API and opt-in local scheduler."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import webbrowser
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.live_service import LiveService
from remote_fleet_agent.live_store import Conflict, Denied
from remote_fleet_agent.storage import private_dir, read_json, save_json


def register(sub):
    app = sub.add_parser(
        "workspace-app",
        help="Authenticated local workspace, simulator and optional collections",
    )
    app.add_argument("mode", choices=["init", "add-client", "serve"])
    app.add_argument("--directory", required=True, type=Path)
    app.add_argument("--config", type=Path)
    app.add_argument("--jobs", type=Path)
    app.add_argument("--name")
    app.add_argument("--enable-collection", action="store_true")
    app.add_argument("--enable-schedules", action="store_true")
    app.add_argument("--key", type=Path)
    app.add_argument("--authorisation-reference")
    app.add_argument("--access-expires-at")
    app.add_argument("--port", type=int, default=8877)
    app.add_argument("--open", action="store_true")


def initialize(directory):
    directory = Path(directory).expanduser().resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose an empty workspace directory.")
    private_dir(directory)
    save_json(directory / "workspace.json", {"schema": 1, "clients": []})
    service = LiveService(directory)
    try:
        actor, token = service.store.add_operator("Local owner", "owner", {})
        save_json(
            directory / ".state/owner-access.json",
            {"operator_id": actor["id"], "token": token},
        )
    finally:
        service.store.close()
    return directory


def add_client(directory, config, jobs, **options):
    directory = Path(directory).resolve()
    config = Path(config).expanduser().resolve()
    jobs = Path(jobs).expanduser().resolve()
    c = FleetConfig.load(config, require_known_hosts=False)
    registry = read_json(directory / "workspace.json")
    if any(
        FleetConfig.load(r["config"], require_known_hosts=False).client == c.client
        for r in registry["clients"]
    ):
        raise ValueError(
            "Client already registered; review workspace.json to update its policy."
        )
    entry = {
        "config": str(config),
        "jobs": str(jobs),
        "name": options.get("name") or c.client,
        "allow_collection": bool(options.get("enable_collection")),
        "allow_schedules": bool(options.get("enable_schedules")),
    }
    for key in ("authorisation_reference", "access_expires_at"):
        if options.get(key):
            entry[key] = options[key]
    if options.get("key"):
        entry["key_path"] = str(Path(options["key"]).expanduser().resolve())
    if entry["allow_collection"] and not c.simulation:
        FleetConfig.load(config)
        if (
            not entry.get("key_path")
            or not Path(entry["key_path"]).is_file()
            or not entry.get("authorisation_reference")
        ):
            raise ValueError(
                "Live access needs a private key, pinned host keys and an authorisation reference."
            )
    if entry.get("access_expires_at"):
        from remote_fleet_agent.live_service import utc

        utc(entry["access_expires_at"])
    registry["clients"].append(entry)
    save_json(directory / "workspace.json", registry)
    return {
        "client": c.client,
        "simulation": c.simulation,
        "collection_enabled": entry["allow_collection"],
    }


def seed_demo(directory):
    """Create two fictional client workspaces; never alter existing registered clients."""
    directory = initialize(directory)
    for client, name in [
        ("northstar-demo", "Northstar · simulated"),
        ("harbour-demo", "Harbour · simulated"),
    ]:
        folder = private_dir(directory / client)
        hosts = []
        for i, (platform, scenario) in enumerate(
            [
                ("windows", "healthy"),
                ("macos", "attention"),
                ("linux", "healthy"),
                ("linux", "limited"),
                ("linux", "offline"),
            ]
        ):
            host = f"{platform}-{i + 1:02d}"
            hosts.append(
                {
                    "name": host,
                    "target": f"demo@{host}.invalid",
                    "platform": platform,
                    "scenario": scenario,
                    "approved": True,
                    "site": "head-office" if i < 3 else "warehouse",
                    "groups": ["all"],
                }
            )
        save_json(
            folder / "fleet.json",
            {
                "client": client,
                "simulation": True,
                "known_hosts": "unused-known-hosts",
                "hosts": hosts,
            },
        )
        add_client(
            directory,
            folder / "fleet.json",
            folder / "jobs",
            name=name,
            enable_collection=True,
            enable_schedules=True,
        )
    return directory


class Application:
    def __init__(self, directory, port=8877):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid local port.")
        self.directory = Path(directory).resolve()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

        async def create():
            return LiveService(self.directory)

        self.service = self.call(create())
        self.sessions = {}
        self.login_attempts = []
        self.scheduler_error = None
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), self.handler())
        except OSError:
            self.call(self.service.close())
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.loop.close()
            raise
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.scheduler = self.call(self.start_scheduler())
        logging.getLogger(__name__).info(
            "workspace_ready", extra={"port": self.server.server_port}
        )

    def call(self, awaitable, timeout=30):
        return asyncio.run_coroutine_threadsafe(awaitable, self.loop).result(
            timeout=timeout
        )

    async def start_scheduler(self):
        async def run():
            while True:
                try:
                    await self.service.tick()
                    self.scheduler_error = None
                except (OSError, ValueError, PermissionError) as exc:
                    self.scheduler_error = type(exc).__name__
                await asyncio.sleep(15)

        return asyncio.create_task(run())

    async def dispatch(self, method, path, data, session=None):
        service = self.service
        now = datetime.now(UTC)
        if path == "/api/login" and method == "POST":
            self.login_attempts = [
                t for t in self.login_attempts if (now - t).total_seconds() < 60
            ]
            if len(self.login_attempts) >= 12:
                raise Denied("Too many login attempts. Wait one minute.")
            self.login_attempts.append(now)
            token = data.get("token")
            if not isinstance(token, str) or len(token) > 256:
                raise Denied("Invalid access token.")
            actor = service.store.authenticate(token)
            ident = secrets.token_urlsafe(32)
            self.sessions[ident] = {
                "actor": actor["id"],
                "csrf": secrets.token_urlsafe(24),
                "expires": now + timedelta(hours=8),
            }
            return {"signed_in": True, "_cookie": ident}
        if not session or session not in self.sessions:
            raise Denied("Sign in with your operator access token.")
        info = self.sessions[session]
        if info["expires"] <= now:
            del self.sessions[session]
            raise Denied("Session expired. Sign in again.")
        actor = service.store.operator(info["actor"])
        if method == "POST" and data.pop("_csrf", None) != info["csrf"]:
            raise Denied("Refresh this page before saving.")
        split = urlsplit(path)
        query = parse_qs(split.query)
        parts = split.path.strip("/").split("/")
        site = query.get("site", [""])[0]
        if split.path == "/api/bootstrap" and method == "GET":
            return {
                **await service.bootstrap(actor),
                "csrf": info["csrf"],
                "scheduler_error": self.scheduler_error,
            }
        if split.path == "/api/health" and method == "GET":
            service.store.require(actor, "*")
            return {
                "live": True,
                "ready": service.store.integrity and self.scheduler_error is None,
                "schema": 1,
                "scheduler_error": self.scheduler_error,
                "active_repairs": len(service.repairs.tasks),
            }
        if split.path == "/api/operations" and method == "GET":
            from remote_fleet_agent.operations import parse_query

            return service.operations(actor, parse_query(split.query))
        if split.path == "/api/devices" and method == "GET":
            from remote_fleet_agent.operations import devices, parse_query

            return devices(service, actor, parse_query(split.query))
        if split.path == "/api/logout" and method == "POST":
            del self.sessions[session]
            return {"signed_out": True, "_clear_cookie": True}
        if split.path == "/api/operators":
            service.store.require(actor, "*")
            if method == "GET":
                return [
                    dict(service.store.operator(r[0]))
                    for r in service.store.db.execute(
                        "SELECT id FROM operators WHERE enabled=1"
                    )
                ]
            if method == "POST":
                scopes = data.get("scopes", {})
                if not isinstance(scopes, dict):
                    raise ValueError("Scopes must map client IDs to * or site lists.")
                for client, sites in scopes.items():
                    service.descriptor(client)
                    if sites != "*" and (
                        not isinstance(sites, list)
                        or not sites
                        or any(s not in service.sites(client) for s in sites)
                    ):
                        raise ValueError("Invalid operator site scope.")
                operator, token = service.store.add_operator(
                    data.get("name"), data.get("role"), scopes, actor
                )
                return {
                    "operator": operator,
                    "token": token,
                    "note": "Shown once. Store privately and give only to this operator.",
                }
        if (
            len(parts) == 4
            and parts[:2] == ["api", "operators"]
            and parts[3] == "disable"
            and method == "POST"
        ):
            service.store.require(actor, "*")
            if parts[2] == actor["id"]:
                raise Denied("The active owner cannot disable their own access.")
            service.store.operator(parts[2])
            with service.store.db:
                service.store.db.execute(
                    "UPDATE operators SET enabled=0 WHERE id=?", (parts[2],)
                )
                service.store.audit(
                    actor, "operator.disabled", data={"operator": parts[2]}
                )
            return {"disabled": True}
        if len(parts) < 4 or parts[:2] != ["api", "clients"]:
            raise FileNotFoundError("Route not found.")
        client = parts[2]
        action = parts[3]
        service.grants(actor, client)
        if site:
            service.require_site(actor, client, site)
        if action in ("investigations", "repair-plans"):
            workflow = (
                service.investigations
                if action == "investigations"
                else service.repairs
            )
            if method == "GET" and len(parts) == 4:
                return workflow.list(actor, client)
            if method == "POST" and len(parts) == 4:
                return (
                    workflow.request(actor, client, data)
                    if action == "investigations"
                    else workflow.propose(actor, client, data)
                )
            if len(parts) >= 5:
                ident = parts[4]
                if method == "GET" and len(parts) == 5:
                    return workflow.present(workflow.get(actor, client, ident))
                if method == "GET" and len(parts) == 6 and parts[5] == "history":
                    return workflow.history(
                        actor,
                        client,
                        ident,
                        int(query.get("offset", ["0"])[0]),
                        int(query.get("limit", ["10"])[0]),
                    )
                if method == "POST" and len(parts) == 6:
                    operation = parts[5]
                    if action == "investigations":
                        if operation == "reconcile":
                            return workflow.reconcile(actor, client, ident, data)
                        if operation == "confirm":
                            return workflow.confirm(actor, client, ident, data)
                        if operation == "close":
                            return workflow.close(actor, client, ident, data)
                        if operation == "run":
                            return await workflow.run(actor, client, ident, data)
                    else:
                        if operation in ("approve", "reject"):
                            return workflow.decide(
                                actor, client, ident, data, operation == "approve"
                            )
                        if operation in ("execute", "ready", "cancel", "reconcile"):
                            return getattr(workflow, operation)(
                                actor, client, ident, data
                            )
            raise FileNotFoundError("Administration route not found")
        if action == "support-cases":
            if method == "GET" and len(parts) == 4:
                return service.support.list(actor, client, site)
            if method == "GET" and len(parts) == 5:
                return service.support.present(
                    service.support.get(actor, client, parts[4])
                )
            if method == "GET" and len(parts) == 6 and parts[5] == "history":
                case = service.support.get(actor, client, parts[4])
                index = int(query.get("index", ["-1"])[0])
                if not 0 <= index < len(case["repair_history"]):
                    raise ValueError("Invalid repair history index")
                return {
                    "case_id": case["id"],
                    "index": index,
                    "record": case["repair_history"][index],
                }
            if method == "POST" and len(parts) == 4:
                return service.support.present(
                    service.support.create(actor, client, data)
                )
            if method == "POST" and len(parts) == 6:
                ident, operation = parts[4:6]
                if operation == "investigate":
                    return service.support.present(
                        service.support.investigate(actor, client, ident, data)
                    )
                if operation == "propose":
                    return service.support.present(
                        service.support.propose(actor, client, ident, data)
                    )
                if operation in ("approve", "reject"):
                    return service.support.present(
                        service.support.decide(
                            actor, client, ident, data, operation == "approve"
                        )
                    )
                if operation == "execute":
                    return service.support.present(
                        service.support.execute(actor, client, ident, data)
                    )
                if operation == "confirm":
                    return service.support.present(
                        service.support.confirm(actor, client, ident, data)
                    )
            raise FileNotFoundError("Support route not found.")
        if method == "GET":
            if action == "snapshot":
                return service.snapshot(actor, client, site)
            if action == "onboarding":
                return service.onboarding(actor, client, site)
            if action == "activity":
                return service.activity(actor, client, site)
            if action == "job" and len(parts) == 5:
                return await service.job(actor, client, parts[4])
            if action == "report":
                return {
                    "_html": await service.report(
                        actor,
                        client,
                        site,
                        query.get("shareable", ["true"])[0] != "false",
                    )
                }
            if action == "compare":
                return await service.compare(
                    actor,
                    client,
                    query.get("before", [""])[0],
                    query.get("after", [""])[0],
                    site,
                )
        if method == "POST":
            if action == "preview":
                return service.preview(actor, client, data)
            if action == "start":
                return await service.start(actor, client, data)
            if action == "retry":
                return await service.retry(actor, client, data.get("id", ""))
            if action == "cancel":
                return await service.cancel(actor, client, data.get("id", ""))
            if action == "manual":
                return service.save_manual(actor, client, data)
            if action == "tasks":
                return service.save_task(actor, client, data)
            if action == "views":
                return service.save_view(actor, client, data)
            if action == "branding":
                return service.save_branding(actor, client, data)
            if action == "schedules":
                return service.save_schedule(actor, client, data)
            if action == "pause-schedule":
                service.store.require(actor, "schedules")
                old = service.scoped_record(
                    actor, "schedule", client, data.get("id", "")
                )
                value = {
                    k: v
                    for k, v in old.items()
                    if k not in ("id", "revision", "site", "owner_id")
                }
                value.update(enabled=False, state="disabled")
                return service.store.save(
                    "schedule",
                    client,
                    old["site"],
                    actor,
                    value,
                    old["id"],
                    data.get("revision"),
                )
            if action == "baseline":
                return await service.baseline(actor, client, data)
        raise FileNotFoundError("Route not found.")

    def handler(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "FleetWorkspace"

            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def log_message(self, *args):
                pass

            def reply(self, status, body, kind="application/json", cookie=None):
                self.send_response(status)
                self.send_header("Content-Type", kind + "; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                policy = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                if kind == "text/html" and urlsplit(self.path).path.endswith("/report"):
                    policy = policy.replace(
                        "style-src 'self'", "style-src 'self' 'unsafe-inline'"
                    )
                self.send_header("Content-Security-Policy", policy)
                if cookie is not None:
                    self.send_header(
                        "Set-Cookie",
                        "fleet_session="
                        + cookie
                        + "; HttpOnly; SameSite=Strict; Path=/"
                        + ("; Max-Age=0" if not cookie else "; Max-Age=28800"),
                    )
                self.end_headers()
                self.wfile.write(body)

            def handle_request(self):
                try:
                    if self.headers.get("Host") != urlsplit(app.url).netloc:
                        raise Denied("Invalid local host.")
                    if self.command == "POST" and self.headers.get("Origin") != app.url:
                        raise Denied(
                            "Requests must originate from this local application."
                        )
                    if self.headers.get("Origin") and self.headers["Origin"] != app.url:
                        raise Denied("Cross-origin access denied.")
                    path = urlsplit(self.path).path
                    if self.command == "GET" and path in (
                        "/",
                        "/app.js",
                        "/operations.js",
                        "/app.css",
                        "/logo.svg",
                        "/favicon.svg",
                    ):
                        name = {
                            "/": "index.html",
                            "/app.js": "app.js",
                            "/operations.js": "operations.js",
                            "/app.css": "app.css",
                            "/logo.svg": "logo.svg",
                            "/favicon.svg": "favicon.svg",
                        }[path]
                        body = (
                            files("remote_fleet_agent")
                            .joinpath("app_assets", name)
                            .read_bytes()
                        )
                        self.reply(
                            200,
                            body,
                            {
                                "/": "text/html",
                                "/app.js": "text/javascript",
                                "/operations.js": "text/javascript",
                                "/app.css": "text/css",
                                "/logo.svg": "image/svg+xml",
                                "/favicon.svg": "image/svg+xml",
                            }[path],
                        )
                        return
                    if not path.startswith("/api/"):
                        raise FileNotFoundError("Route not found.")
                    data = {}
                    if self.command == "POST":
                        if self.headers.get("Transfer-Encoding"):
                            raise ValueError("Chunked requests are unsupported.")
                        size = int(self.headers.get("Content-Length", "0"))
                        if (
                            not 0
                            < size
                            <= (1200000 if "/repair-plans" in path else 131072)
                        ):
                            raise ValueError("Request is empty or too large.")
                        if (
                            self.headers.get("Content-Type", "").split(";")[0]
                            != "application/json"
                        ):
                            raise ValueError("JSON request required.")
                        data = json.loads(self.rfile.read(size))
                        if not isinstance(data, dict):
                            raise ValueError("JSON object required.")
                        data["_csrf"] = self.headers.get("X-Fleet-CSRF")
                    cookie = SimpleCookie()
                    cookie.load(self.headers.get("Cookie", ""))
                    session = cookie.get("fleet_session")
                    session = session.value if session else None
                    result = app.call(
                        app.dispatch(self.command, self.path, data, session),
                        timeout=310 if path.endswith("/run") else 30,
                    )
                    newcookie = (
                        result.pop("_cookie", None)
                        if isinstance(result, dict)
                        else None
                    )
                    if isinstance(result, dict) and result.pop("_clear_cookie", False):
                        newcookie = ""
                    if isinstance(result, dict) and "_html" in result:
                        self.reply(200, result["_html"].encode(), "text/html")
                        return
                    self.reply(200, json.dumps(result).encode(), cookie=newcookie)
                except Conflict as exc:
                    self.reply(409, json.dumps({"error": str(exc)}).encode())
                except Denied as exc:
                    self.reply(403, json.dumps({"error": str(exc)}).encode())
                except FileNotFoundError:
                    self.reply(404, b'{"error":"Record not found in this scope."}')
                except (ValueError, TypeError, KeyError) as exc:
                    self.reply(400, json.dumps({"error": str(exc)[:500]}).encode())
                except Exception:  # noqa: BLE001 - sanitize failures at the HTTP boundary
                    self.reply(
                        500,
                        b'{"error":"Local operation failed; inspect the workspace files or restart the app."}',
                    )

            do_GET = handle_request
            do_POST = handle_request

        return Handler

    def close(self):
        logging.getLogger(__name__).info("workspace_draining")

        async def stop():
            self.scheduler.cancel()
            await asyncio.gather(self.scheduler, return_exceptions=True)
            await self.service.close()

        self.call(stop())
        self.server.server_close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()
        logging.getLogger(__name__).info("workspace_shutdown_complete")


def execute(args):
    if args.mode == "init":
        seed_demo(args.directory)
        print(
            "Workspace created with two fictional clients. Owner access is stored privately under .state/owner-access.json."
        )
        return 0
    if args.mode == "add-client":
        if not args.config or not args.jobs:
            raise ValueError("--config and --jobs are required.")
        print(
            json.dumps(
                add_client(
                    args.directory,
                    args.config,
                    args.jobs,
                    **{
                        k: v
                        for k, v in vars(args).items()
                        if k not in ("directory", "config", "jobs")
                    },
                )
            )
        )
        return 0
    app = Application(args.directory, args.port)
    save_json(app.directory / ".state/runtime.json", {"url": app.url})
    print("Local workspace: " + app.url, flush=True)
    if args.open:
        token = read_json(app.directory / ".state/owner-access.json")["token"]
        webbrowser.open(app.url + "/#token=" + token)
    try:
        app.server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
    return 0
