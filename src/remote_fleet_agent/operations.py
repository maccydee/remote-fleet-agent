"""Scoped, read-only operational summaries. This module never connects to devices."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs

from remote_fleet_agent.config import FleetConfig
from remote_fleet_agent.live_store import Denied
from remote_fleet_agent.storage import read_json

BUCKETS = ("attention", "approval", "active", "open", "history")
STATE_BUCKET = {
    "unknown": "attention",
    "execution_failed": "attention",
    "verification_failed": "attention",
    "issue_persists": "attention",
    "awaiting_approval": "approval",
    "investigating": "active",
    "executing": "active",
    "approved": "active",
    "resolved": "history",
    "pending_target_confirmation": "approval",
    "awaiting_readiness": "approval",
    "scheduled": "active",
    "queued": "active",
    "active": "active",
    "paused": "attention",
    "cancellation_requested": "attention",
    "expired": "history",
    "reconciled": "history",
    "cancelled": "history",
    "closed": "history",
    "new": "open",
    "open": "open",
    "investigated": "open",
    "rejected": "open",
}
FIELDS = {
    "client",
    "site",
    "platform",
    "state",
    "bucket",
    "engineer",
    "q",
    "limit",
    "offset",
}


def validate_filters(filters: dict) -> dict:
    if not isinstance(filters, dict) or set(filters) - FIELDS:
        raise ValueError("Unsupported operations filters")
    result = {"limit": 25, "offset": 0, **filters}
    for name, value in result.items():
        if name in ("limit", "offset"):
            maximum = 200 if name == "limit" else 1_000_000
            minimum = 1 if name == "limit" else 0
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(
                    f"Invalid {name}: expected integer {minimum}..{maximum}"
                )
        elif (
            not isinstance(value, str)
            or len(value) > (240 if name == "q" else 64)
            or "\x00" in value
        ):
            raise ValueError(f"Invalid {name}")
    if result.get("platform") not in (None, "", "windows", "macos", "linux"):
        raise ValueError("Invalid platform")
    if result.get("bucket") not in (None, "", *BUCKETS):
        raise ValueError("Invalid bucket")
    return result


def parse_query(query: str) -> dict:
    """Decode a bounded singular filter query; preserve blanks for validation."""
    values = parse_qs(
        query, keep_blank_values=True, strict_parsing=True, max_num_fields=20
    )
    filters = {}
    for name, entries in values.items():
        if name not in FIELDS or len(entries) != 1:
            raise ValueError("Unsupported or repeated operations filter")
        value = entries[0]
        if name in ("limit", "offset"):
            if not re.fullmatch(r"[0-9]{1,7}", value):
                raise ValueError(f"Invalid {name}")
            value = int(value)
        filters[name] = value
    return validate_filters(filters)


def select_permitted(
    config: FleetConfig, grant: str | set, site: str = "", hosts: list | None = None
) -> list:
    """Shared device selection for the existing service and the isolated read model."""
    if grant != "*" and site and site not in grant:
        raise Denied("Site is outside your permitted scope.")
    selected = [
        h
        for h in config.hosts
        if (not site or h.site == site) and (grant == "*" or h.site in grant)
    ]
    if hosts is not None:
        if (
            not isinstance(hosts, list)
            or not hosts
            or any(not isinstance(n, str) for n in hosts)
            or len(set(hosts)) != len(hosts)
        ):
            raise ValueError("Select a nonempty unique host list.")
        by_name = {h.name: h for h in selected}
        if any(n not in by_name for n in hosts):
            raise Denied(
                "A selected device is not approved in the current client/site scope."
            )
        selected = [by_name[n] for n in hosts]
    return selected


def _grant(actor: dict, client: str) -> str | set:
    if actor["role"] == "owner":
        return "*"
    grant = actor["scopes"].get(client)
    if grant == "*":
        return "*"
    return set(grant) if isinstance(grant, list) else set()


def _sources(service, actor: dict, client_filter: str) -> tuple[dict, list]:
    """Isolate source failures; use cached identity only to label a broken source.

    WHY: load_registry validates all files together, so it cannot serve partial reads.
    Live config and grants, never the identity cache, decide visible device scope.
    """
    registry = read_json(service.directory / "workspace.json")
    if registry.get("schema") != 1 or not isinstance(registry.get("clients"), list):
        raise ValueError("Invalid workspace catalog")
    known = {entry["config"]: client for client, entry in service.registry.items()}
    sources, partial, duplicates = {}, set(), set()
    for entry in registry["clients"]:
        client = known.get(entry.get("config"))
        try:
            identity = read_json(entry["config"]).get("client")
            if not isinstance(identity, str) or not identity:
                raise ValueError("Invalid client identity")
            client = identity
            if (client_filter and client != client_filter) or not _grant(actor, client):
                continue
            config = FleetConfig.load(entry["config"], require_known_hosts=False)
            if config.client != client:
                raise ValueError("Source identity changed during validation")
            if client in sources or client in partial:
                duplicates.add(client)
            sources[client] = (entry, config)
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            if (
                client
                and _grant(actor, client)
                and (not client_filter or client == client_filter)
            ):
                partial.add(client)
            elif not client and actor["role"] == "owner" and not client_filter:
                partial.add(None)
    for client in partial | duplicates:
        sources.pop(client, None)
    partial |= duplicates
    return sources, [
        {"client": c, "reason": "Source unavailable"}
        for c in sorted(partial, key=lambda c: c or "")
    ]


def _timestamp(value: str | None) -> float:
    if not value:
        return float("-inf")
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.timestamp() if parsed.tzinfo else float("-inf")
    except (ValueError, TypeError, OverflowError):
        return float("-inf")


def _matches(row: dict, filters: dict) -> bool:
    for name in ("platform", "state", "bucket"):
        if filters.get(name) and row[name] != filters[name]:
            return False
    engineer = filters.get("engineer")
    if engineer and (row["engineer_id"] or "unassigned") != engineer:
        return False
    haystack = " ".join(
        str(row.get(n) or "")
        for n in ("client", "client_name", "site", "host", "symptom", "engineer_name")
    )
    return not filters.get("q") or filters["q"].casefold() in haystack.casefold()


def operations(service, actor: dict, filters: dict) -> dict:
    filters = validate_filters(filters)
    actor = service.store.operator(actor["id"])
    service.store.require(actor, "read")
    requested = filters.get("client", "")
    if requested and not _grant(actor, requested):
        raise Denied("Client is outside your permitted scope.")
    site = filters.get("site", "")
    if (
        requested
        and site
        and _grant(actor, requested) != "*"
        and site not in _grant(actor, requested)
    ):
        raise Denied("Site is outside your permitted scope.")
    sources, partial = _sources(service, actor, requested)
    if requested and requested not in sources and not partial:
        raise FileNotFoundError("Client not found")
    rows, upcoming = [], []
    for client, (entry, config) in sources.items():
        grant = _grant(actor, client)
        if site and grant != "*" and site not in grant:
            continue
        hosts = {h.name: h for h in select_permitted(config, grant, site)}
        permitted_sites = {h.site for h in hosts.values()}
        client_rows, schedules = [], []
        try:
            for case in service.store.iter_records("support", client, permitted_sites):
                host = hosts.get(case.get("host"))
                if host is None or case["site"] != host.site:
                    continue
                state = case["state"]
                row = {
                    "id": case["id"],
                    "kind": "support",
                    "client": client,
                    "client_name": str(entry.get("name") or client)[:120],
                    "site": host.site,
                    "host": host.name,
                    "platform": host.platform,
                    "symptom": str(case["symptom"])[:240],
                    "state": state,
                    "revision": case["revision"],
                    "updated_at": case.get("updated_at"),
                    "engineer_id": case.get("assigned_engineer_id"),
                    "engineer_name": case.get("assigned_engineer_name"),
                    "simulation": config.simulation,
                    "bucket": STATE_BUCKET.get(state, "attention"),
                }
                if _matches(row, filters):
                    client_rows.append(row)
            for kind in ("investigation", "repair_plan"):
                for record in service.store.iter_records(
                    kind, client, permitted_sites | {""}
                ):
                    targets = record.get("targets", [])
                    if not targets or any(
                        t.get("name") not in hosts
                        or t.get("site") != hosts[t["name"]].site
                        for t in targets
                    ):
                        continue
                    state = record["state"]
                    row = {
                        "id": record["id"],
                        "client": client,
                        "client_name": str(entry.get("name") or client)[:120],
                        "site": targets[0]["site"]
                        if len({t["site"] for t in targets}) == 1
                        else "Multiple sites",
                        "host": targets[0]["name"]
                        if len(targets) == 1
                        else str(len(targets)) + " devices",
                        "hosts": [t["name"] for t in targets],
                        "platform": targets[0]["platform"]
                        if len({t["platform"] for t in targets}) == 1
                        else "mixed",
                        "symptom": (
                            record.get("plan", {}).get("title")
                            or "Target confirmation / investigation"
                        )[:240],
                        "state": state,
                        "revision": record["revision"],
                        "updated_at": record.get("updated_at"),
                        "engineer_id": None,
                        "engineer_name": None,
                        "simulation": config.simulation,
                        "bucket": STATE_BUCKET.get(state, "attention"),
                        "kind": kind,
                    }
                    if filters.get("platform") and not any(
                        t["platform"] == filters["platform"] for t in targets
                    ):
                        continue
                    other_filters = {
                        k: v for k, v in filters.items() if k != "platform"
                    }
                    if _matches(row, other_filters):
                        client_rows.append(row)
            for schedule in service.store.iter_records(
                "schedule", client, permitted_sites | {""}
            ):
                names = schedule.get("hosts", [])
                if (
                    not schedule.get("enabled")
                    or not names
                    or any(n not in hosts for n in names)
                ):
                    continue
                if filters.get("platform") and any(
                    hosts[n].platform != filters["platform"] for n in names
                ):
                    continue
                schedules.append(
                    {
                        "id": schedule["id"],
                        "client": client,
                        "client_name": str(entry.get("name") or client)[:120],
                        "site": schedule["site"],
                        "hosts": names,
                        "name": str(schedule.get("name", "Collection"))[:100],
                        "kind": "collection",
                        "operation": schedule.get("operation"),
                        "next_run": schedule.get("next_run"),
                        "simulation": config.simulation,
                    }
                )
        except (OSError, ValueError, TypeError, KeyError):
            partial.append({"client": client, "reason": "Source unavailable"})
            continue
        rows.extend(client_rows)
        upcoming.extend(schedules)
    rows.sort(
        key=lambda r: (
            BUCKETS.index(r["bucket"]),
            -_timestamp(r["updated_at"]),
            r["client"],
            r["id"],
        )
    )
    upcoming.sort(key=lambda r: (_timestamp(r["next_run"]), r["client"], r["id"]))
    offset, limit = filters["offset"], filters["limit"]
    counts = {bucket: sum(r["bucket"] == bucket for r in rows) for bucket in BUCKETS}
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "counts_by_bucket": counts,
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "partial_sources": partial,
        "upcoming": upcoming[:25],
        "upcoming_total": len(upcoming),
    }


def devices(service, actor: dict, filters: dict) -> dict:
    """Scope-filtered device search; last observation is never a live heartbeat."""
    from remote_fleet_agent.workspace import EvidenceWorkspace

    filters = validate_filters(filters)
    actor = service.store.operator(actor["id"])
    service.store.require(actor, "read")
    requested, site = filters.get("client", ""), filters.get("site", "")
    if requested and not _grant(actor, requested):
        raise Denied("Client is outside your permitted scope.")
    sources, partial = _sources(service, actor, requested)
    if requested and requested not in sources and not partial:
        raise FileNotFoundError("Client not found")
    rows = []
    for client, (entry, config) in sources.items():
        grant = _grant(actor, client)
        if site and grant != "*" and site not in grant:
            if requested:
                raise Denied("Site is outside your permitted scope.")
            continue
        observed = {}
        try:
            ws = EvidenceWorkspace(config, entry["jobs"])
            observed = {
                h["name"]: h.get("last_collected_at") for h in ws.snapshot()["hosts"]
            }
        except (OSError, ValueError, KeyError, TypeError):
            partial.append({"client": client, "reason": "Evidence unavailable"})
        for host in select_permitted(config, grant, site):
            row = {
                "client": client,
                "client_name": str(entry.get("name") or client)[:120],
                "name": host.name,
                "host": host.name,
                "site": host.site,
                "platform": host.platform,
                "last_observed": observed.get(host.name),
                "simulation": config.simulation,
                "target": host.target,
                "port": host.port,
            }
            if filters.get("platform") and row["platform"] != filters["platform"]:
                continue
            if (
                filters.get("q")
                and filters["q"].casefold()
                not in " ".join(str(v) for v in row.values()).casefold()
            ):
                continue
            rows.append(row)
    rows.sort(key=lambda r: (r["client"], r["site"], r["name"]))
    offset, limit = filters["offset"], filters["limit"]
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "partial_sources": partial,
    }
