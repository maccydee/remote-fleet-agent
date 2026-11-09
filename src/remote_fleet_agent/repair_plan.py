"""Strict versioned executable plans and declared, non-evaluated predicates."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from remote_fleet_agent.support import required

TOP = {
    "schema",
    "title",
    "rationale",
    "uncertainty",
    "impact",
    "targets",
    "window",
    "recovery_window",
    "disruption",
    "trial",
    "batch_size",
    "pause_after_trial",
    "stop_after_failures",
    "steps",
    "target_context",
    "case_id",
}
STEP = {
    "id",
    "kind",
    "targets",
    "platform",
    "script",
    "timeout_seconds",
    "when",
    "packages",
}


def plan_digest(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    if len(encoded) > 1_048_576:
        raise ValueError("plan exceeds 1 MiB")
    return hashlib.sha256(encoded).hexdigest()


def parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError("Timezone required")
        return result.astimezone(UTC)
    except (ValueError, TypeError):
        raise ValueError("Use an ISO date/time with explicit timezone offset") from None


def validate_window(window: dict) -> dict:
    if not isinstance(window, dict) or set(window) != {"start", "end", "timezone"}:
        raise ValueError("window requires start, end and timezone")
    start, end = parse_time(window["start"]), parse_time(window["end"])
    try:
        ZoneInfo(window["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError("window.timezone must be a valid IANA zone") from None
    if start >= end:
        raise ValueError("window end must follow start")
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": window["timezone"],
    }


def evaluate_when(predicate: dict | None, results: dict) -> bool | None:
    if predicate is None:
        return True
    if not isinstance(predicate, dict) or set(predicate) != {"step", "field", "equals"}:
        raise ValueError("when requires step, field and equals")
    field, expected = predicate["field"], predicate["equals"]
    expected_type = {"passed": bool, "exit_code": int}.get(field)
    if expected_type is None or type(expected) is not expected_type:
        raise ValueError("when has invalid field or equals type")
    result = results.get(predicate["step"])
    if (
        result is None
        or result.get("outcome") != "known"
        or result.get("truncated") is True
    ):
        return None
    observed = result.get(field)
    if type(observed) is not type(expected):
        return None
    return observed == expected


def canonical_plan(body: dict, contexts: list[dict] | None = None) -> dict:
    if not isinstance(body, dict) or set(body) - TOP:
        raise ValueError("Unknown plan field")
    plan = copy.deepcopy(body)
    if type(plan.get("schema")) is not int or plan["schema"] != 1:
        raise ValueError("schema must be 1")
    for key in ("title", "rationale", "uncertainty", "impact"):
        plan[key] = required(plan.get(key), key)
    names = plan.get("targets")
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= 100
        or any(not isinstance(n, str) or not n for n in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("targets must be 1..100 unique device names")
    if contexts is not None:
        plan["target_context"] = contexts
    context = {t["name"]: t for t in plan.get("target_context", [])}
    if set(context) != set(names):
        raise ValueError("target_context must exactly match targets")
    plan["targets"] = sorted(names)
    plan["window"] = validate_window(plan.get("window"))
    if "recovery_window" in plan:
        plan["recovery_window"] = validate_window(plan["recovery_window"])
    if plan.get("disruption") not in {"now", "readiness", "maintenance_window"}:
        raise ValueError(
            "Choose now, readiness or maintenance_window disruption policy"
        )
    if plan.get("trial") not in names:
        raise ValueError("trial must name an approved target")
    for key, default, maximum in (
        ("batch_size", 1, 16),
        ("stop_after_failures", 1, 100),
    ):
        plan.setdefault(key, default)
        if type(plan[key]) is not int or not 1 <= plan[key] <= maximum:
            raise ValueError(f"Invalid {key}")
    plan.setdefault("pause_after_trial", False)
    if type(plan["pause_after_trial"]) is not bool:
        raise ValueError("pause_after_trial must be boolean")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
        raise ValueError("steps must contain 1..100 executable steps")
    prior, coverage = {}, set()
    for step in steps:
        if not isinstance(step, dict) or set(step) - STEP:
            raise ValueError("Unknown step field")
        ident = required(step.get("id"), "step.id", 64)
        if ident in prior:
            raise ValueError("Duplicate step id")
        if step.get("kind") not in {"check", "change", "recovery"}:
            raise ValueError("Invalid step kind")
        targets = step.get("targets")
        if (
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(n, str) or n not in names for n in targets)
            or len(set(targets)) != len(targets)
        ):
            raise ValueError("step.targets must name unique selected targets")
        if step.get("platform") not in {"windows", "macos", "linux"} or any(
            context[n]["platform"] != step["platform"] for n in targets
        ):
            raise ValueError("step platform must match every target")
        step["script"] = required(step.get("script"), "step.script", 32768)
        if step["platform"] == "windows" and not step["script"].startswith(
            "$ErrorActionPreference='Stop'\n"
        ):
            step["script"] = required("$ErrorActionPreference='Stop'\n" + step["script"], "step.script", 32768)
        timeout = step.get("timeout_seconds")
        if type(timeout) is not int or not 5 <= timeout <= 300:
            raise ValueError("step.timeout_seconds must be integer 5..300")
        predicate = step.get("when")
        if predicate is not None:
            evaluate_when(predicate, {})
            ref = prior.get(predicate["step"])
            if (
                ref is None
                or ref["kind"] != "check"
                or not set(targets) <= set(ref["targets"])
            ):
                raise ValueError(
                    "when must reference an earlier check covering these targets"
                )
        if step["kind"] == "recovery" and predicate is None:
            raise ValueError("recovery requires an explicit known check condition")
        packages = step.get("packages", [])
        if not isinstance(packages, list):
            raise TypeError("Invalid package declaration")
        for package in packages:
            if not isinstance(package, dict) or set(package) != {
                "name",
                "version",
                "sha256",
                "check_step",
            }:
                raise ValueError(
                    "package requires name, version, sha256 and check_step"
                )
            for key in ("name", "version"):
                required(package[key], "package." + key, 240)
            sha = package["sha256"]
            if (
                not isinstance(sha, str)
                or len(sha) != 64
                or any(c not in "0123456789abcdef" for c in sha)
            ):
                raise ValueError("package.sha256 must be lowercase hex")
            if predicate != {
                "step": package["check_step"],
                "field": "passed",
                "equals": True,
            }:
                raise ValueError(
                    "package change requires a successful reviewed hash check predicate"
                )
        prior[ident] = step
        if step["kind"] != "recovery":
            coverage.update(targets)
    if coverage != set(names):
        raise ValueError("Every target needs executable platform steps")
    plan_digest(plan)
    return plan
