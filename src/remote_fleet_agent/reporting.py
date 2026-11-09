"""Deterministic evidence summaries and offline HTML. No compliance scoring."""

from __future__ import annotations

import base64
import html
import json
from datetime import datetime
from importlib.resources import files

from remote_fleet_agent.maintenance import ACTIONS
from remote_fleet_agent.tools.diagnostics import QUERIES as DIAGNOSTICS
from remote_fleet_agent.tools.inventory import QUERIES as INVENTORY
from remote_fleet_agent.tools.readiness import QUERIES as READINESS


def brand_image():
    """Self-contained branding for offline exports, without external requests."""
    logo = files("remote_fleet_agent").joinpath("app_assets", "logo.svg").read_bytes()
    data = base64.b64encode(logo).decode("ascii")
    return f'<img class="export-brand" src="data:image/svg+xml;base64,{data}" alt="Remote Fleet" width="178" height="38">'


CATALOG = {
    "firewall_off": (
        "attention",
        "A firewall profile reports disabled",
        "Review the profile and its applicable policy before making changes.",
    ),
    "encryption_off": (
        "attention",
        "A volume reports disk encryption protection off",
        "Confirm whether protection is suspended or encryption is absent, and review the intended policy.",
    ),
    "defender_review": (
        "review",
        "Defender protection needs review",
        "Establish the active antivirus product and its health; Defender may be passive with a different product.",
    ),
    "low_disk": (
        "review",
        "A disk has less than 10% free space",
        "Review capacity and an agreed cleanup plan; do not delete user files automatically.",
    ),
    "reboot_signal": (
        "review",
        "A selected reboot indicator is present",
        "Review servicing state and agree a reboot window; these indicators are not exhaustive.",
    ),
    "security_gap": (
        "unknown",
        "Required security evidence is missing or incomplete",
        "Collect or manually verify the missing evidence; unknown results are not passes.",
    ),
}


def findings_for(host):
    sections = host["sections"]
    findings = []

    def add(code, detail):
        severity, title, action = CATALOG[code]
        findings.append(
            {
                "code": code,
                "severity": severity,
                "title": title,
                "recommendation": action,
                "detail": detail,
            }
        )

    def items(key):
        s = sections.get(key, {})
        return s.get("items", []) if s.get("status") == "collected" else []

    for r in items("inventory.firewall"):
        if r.get("Enabled") is False or r.get("Enabled") in (0, "False"):
            add("firewall_off", str(r.get("Name", "profile")))
    for r in items("inventory.encryption"):
        if r.get("ProtectionStatus") in ("Off", 0, "0"):
            add("encryption_off", str(r.get("MountPoint", "volume")))
    for r in items("inventory.defender"):
        if any(
            r.get(k) is False
            for k in (
                "AMServiceEnabled",
                "AntivirusEnabled",
                "RealTimeProtectionEnabled",
            )
        ):
            add("defender_review", str(r.get("AMRunningMode", "Not established")))
    for r in items("inventory.disks"):
        size, free = r.get("Size"), r.get("FreeSpace")
        if (
            type(size) in (int, float)
            and type(free) in (int, float)
            and size > 0
            and free / size < 0.1
        ):
            add("low_disk", str(r.get("DeviceID", "disk")))
    for r in items("diagnostics.reboot_pending"):
        if any(
            r.get(k) is True
            for k in (
                "componentServicing",
                "windowsUpdate",
                "fileRenamePending",
                "required",
            )
        ):
            add("reboot_signal", "A recorded reboot indicator is true.")
    expected = (
        ("inventory.firewall", "inventory.encryption", "inventory.defender")
        if host.get("platform", "windows") == "windows"
        else (
            "inventory.firewall",
            "inventory.encryption",
            "inventory.antivirus_products",
        )
    )
    gaps = [
        key
        for key in expected
        if sections.get(key, {}).get("status") != "collected"
        or sections.get(key, {}).get("truncated")
        or not sections.get(key, {}).get("items")
    ]
    if gaps:
        add("security_gap", ", ".join(gaps))
    return findings


def safe_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.isoformat() if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def shareable_snapshot(data):
    """Allowlist export: exclude raw evidence, notes, references, account/host identities."""
    state_values = {
        "complete",
        "partial",
        "failed",
        "queued",
        "running",
        "cancelled",
        "interrupted",
        "unknown",
        "not_collected",
        "preview",
        "preview_failed",
        "not_run",
        "awaiting_continuation",
        "halted",
        "blocked",
        "expired",
        "canary_running",
        "ready",
        "needs_attention",
        "scheduled",
        "overdue",
        "expires_soon",
        "completed",
        "rolled_back",
        "not_managed",
    }

    def state(value):
        return value if value in state_values else "unknown"

    host_map = {r["name"]: f"Host {i + 1:03}" for i, r in enumerate(data["hosts"])}
    job_map = {r["id"]: f"Collection {i + 1:03}" for i, r in enumerate(data["jobs"])}
    from remote_fleet_agent.tools.unix import CATALOG as UNIX_CATALOG

    valid_sections = {
        op + "." + n
        for op, queries in (
            ("inventory", INVENTORY),
            ("diagnostics", DIAGNOSTICS),
            ("readiness", READINESS),
        )
        for n in queries
    }
    valid_sections.update(
        op + "." + name
        for catalog in UNIX_CATALOG.values()
        for op, sections in catalog.items()
        for name in sections
    )
    result = {
        "schema": "fleet-workspace-v1",
        "client": "Engagement",
        "simulation": data.get("simulation") is True,
        "generated_at": safe_time(data["generated_at"]),
        "shareable": True,
        "note": "Redacted saved evidence summary. No raw evidence, identities, manual notes or references. Not a compliance verdict.",
        "hosts": [],
        "jobs": [],
        "manual": [],
        "batches": [],
        "storage_errors": [
            {"error": "A source record could not be read"}
            for _ in data["storage_errors"]
        ],
    }
    for row in data["hosts"]:
        readiness = row.get("readiness")
        result["hosts"].append(
            {
                "name": host_map[row["name"]],
                "target": "Redacted",
                "platform": row.get("platform", "windows")
                if row.get("platform", "windows") in ("windows", "linux", "macos")
                else "unknown",
                "simulation": row.get("simulation") is True,
                "groups": [],
                "approved": row["approved"] is True,
                "collection_status": state(row["collection_status"]),
                "last_collected_at": safe_time(row["last_collected_at"]),
                "sections": {
                    k: {
                        "status": "collected"
                        if s.get("status") == "collected"
                        else "unknown",
                        "total": s.get("total")
                        if type(s.get("total")) is int
                        else None,
                        "truncated": s.get("truncated") is True,
                    }
                    for k, s in row["sections"].items()
                    if k in valid_sections
                },
                "sources": {
                    k: {
                        "job_id": job_map.get(v.get("job_id"), "Historical collection"),
                        "collected_at": safe_time(v.get("collected_at")),
                    }
                    for k, v in row["sources"].items()
                    if k in valid_sections
                },
                "readiness": {
                    "state": state(readiness.get("state")),
                    "connection": "connected"
                    if readiness.get("connection") == "connected"
                    else "simulated"
                    if readiness.get("connection") == "simulated"
                    else "unknown",
                    "checked_at": safe_time(readiness.get("checked_at")),
                    "cleanup": state(readiness.get("cleanup")),
                    "notes": [],
                }
                if readiness
                else None,
                "findings": [
                    {
                        "code": f["code"],
                        "severity": CATALOG[f["code"]][0],
                        "title": CATALOG[f["code"]][1],
                        "recommendation": CATALOG[f["code"]][2],
                    }
                    for f in row["findings"]
                    if f["code"] in CATALOG
                ],
            }
        )
    for job in data["jobs"]:
        result["jobs"].append(
            {
                "id": job_map[job["id"]],
                "operation": job["operation"]
                if job["operation"] in ("inventory", "diagnostics", "readiness")
                else "other",
                "status": state(job["status"]),
                "created_at": safe_time(job["created_at"]),
                "finished_at": safe_time(job["finished_at"]),
                "hosts": {
                    host_map.get(n, "Historical host"): state(s)
                    for n, s in job["hosts"].items()
                },
            }
        )
    from remote_fleet_agent.workspace import CATEGORIES, CONTROLS, MANUAL_STATES

    for i, record in enumerate(data["manual"]):
        result["manual"].append(
            {
                "id": f"Manual {i + 1:03}",
                "asset": f"Asset {i + 1:03}",
                "category": record["category"]
                if record["category"] in CATEGORIES
                else "other",
                "control": record["control"]
                if record["control"] in CONTROLS
                else "unknown",
                "status": record["status"]
                if record["status"] in MANUAL_STATES
                else "unknown",
                "note": "Omitted from shareable export",
                "reference": "",
                "reviewer": "Redacted",
                "revision": record["revision"],
                "updated_at": safe_time(record["updated_at"]),
            }
        )
    for i, batch in enumerate(data["batches"]):
        result["batches"].append(
            {
                "id": f"Batch {i + 1:03}",
                "action": batch["action"] if batch["action"] in ACTIONS else "unknown",
                "status": state(batch["status"]),
                "canary": host_map.get(batch["canary"], "Historical host"),
                "created_at": safe_time(batch["created_at"]),
                "hosts": {
                    host_map.get(n, "Historical host"): {
                        "status": state(e.get("status"))
                    }
                    for n, e in batch["hosts"].items()
                },
            }
        )
    return result


CSS = """
:root{--bg:#fafafa;--ink:#202126;--muted:#71717c;--accent:#6458d8;--line:#e6e6eb;--paper:#fff;--soft:#f3f2fc;--sidebar:#f3f3f6}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button,input,select,textarea{font:inherit;color:inherit}button{cursor:pointer}a{color:var(--accent)}h1,h2,h3,p{margin-top:0}h1{font-size:28px;letter-spacing:-.04em;font-weight:650;margin-bottom:6px}h2{font-size:15px;letter-spacing:-.015em;font-weight:600;margin:0}h3{font-size:14px;line-height:1.5}button,select,input,textarea{border:1px solid #dcdce3;border-radius:7px;padding:8px 11px;background:var(--paper)}button{font-size:13px;font-weight:500;transition:background .15s}button:hover{background:#f0eff8}button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible,summary:focus-visible{outline:3px solid #b4abfa;outline-offset:3px}button.primary{background:var(--accent);border-color:var(--accent);color:white}button.primary:hover{background:#5145c0}.subtle{color:var(--muted);font-size:12px}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.pill,.badge{display:inline-flex;gap:6px;align-items:center;white-space:nowrap;font-size:11px;font-weight:500;padding:3px 8px;border:1px solid var(--line);border-radius:5px;background:#f5f5f7;color:#61616e}.badge:before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;flex:none}.attention,.failed,.halted,.overdue{color:#b4413f;background:#fff1f0;border-color:#f6d8d6}.review,.open,.needs_attention,.unknown,.partial,.not_checked,.not_collected{color:#966420;background:#fff8e9;border-color:#f2e4c5}.complete,.ready,.verified,.rolled_back{color:#288060;background:#edf8f1;border-color:#d8ebdf}.running,.queued,.preview,.awaiting_continuation{color:#6458c5;background:#f0edff;border-color:#e1dcfa}header{padding:32px max(24px,5vw);border-bottom:1px solid var(--line);background:white}header small{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em}main{max-width:1440px;padding:28px 4vw 60px;margin:auto}.notice{padding:12px 15px;border:1px solid var(--line);background:#f6f6f9;border-radius:7px;margin-bottom:22px;color:#62626e;font-size:12px}.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:0;margin-bottom:28px;border:1px solid var(--line);border-radius:9px;background:white;overflow:hidden}.metric{padding:20px 22px;position:relative}.metric+.metric{border-left:1px solid var(--line)}.metric strong{display:block;font-size:30px;font-weight:600;letter-spacing:-.05em;line-height:1.25;margin-bottom:7px;font-variant-numeric:tabular-nums}.metric span{font-size:12px;color:var(--muted)}.metric .metric-hint{display:block;font-size:11px;color:#9b9ba5;margin-top:9px}.panel{border:1px solid var(--line);border-radius:9px;background:white;overflow:hidden;margin-bottom:22px}.panel-head{padding:17px 20px;display:flex;gap:14px;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);flex-wrap:wrap}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;text-align:left;font-size:12px}th,td{padding:14px 18px;border-bottom:1px solid #eeeeF2;vertical-align:top}th{background:#fcfcfd;color:var(--muted);font-size:11px;font-weight:500;white-space:nowrap}tr:last-child td{border-bottom:0}tbody tr:hover{background:#fcfbff}td strong{font-weight:550}td{max-width:400px;overflow-wrap:anywhere}.filters{display:flex;gap:8px;flex-wrap:wrap}.filters input{width:200px}.filters input,.filters select{font-size:12px;padding:7px 10px}.empty{padding:32px 22px;color:var(--muted);font-size:13px}.finding{padding:20px;border-bottom:1px solid var(--line)}.finding:last-child{border:0}.finding h3{margin-bottom:8px;font-weight:550}.finding p{margin:7px 0 0;font-size:13px;color:#666674}.form{padding:22px;display:grid;grid-template-columns:1fr 1fr;gap:16px}.form label{display:grid;gap:6px;font-size:12px;font-weight:500}.form .toolbar{font-weight:400}.wide{grid-column:1/-1}textarea{min-height:100px;resize:vertical}.form input,.form select,.form textarea{width:100%}details{margin-top:9px}summary{cursor:pointer;font-size:12px;color:var(--accent)}pre{white-space:pre-wrap;word-break:break-word;background:#f6f6f9;padding:14px;border-radius:7px;font-size:11px;max-height:420px;overflow:auto}footer{padding-top:20px;border-top:1px solid var(--line);color:#858590;font-size:11px}.hide,[hidden]{display:none!important}
.dashboard{display:grid;grid-template-columns:224px minmax(0,1fr);min-height:100vh}.sidebar{background:var(--sidebar);border-right:1px solid #e1e1e7;position:sticky;top:0;height:100vh;padding:24px 14px;display:flex;flex-direction:column}.brand{display:flex;align-items:center;gap:10px;padding:0 10px;font-size:15px;font-weight:650;letter-spacing:-.035em}.export-brand{display:block;max-width:100%;height:auto}.brand-mark{width:27px;height:27px;background:#272632;color:white;border-radius:7px;display:grid;place-items:center}.icon{width:17px;height:17px;display:inline-block;vertical-align:middle;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}.workspace-label{margin:29px 9px 22px;padding:12px;border:1px solid #dddde5;border-radius:7px;background:#fcfcfd}.workspace-label small{display:block;color:var(--muted);font-size:10px;letter-spacing:.08em;text-transform:uppercase}.workspace-label strong{display:block;margin-top:4px;font-size:13px;font-weight:550;overflow-wrap:anywhere}.nav-label{color:#94949e;font-size:10px;letter-spacing:.09em;text-transform:uppercase;padding:0 12px;margin-bottom:9px}#tabs{display:flex;flex-direction:column;gap:4px;margin:0}#tabs button{display:flex;align-items:center;gap:10px;text-align:left;background:transparent;border:1px solid transparent;color:#71717e;padding:9px 11px;font-size:12px;font-weight:450}#tabs button:hover{background:#eaeaf0;color:var(--ink)}#tabs button.active{background:#e6e4f4;border-color:#dedaf2;color:#5549b8;font-weight:550}.nav-count{margin-left:auto;font-size:10px;opacity:.75;font-variant-numeric:tabular-nums}.sidebar-foot{margin-top:auto;padding:28px 12px 0;color:#94949f;font-size:11px}.saved-dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#a6a2b8;margin-right:7px}.workspace-main{min-width:0}.dashboard .topbar{padding:15px 30px;height:58px;display:flex;justify-content:space-between;gap:12px;align-items:center;border-bottom:1px solid var(--line);background:#fff;font-size:12px;color:var(--muted)}.breadcrumb{display:flex;align-items:center;gap:12px}.breadcrumb b{color:#575762;font-weight:500}.breadcrumb span{color:#c5c5ce}.dashboard main{padding:31px 32px 50px;max-width:1540px}.page-head{display:flex;align-items:flex-start;justify-content:space-between;gap:22px;margin-bottom:25px}.page-head p{font-size:13px;color:var(--muted);margin-bottom:0}.page-head button{white-space:nowrap;flex-shrink:0;display:flex;align-items:center;gap:7px;margin-top:4px}.snapshot-note{display:flex;gap:9px;align-items:flex-start;border:0;background:transparent;padding:0;margin-bottom:25px;font-size:11px;color:#888894}.snapshot-note details{margin:0}.snapshot-note summary{color:#7c7c89;font-size:11px}.snapshot-note p{margin:8px 0;max-width:800px}.overview-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(270px,1fr);gap:22px}.overview-grid .panel{margin-bottom:22px}.queue-row{display:flex;gap:13px;padding:16px 20px;border-bottom:1px solid #efeff3;align-items:flex-start}.queue-row:last-child{border-bottom:0}.queue-row .queue-icon{width:29px;height:29px;border:1px solid #eee6d8;border-radius:7px;background:#fffbf3;color:#ad8b51;display:grid;place-items:center;flex:none}.queue-row>div{min-width:0}.queue-row p{overflow-wrap:anywhere}.queue-row h3{font-size:12px;font-weight:550;margin:0 0 3px}.queue-row p{font-size:11px;color:var(--muted);margin:0}.queue-row .badge{margin-left:auto}.text-button{padding:0;border:0;background:none;color:var(--accent);font-size:11px}.coverage{padding:22px;display:flex;align-items:center;gap:22px}.coverage-ring{width:92px;height:92px;flex:none;position:relative;display:grid;place-items:center}.coverage-ring svg{position:absolute;inset:0;transform:rotate(-90deg)}.coverage-ring strong{font-size:22px;font-weight:600;letter-spacing:-.04em}.coverage p{font-size:12px;color:var(--muted);margin:4px 0}.coverage strong{font-weight:550}.coverage-legend{border-top:1px solid var(--line);display:grid;grid-template-columns:1fr 1fr;padding:14px 22px;gap:12px;font-size:11px;color:var(--muted)}.coverage-legend b{display:block;color:var(--ink);font-size:16px;font-weight:500}.activity-row{padding:13px 20px;border-bottom:1px solid #efeff3;font-size:12px;display:flex;justify-content:space-between;gap:12px;align-items:center}.activity-row:last-child{border:0}.activity-row strong{font-weight:500;text-transform:capitalize}.activity-row small{display:block;color:var(--muted);font-size:10px;margin-top:3px}.manual-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;padding:18px}.evidence-card{border:1px solid var(--line);border-radius:8px;padding:17px 18px;min-width:0}.evidence-card .card-top{display:flex;justify-content:space-between;gap:10px;margin-bottom:13px}.category{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#91919c}.evidence-card h3{font-size:14px;font-weight:550;margin:0 0 8px}.evidence-card p{font-size:12px;color:#71717d;margin:0;overflow-wrap:anywhere}.evidence-card .reference{font-size:11px;margin-top:8px;color:#9595a0}.card-foot{border-top:1px solid #efeff3;padding-top:12px;margin-top:18px;display:flex;align-items:center;justify-content:space-between;gap:10px;color:#91919c;font-size:10px}.card-foot button{padding:4px 9px;font-size:11px}.manual-editor{margin-top:0}.manual-editor>summary{padding:18px 20px;font-size:13px;font-weight:550;list-style:none;color:var(--ink)}.manual-editor>summary:before{content:"+";display:inline-block;margin-right:10px;color:var(--accent);font-size:18px;line-height:1}.manual-editor[open]>summary{border-bottom:1px solid var(--line)}.manual-editor .panel{border:0;margin:0}.manual-editor .panel-head{padding:14px 22px;border-bottom:0}.manual-editor .form{padding-top:4px}.manual-editor .panel-head h2{display:none}
@media(max-width:1100px){.dashboard{grid-template-columns:190px minmax(0,1fr)}.dashboard main{padding:26px 24px}.overview-grid{grid-template-columns:1fr}.metric{padding:17px}.metric span{font-size:11px}.manual-grid{grid-template-columns:1fr}}@media(max-width:760px){.dashboard{display:block}.sidebar{position:relative;height:auto;padding:15px 16px 0;border-right:0;border-bottom:1px solid var(--line)}.brand{padding:0;font-size:14px}.workspace-label,.nav-label,.sidebar-foot{display:none}#tabs{flex-direction:row;overflow:auto;margin:14px -16px 0;padding:0 12px;gap:3px;scrollbar-width:none}#tabs button{flex:none;padding:11px 9px;border-radius:6px 6px 0 0;font-size:11px}#tabs .icon{display:none}.nav-count{font-size:9px}.dashboard .topbar{height:44px;padding:10px 18px;font-size:10px}.dashboard .topbar .pill{font-size:9px}.dashboard main{padding:23px 18px 35px}.page-head{margin-bottom:20px;gap:10px}.page-head h1{font-size:25px}.page-head p{font-size:12px}.page-head button{padding:7px 9px;font-size:11px}.metrics{grid-template-columns:1fr 1fr;margin-bottom:22px}.metric{padding:16px}.metric strong{font-size:27px}.metric:nth-child(3){border-left:0}.metric:nth-child(n+3){border-top:1px solid var(--line)}.metric .metric-hint{font-size:10px;margin-top:5px}.panel-head{padding:15px 16px}.panel-head h2{font-size:13px}.form{grid-template-columns:1fr;padding:16px}.filters{width:100%}.filters input{flex:1;min-width:120px;width:100%}.filters select{flex:1;min-width:90px}.manual-grid{padding:12px;gap:12px}.evidence-card{padding:15px}.coverage{padding:20px}.queue-row{padding:14px 16px;gap:10px}.queue-row .badge{font-size:9px}.snapshot-note{margin-bottom:20px}.toolbar .subtle{font-size:11px}main{padding:20px}.metrics:not(.dashboard .metrics){grid-template-columns:1fr 1fr}header{padding:24px}.form .toolbar{align-items:stretch}.form .toolbar button{width:100%}}@media print{.dashboard{display:block}.sidebar,.topbar,.page-head button,.snapshot-note,nav,.no-print,.manual-editor{display:none!important}.dashboard main,main{max-width:none;padding:20px 0;background:white}body{background:white}header{padding:0 0 16px}.metrics{grid-template-columns:repeat(4,1fr)}.overview-grid{display:block}.panel{break-inside:auto}.finding,.metric,.evidence-card,tr{break-inside:avoid}pre{max-height:none}.manual-grid{grid-template-columns:1fr 1fr}}
"""
CSS += """
.data-grid{min-width:0}.grid-toolbar{display:flex;gap:12px;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--line);flex-wrap:wrap}.grid-search{font-size:12px;min-width:190px;width:250px}.grid-actions{display:flex;align-items:center;gap:9px;margin-left:auto}.grid-actions button{font-size:11px;padding:7px 10px}.column-selector{margin:0;position:relative}.column-selector>summary{list-style:none;font-size:11px;color:var(--ink);border:1px solid #dcdce3;border-radius:6px;padding:7px 10px;background:#fff}.column-selector>summary:after{content:'⌄';margin-left:12px;color:var(--muted)}.column-menu{position:absolute;right:0;top:38px;z-index:5;padding:15px;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 28px #26223b18;width:250px;max-height:300px;overflow:auto}.column-menu>span{display:block;margin-bottom:10px}.column-menu label{display:flex;align-items:center;gap:9px;padding:6px 0;font-size:12px}.column-menu input{accent-color:var(--accent);margin:0;width:14px;height:14px}.panel:has(.data-grid){overflow:visible}.grid-scroll{max-height:620px;border-radius:0 0 8px 8px}.data-grid table{min-width:700px}.data-grid thead{position:sticky;top:0;z-index:1;background:#fcfcfd}.data-grid th{padding:10px 13px}.data-grid td{padding:13px;min-width:100px;max-width:340px}.sort-column{display:flex;align-items:center;justify-content:space-between;gap:14px;background:none;border:0;padding:0;width:100%;white-space:nowrap;text-align:left;color:#666672;font-size:11px}.sort-column span{color:#aaa9b7}.column-filters th{padding-top:0;padding-bottom:11px}.column-filters input{width:100%;min-width:90px;font-size:11px;padding:6px 8px;background:white}.cell-value{display:block;overflow-wrap:anywhere;max-width:320px}.source-job{font:11px/1.5 ui-monospace,SFMono-Regular,monospace;color:#83818f;overflow-wrap:anywhere}.grid-footer{padding:12px 18px;border-top:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:12px;color:var(--muted);font-size:11px;flex-wrap:wrap}.grid-pagination{display:flex;gap:7px;align-items:center}.grid-pagination label{display:flex;gap:7px;align-items:center}.grid-pagination select,.grid-pagination button{font-size:11px;padding:4px 7px}.grid-pagination button:disabled,button:disabled{opacity:.45;cursor:default}.evidence-switch{padding:16px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}.segmented{display:flex;gap:3px;padding:3px;border:1px solid var(--line);background:#f5f4f8;border-radius:7px}.segmented button{font-size:11px;border:0;background:transparent;padding:7px 11px;color:var(--muted)}.segmented button.active{color:#594cc3;background:white;box-shadow:0 1px 3px #25213416}.evidence-note{padding:0 18px 16px;font-size:12px;color:var(--muted);max-width:1000px}.data-grid .open-evidence,.data-grid .show-records{font-size:11px;color:var(--accent);white-space:nowrap;padding:5px 9px}@media(max-width:760px){.grid-toolbar{padding:12px;gap:9px}.grid-search{min-width:120px;flex:1;width:100%}.grid-actions{gap:5px}.grid-actions button,.column-selector>summary{font-size:10px;padding:7px}.grid-footer{padding:12px}.evidence-switch{padding:12px}.evidence-note{padding:0 12px 12px}.grid-scroll{max-height:560px}.column-menu{width:225px}.data-grid table{min-width:650px}}@media print{.grid-toolbar,.column-filters,.grid-pagination,.evidence-switch{display:none!important}.grid-scroll{max-height:none;overflow:visible}.data-grid thead{position:static}.data-grid table{min-width:0}}
"""


def esc(value):
    return html.escape(str(value if value is not None else "Not recorded"), quote=True)


def render_report(data):
    findings = [(h["name"], f) for h in data["hosts"] for f in h["findings"]]
    rows = "".join(
        f"<tr><td>{esc(h['name'])}</td><td>{esc(h['collection_status'])}</td><td>{esc(h['last_collected_at'])}</td><td>{len(h['findings'])}</td></tr>"
        for h in data["hosts"]
    )
    issues = (
        "".join(
            f'<article class="finding"><h3>{esc(name)} · {esc(f["title"])}</h3><p><span class="badge {esc(f["severity"])}">{esc(f["severity"])}</span></p><p>{esc(f.get("detail", ""))}</p><p>{esc(f["recommendation"])}</p></article>'
            for name, f in findings
        )
        or '<div class="empty">No configured review rules matched. Missing checks and manual scope still require review.</div>'
    )
    manual = "".join(
        f'<tr><td>{esc(r["asset"])}</td><td>{esc(r["category"])}</td><td>{esc(r["control"])}</td><td>{esc(r["status"])}</td><td>{esc(r["note"])}<br><span class="subtle">{esc(r.get("reference", ""))}</span></td><td>{esc(r["reviewer"])}</td></tr>'
        for r in data["manual"]
    )
    coverage = "".join(
        f"<tr><td>{esc(h['name'])}</td><td>{esc(name)}</td><td>{esc(s.get('status'))}{' · truncated' if s.get('truncated') else ''}</td><td>{esc(s.get('total'))}</td><td>{esc(h['sources'].get(name, {}).get('collected_at'))}</td></tr>"
        for h in data["hosts"]
        for name, s in h["sections"].items()
    )
    source_warning = (
        f'<div class="notice">{len(data["storage_errors"])} source records could not be read. Coverage may be incomplete; inspect the private store.</div>'
        if data["storage_errors"]
        else ""
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>{esc(data["client"])} · Evidence report</title><style>{CSS}</style></head><body><header>{brand_image()}<small>Fleet evidence / report</small><h1>{esc(data["client"])}</h1><div>{esc(data["generated_at"])} · {"Shareable summary" if data.get("shareable") else "Private evidence report"}</div></header><main><div class="notice">{"SIMULATED / FICTIONAL evidence. " if data.get("simulation") else ""}{esc(data["note"])} Findings are observed conditions for review. Manual verification is operator recorded; this report does not certify Cyber Essentials, GDPR or resilience compliance.</div><div class="metrics"><div class="metric"><strong>{len(data["hosts"])}</strong><span>Recorded hosts</span></div><div class="metric"><strong>{len(findings)}</strong><span>Review observations</span></div><div class="metric"><strong>{len(data["manual"])}</strong><span>Manual evidence records</span></div><div class="metric"><strong>{sum(r["status"] in ("open", "unknown") for r in data["manual"])}</strong><span>Unresolved manual records</span></div></div><p class="no-print"><button onclick="window.print()">Print / save as PDF</button></p>{source_warning}<h2>Host coverage</h2><section class="panel scroll"><table><thead><tr><th>Host</th><th>Latest saved outcome</th><th>Last collected</th><th>Observations</th></tr></thead><tbody>{rows}</tbody></table></section><h2>Findings and recommended actions</h2><section class="panel">{issues}</section><h2>Manual evidence</h2><section class="panel scroll"><table><thead><tr><th>Asset</th><th>Category</th><th>Control</th><th>Operator status</th><th>Notes / reference</th><th>Reviewer</th></tr></thead><tbody>{manual}</tbody></table></section><h2>Collected sections and provenance</h2><section class="panel scroll"><table><thead><tr><th>Host</th><th>Section</th><th>Evidence</th><th>Count</th><th>Collected at</th></tr></thead><tbody>{coverage}</tbody></table></section><footer>Software coverage varies by platform. Installed updates do not establish patch compliance. Antivirus health requires product-specific evidence. Empty or truncated sections are not passes. Manual NAS, M365, network appliance and backup evidence remains essential. Store or share this document according to its privacy mode.</footer></main></body></html>"""


def render_dashboard(data, private=False):
    payload = (
        json.dumps(data)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return (
        """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"><title>Fleet evidence workspace</title><style>"""
        + CSS
        + """</style></head><body class="dashboard"><aside class="sidebar"><div class="brand">"""
        + brand_image()
        + """</div><div class="workspace-label"><small>Engagement</small><strong id="client"></strong></div><div class="nav-label">Workspace</div><nav id="tabs" aria-label="Workspace navigation"></nav><div class="sidebar-foot"><span class="saved-dot"></span>Saved evidence<br><span style="display:block;margin:5px 0 0 13px">Remote Fleet · 0.8</span></div></aside><div class="workspace-main"><div class="topbar"><div class="breadcrumb"><b id="context-client"></b><span>/</span><span id="crumb"></span></div><span class="pill" id="privacy"></span></div><main><div class="page-head"><div><h1 id="page-title"></h1><p id="page-description"></p></div><button id="print" class="no-print">Print view</button></div><div class="snapshot-note"><details><summary id="generated"></summary><p>Saved evidence snapshot. Connectivity is shown only when a readiness check recorded it, with its timestamp. Regenerate this dashboard after new jobs or imports. No endpoint action runs from this page. Findings are observations for review, not a compliance verdict.</p></details></div><div class="metrics" id="metrics"></div><section id="view" aria-label="Evidence workspace"></section><footer>Manual statuses are operator recorded. Unknown evidence is not a pass. Shareable exports omit identities, raw query output, notes and references. Copies downloaded elsewhere are outside managed retention.</footer></main></div><script id="evidence" type="application/json">"""
        + payload
        + """</script><script>
'use strict';
const D=JSON.parse(document.getElementById('evidence').textContent), PRIVATE="""
        + ("true" if private else "false")
        + """;
const $=id=>document.getElementById(id), E=x=>String(x??'Not recorded').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=x=>String(x).replace(/_/g,' ');
const B=x=>`<span class="badge ${E(String(x).replace(/[^a-z_]/g,''))}">${E(label(x))}</span>`;
let tab='Overview', changes=[];const tabs=['Overview','Fleet','Evidence','Findings','Collections','Readiness','Maintenance','Manual evidence'];
const F=s=>{const d=s?new Date(s):null;return d&&!Number.isNaN(d.valueOf())?d.toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short'}):'Not recorded'};
$('client').textContent=D.client;$('context-client').textContent=D.client;$('print').onclick=()=>window.print();$('privacy').textContent=(D.simulation?'SIMULATED · ':'')+(PRIVATE?'Private workspace':'Shareable summary');$('generated').textContent='Snapshot generated '+F(D.generated_at);
const findings=D.hosts.flatMap(h=>h.findings.map(f=>({host:h.name,...f})));
const complete=D.hosts.filter(h=>h.collection_status==='complete').length, unresolved=D.manual.filter(r=>['open','unknown'].includes(r.status));
$('metrics').innerHTML=[[D.hosts.length,'Recorded hosts','In this engagement'],[complete,'Collection complete','Latest saved outcome'],[findings.length,'Review observations','From collected evidence'],[unresolved.length,'Manual follow-ups','Open or unknown records']].map(([n,t,h])=>`<div class="metric"><strong>${n}</strong><span>${E(t)}</span><span class="metric-hint">${E(h)}</span></div>`).join('');
const paths={Overview:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',Fleet:'M3 4h18v12H3z M8 21h8 M12 16v5',Evidence:'M4 4h16v16H4z M4 10h16 M10 4v16',Findings:'M12 3 2 21h20z M12 9v5 M12 17v1',Collections:'M4 5h16v16H4z M8 2v6 M16 2v6 M8 12h8 M8 16h5',Readiness:'M20 12a8 8 0 1 1-8-8 M9 11l3 3 9-10',Maintenance:'M14 6a5 5 0 0 0-6 6l-5 5a3 3 0 0 0 4 4l5-5a5 5 0 0 0 6-6l-4 1-2-2 2-3z','Manual evidence':'M5 3h10l4 4v14H5z M14 3v5h5 M9 12h6 M9 16h6'};
const icon=t=>`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[t]||paths.Overview}"/></svg>`;
const counts={Fleet:D.hosts.length,Findings:findings.length,Collections:D.jobs.length,'Manual evidence':D.manual.length};
const descriptions={Overview:D.simulation?'Fictional fleet. No real device is contacted.':'Your engagement, at a glance.',Fleet:'Approved devices and their latest saved evidence.',Evidence:'Browse saved evidence, choose columns and trace each result to its collection.',Findings:'Observed conditions to investigate and follow up.',Collections:'A record of the evidence collected so far.',Readiness:'Access, prerequisites and cleanup checks, as last recorded.',Maintenance:'Reviewed changes, canary outcomes and batch progress.','Manual evidence':'Track appliances, cloud services and checks made on site.'};
const grids=new Map(), evidenceScope={host:'',section:'',status:'',mode:'sections'};
const pretty=s=>String(s).replace(/([A-Z]+)([A-Z][a-z])/g,'$1 $2').replace(/([a-z0-9])([A-Z])/g,'$1 $2').replace(/[_.]/g,' ').replace(/^./,c=>c.toUpperCase());
function human(value,depth=0){
 if(value===null||value===undefined||value==='')return 'Not recorded';
 if(typeof value==='boolean')return value?'Yes':'No';
 if(Array.isArray(value))return value.length?value.map(v=>human(v,depth+1)).join(', '):'None recorded';
 if(typeof value==='object')return depth>3?'Additional fields':Object.entries(value).map(([k,v])=>pretty(k)+': '+human(v,depth+1)).join(' · ')||'None recorded';
 return String(value);
}
function matches(value,query){
 if(!query.trim())return true;
 const numeric=query.trim().match(/^(>=|<=|>|<|=)\\s*(-?\\d+(?:\\.\\d+)?)$/);
 if(numeric){if(typeof value!=='number')return false;const n=Number(numeric[2]);return {'>=':value>=n,'<=':value<=n,'>':value>n,'<':value<n,'=':value===n}[numeric[1]]}
 return human(value).toLowerCase().includes(query.toLowerCase());
}
function grid(mount,key,columns,rows,options={}){
 let state=grids.get(key);
 if(!state){state={known:new Set(columns.map(c=>c.key)),visible:new Set(columns.filter(c=>c.default!==false).map(c=>c.key)),filters:Object.create(null),query:'',sort:null,ascending:true,page:0,size:50,columnsOpen:false};grids.set(key,state)}
 for(const c of columns){if(!state.known.has(c.key)){state.known.add(c.key);if(c.default!==false)state.visible.add(c.key)}if(c.pinned)state.visible.add(c.key);}
 const visible=()=>columns.filter(c=>state.visible.has(c.key));
 function draw(){
  const shown=visible();
  mount.innerHTML=`<div class="data-grid"><div class="grid-toolbar"><input class="grid-search" aria-label="Search ${E(options.name||'table')}" placeholder="Search this table…" value="${E(state.query)}"><div class="grid-actions"><button class="clear-grid">Clear table filters</button><details class="column-selector" ${state.columnsOpen?'open':''}><summary>Columns <span class="subtle">${shown.length}/${columns.length}</span></summary><div class="column-menu"><span class="subtle">Choose visible columns</span>${columns.map(c=>`<label><input type="checkbox" data-column="${E(c.key)}" ${state.visible.has(c.key)?'checked':''} ${c.pinned?'disabled':''}>${E(c.label)}</label>`).join('')}</div></details></div></div><div class="scroll grid-scroll"><table><thead><tr>${shown.map(c=>`<th scope="col" aria-sort="${state.sort===c.key?(state.ascending?'ascending':'descending'):'none'}"><button class="sort-column" data-sort="${E(c.key)}" aria-label="Sort by ${E(c.label)}">${E(c.label)}<span aria-hidden="true">${state.sort===c.key?(state.ascending?'↑':'↓'):'↕'}</span></button></th>`).join('')}</tr><tr class="column-filters">${shown.map(c=>`<th><input data-filter="${E(c.key)}" aria-label="Filter ${E(c.label)}" placeholder="${c.numeric?'e.g. >=10':'Filter…'}" value="${E(state.filters[c.key]||'')}"></th>`).join('')}</tr></thead><tbody></tbody></table></div><div class="grid-footer"><span class="grid-count" role="status" aria-live="polite"></span><div class="grid-pagination"><label>Rows <select aria-label="Rows per page">${[25,50,100].map(n=>`<option ${state.size===n?'selected':''}>${n}</option>`).join('')}</select></label><button class="previous-page" aria-label="Previous page">←</button><button class="next-page" aria-label="Next page">→</button></div></div></div>`;
  mount.querySelector('.grid-search').oninput=e=>{state.query=e.target.value;state.page=0;update()};
  mount.querySelector('.clear-grid').onclick=()=>{state.query='';state.filters=Object.create(null);state.page=0;draw()};
  const picker=mount.querySelector('.column-selector');picker.ontoggle=()=>{state.columnsOpen=picker.open};
  mount.querySelectorAll('[data-column]').forEach(input=>input.onchange=()=>{const k=input.dataset.column;if(input.checked)state.visible.add(k);else{state.visible.delete(k);delete state.filters[k];if(state.sort===k)state.sort=null}state.page=0;state.columnsOpen=true;draw()});
  mount.querySelectorAll('[data-filter]').forEach(input=>input.oninput=()=>{state.filters[input.dataset.filter]=input.value;state.page=0;update()});
  mount.querySelectorAll('[data-sort]').forEach(button=>button.onclick=()=>{const k=button.dataset.sort;state.ascending=state.sort===k?!state.ascending:true;state.sort=k;state.page=0;draw()});
  mount.querySelector('.grid-pagination select').onchange=e=>{state.size=Number(e.target.value);state.page=0;update()};
  mount.querySelector('.previous-page').onclick=()=>{state.page--;update()};
  mount.querySelector('.next-page').onclick=()=>{state.page++;update()};
  update();
 }
 function update(){
  const shown=visible();let filtered=rows.filter(row=>shown.every(c=>matches(c.date?F(c.value(row))+' '+human(c.value(row)):c.value(row),state.filters[c.key]||''))&&(!state.query||columns.some(c=>matches(c.date?F(c.value(row))+' '+human(c.value(row)):c.value(row),state.query))));
  const sort=columns.find(c=>c.key===state.sort);
  if(sort)filtered.sort((a,b)=>{const x=sort.value(a),y=sort.value(b);if(x==null||y==null)return x==null?(y==null?0:1):-1;const order=typeof x==='number'&&typeof y==='number'?x-y:human(x).localeCompare(human(y),undefined,{numeric:true,sensitivity:'base'});return state.ascending?order:-order});
  state.page=Math.max(0,Math.min(state.page,Math.max(0,Math.ceil(filtered.length/state.size)-1)));
  const start=state.page*state.size,selected=filtered.slice(start,start+state.size),body=mount.querySelector('tbody');
  body.innerHTML=selected.map(row=>`<tr>${shown.map(c=>{const v=c.value(row);return `<td>${c.cell?c.cell(v,row):`<span class="cell-value">${E(human(v))}</span>`}</td>`}).join('')}</tr>`).join('')||`<tr><td colspan="${shown.length}"><div class="empty">${E(rows.length?'No rows match these filters.':options.empty||'No saved evidence in this scope.')}</div></td></tr>`;
  mount.querySelector('.grid-count').textContent=filtered.length?`${start+1}–${Math.min(start+state.size,filtered.length)} of ${filtered.length} rows${filtered.length!==rows.length?' · '+rows.length+' total':''}`:`0 rows${rows.length?' · '+rows.length+' total':''}`;
  mount.querySelector('.previous-page').disabled=state.page===0;mount.querySelector('.next-page').disabled=start+state.size>=filtered.length;
  if(options.bind)options.bind(body);
 }
 draw();
}
function openEvidence(host){
 evidenceScope.host=host;evidenceScope.section='';evidenceScope.status='';evidenceScope.mode='sections';
 const state=grids.get('evidence-sections');if(state){state.filters=Object.create(null);state.query='';state.page=0}
 tab='Evidence';render();
}
const sectionRows=()=>D.hosts.flatMap(host=>Object.entries(host.sections||{}).map(([section,data])=>({host,section,data,source:host.sources?.[section]||{}})));
function renderEvidence(){
 const available=sectionRows(),sections=[...new Set(available.map(r=>r.section))].sort();
 $('view').innerHTML=panel('Collected evidence','<div class="evidence-switch"><div class="segmented"><button id="section-mode">Sections & sources</button><button id="record-mode" '+(!PRIVATE?'disabled':'')+'>Collected records</button></div><span class="subtle">Filter a column below its heading. Click a heading to sort.</span></div><div id="evidence-note" class="evidence-note"></div><div id="evidence-grid"></div>',`<div class="filters"><select id="evidence-host" aria-label="Evidence host"><option value="">All hosts</option>${D.hosts.map(h=>`<option value="${E(h.name)}">${E(h.name)}</option>`).join('')}</select><select id="evidence-section" aria-label="Evidence section"><option value="">All sections</option>${sections.map(s=>`<option value="${E(s)}">${E(pretty(s))}</option>`).join('')}</select><select id="evidence-status" aria-label="Evidence status"><option value="">All evidence states</option><option value="collected">Collected</option><option value="unknown">Unknown</option></select></div>`);
 $('evidence-host').value=evidenceScope.host;$('evidence-section').value=evidenceScope.section;$('evidence-status').value=evidenceScope.status;
 for(const [id,key] of [['evidence-host','host'],['evidence-section','section'],['evidence-status','status']])$(id).onchange=e=>{evidenceScope[key]=e.target.value;drawEvidence()};
 $('section-mode').onclick=()=>{evidenceScope.mode='sections';drawEvidence()};
 $('record-mode').onclick=()=>{evidenceScope.mode='records';drawEvidence()};
 drawEvidence();
}
function drawEvidence(){
 const rows=sectionRows().filter(r=>(!evidenceScope.host||r.host.name===evidenceScope.host)&&(!evidenceScope.section||r.section===evidenceScope.section)&&(!evidenceScope.status||r.data.status===evidenceScope.status));
 const records=evidenceScope.mode==='records'&&PRIVATE;
 $('section-mode').classList.toggle('active',!records);$('record-mode').classList.toggle('active',records);$('section-mode').setAttribute('aria-pressed',String(!records));$('record-mode').setAttribute('aria-pressed',String(records));
 if(records&&!evidenceScope.section){$('evidence-note').textContent='Choose a section above to browse its collected records as columns.';$('evidence-grid').innerHTML=empty('Select a section, such as inventory software or inventory firewall.');return}
 const base=[{key:'host',label:'Host',pinned:true,value:r=>r.host.name},{key:'section',label:'Section',value:r=>pretty(r.section),default:!records},{key:'state',label:'Evidence state',value:r=>r.data.status,cell:v=>B(v),default:!records},{key:'count',label:'Total reported',numeric:true,value:r=>r.data.total,default:!records},{key:'saved',label:'Saved rows',numeric:true,value:r=>Array.isArray(r.data.items)?r.data.items.length:null,default:false},{key:'truncated',label:'Truncated',value:r=>typeof r.data.truncated==='boolean'?r.data.truncated:null,default:!records},{key:'collected',label:'Collected at',date:true,value:r=>r.source.collected_at||null,cell:v=>E(F(v))},{key:'job',label:'Source job',value:r=>r.source.job_id,cell:v=>`<span class="source-job">${E(human(v))}</span>`,default:!records},{key:'note',label:'Collection note',value:r=>r.data.error||null,default:false}];
 if(records){
  const items=rows.filter(r=>r.data.status==='collected').flatMap(r=>(Array.isArray(r.data.items)?r.data.items:[]).map(item=>({...r,item:item!==null&&typeof item==='object'&&!Array.isArray(item)?item:{Value:item}}))),fields=[...new Set(items.flatMap(r=>Object.keys(r.item)))];
  const columns=[base[0],...fields.map((field,i)=>({key:'record:'+field,label:pretty(field),value:r=>Object.prototype.hasOwnProperty.call(r.item,field)?r.item[field]:null,default:i<10})),...base.slice(1)];
  const unknown=rows.filter(r=>r.data.status!=='collected').length,truncated=rows.filter(r=>r.data.truncated===true).length;
  $('evidence-note').textContent=`${pretty(evidenceScope.section)} · ${items.length} saved ${items.length===1?'record':'records'}${truncated?' · '+truncated+' truncated sections; some original rows were not saved':''}${unknown?' · '+unknown+' unknown sections; see Sections & sources for collection notes':''}. Values are saved evidence, not live checks.`;
  grid($('evidence-grid'),'records:'+evidenceScope.section,columns,items,{name:'collected records',empty:'No records were saved for this selection. Check Sections & sources for unknown results and collection notes.'});
 }else{
  if(PRIVATE)base.push({key:'records',label:'Records',value:r=>Array.isArray(r.data.items)?r.data.items.length:0,cell:(v,r)=>v&&r.data.status==='collected'?`<button class="show-records" data-host="${E(r.host.name)}" data-section="${E(r.section)}">View ${v} ${v===1?'record':'records'}</button>`:v?'<span class="subtle">Unverified values</span>':'<span class="subtle">No saved rows</span>'});
  $('evidence-note').textContent=PRIVATE?'Each row shows one saved section and the collection it came from. Open its records for individual evidence fields.':'Shareable summary: raw evidence values, collection errors and record browsing are omitted.';
  grid($('evidence-grid'),'evidence-sections',base,rows,{name:'evidence sections',bind:body=>body.querySelectorAll('.show-records').forEach(b=>b.onclick=()=>{evidenceScope.host=b.dataset.host;evidenceScope.section=b.dataset.section;evidenceScope.status='';evidenceScope.mode='records';renderEvidence()})});
 }
}

function overview(){
 const pct=D.hosts.length?Math.round(100*complete/D.hosts.length):0;
 const queue=findings.map(f=>({title:f.title,detail:f.host+(f.detail?' · '+f.detail:''),status:f.severity,view:'Findings'})).concat(unresolved.map(r=>({title:r.asset,detail:label(r.category)+' · Control '+r.control,status:r.status,view:'Manual evidence'})));
 const queueBody=queue.slice(0,5).map(r=>`<div class="queue-row"><span class="queue-icon">${icon(r.view)}</span><div><h3>${E(r.title)}</h3><p>${E(r.detail)}</p></div>${B(r.status)}</div>`).join('')||empty('No recorded follow-ups. Check evidence coverage before drawing conclusions.');
 const coverage=`<div class="coverage"><div class="coverage-ring"><svg viewBox="0 0 100 100" aria-hidden="true"><circle cx="50" cy="50" r="42" fill="none" stroke="#efedf7" stroke-width="7"/><circle cx="50" cy="50" r="42" fill="none" stroke="#8174dc" stroke-width="7" stroke-dasharray="${pct*2.639} 263.9" stroke-linecap="round"/></svg><strong>${D.hosts.length?pct+'%':'—'}</strong></div><div><strong>Collection coverage</strong><p>${D.hosts.length?complete+' of '+D.hosts.length+' latest collections complete':'No device evidence yet'}</p><p class="subtle">Collection status only</p></div></div><div class="coverage-legend"><div><b>${complete}</b>Complete</div><div><b>${D.hosts.length-complete}</b>Other / not collected</div></div>`;
 const activity=D.jobs.slice().reverse().slice(0,4).map(j=>`<div class="activity-row"><div><strong>${E(j.operation)}</strong><small>${E(F(j.created_at))}</small></div>${B(j.status)}</div>`).join('')||empty('Collections will appear here after evidence is imported or collected.');
 const scope=[['0001','Cyber Essentials'],['2314','GDPR'],['2500','Resilience']].map(([id,title])=>{const records=D.manual.filter(r=>r.control===id),open=records.filter(r=>['open','unknown'].includes(r.status)).length;return `<div class="activity-row"><div><strong>${E(title)}</strong><small>Control ${id} · ${records.length} manual records</small></div><span class="subtle">${open} open</span></div>`}).join('');
 return (D.simulation?'<div class="notice"><strong>Simulation mode</strong> · All devices, findings and connection outcomes on this page are fictional. Real SSH and endpoint changes are disabled.</div>':'')+(D.storage_errors.length?`<div class="notice">${D.storage_errors.length} source records could not be read. Coverage may be incomplete.</div>`:'')+`<div class="overview-grid"><div>${panel('Needs review',queueBody,`<span class="subtle">${queue.length} recorded items</span>`)}${panel('Recent collections',activity,`<button class="text-button" data-go="Collections">View all →</button>`)}</div><div>${panel('Evidence coverage',coverage)}${panel('Manual evidence by control',scope,`<button class="text-button" data-go="Manual evidence">Open tracker →</button>`)}</div></div>`;
}
function table(head,rows){return `<div class="scroll"><table><thead><tr>${head.map(x=>`<th>${E(x)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.join(''):`<tr><td colspan="${head.length}"><div class="empty">No saved records in this view.</div></td></tr>`}</tbody></table></div>`}
function panel(title,body,extra=''){return `<div class="panel"><div class="panel-head"><h2>${E(title)}</h2>${extra}</div>${body}</div>`}
function empty(text){return `<div class="empty">${E(text)}</div>`}
function render(){
 window.scrollTo(0,0);
 $('tabs').innerHTML=tabs.map(t=>`<button class="${tab===t?'active':''}" data-tab="${E(t)}" ${tab===t?'aria-current="page"':''}>${icon(t)}<span>${E(t)}</span>${counts[t]!==undefined?`<span class="nav-count">${counts[t]}</span>`:''}</button>`).join('');
 $('tabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});
 $('page-title').textContent=tab;$('page-description').textContent=descriptions[tab];$('crumb').textContent=tab;$('metrics').hidden=tab!=='Overview';
 if(tab==='Overview'){
  $('view').innerHTML=overview();$('view').querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>{tab=b.dataset.go;render()});
 }else if(tab==='Fleet'){
  $('view').innerHTML=panel('Approved scope and saved results','<div id="host-table"></div>',`<div class="filters"><input id="search" placeholder="Find a host" aria-label="Find a host"><select id="group" aria-label="Group"><option value="">All groups</option>${[...new Set(D.hosts.flatMap(h=>h.groups))].map(g=>`<option>${E(g)}</option>`).join('')}</select><select id="status" aria-label="Status"><option value="">All outcomes</option>${[...new Set(D.hosts.map(h=>h.collection_status))].map(s=>`<option>${E(s)}</option>`).join('')}</select></div>`);
  const columns=[
   {key:'host',label:'Host / target',pinned:true,value:h=>h.name+' '+h.target,cell:(_,h)=>`<strong>${E(h.name)}</strong><div class="subtle">${E(h.target)}${h.port&&h.port!==22?' · Port '+E(h.port):''}</div>${h.approved?'':'<div class="subtle">Historical · not approved now</div>'}`},
   {key:'platform',label:'Platform',value:h=>h.platform||'windows',cell:(v,h)=>`${E(v)}${h.simulation?'<div class="subtle">Simulated</div>':''}`},
   {key:'groups',label:'Groups',value:h=>h.groups.join(', '),default:false},
   {key:'approved',label:'Currently approved',value:h=>h.approved,default:false},
   {key:'outcome',label:'Saved outcome',value:h=>h.collection_status,cell:v=>B(v)},
   {key:'readiness',label:'Readiness',value:h=>h.readiness?.state||'not_checked',cell:v=>B(v)},
   {key:'connection',label:'Observed connection',value:h=>h.readiness?.connection||'Not checked',default:false},
   {key:'checked',label:'Readiness checked',date:true,value:h=>h.readiness?.checked_at||null,cell:v=>E(F(v)),default:false},
   {key:'collected',label:'Last collected',date:true,value:h=>h.last_collected_at||null,cell:v=>E(F(v))},
   {key:'findings',label:'Observations',numeric:true,value:h=>h.findings.length},
   {key:'evidence',label:'Evidence',value:h=>Object.keys(h.sections||{}).length,cell:(n,h)=>`<button class="open-evidence" data-host="${E(h.name)}">View evidence${n?' · '+n:''}</button>`}
  ];
  const filter=()=>grid($('host-table'),'fleet',columns,D.hosts.filter(h=>(h.name+' '+h.target).toLowerCase().includes($('search').value.toLowerCase())&&(!$('group').value||h.groups.includes($('group').value))&&(!$('status').value||h.collection_status===$('status').value)),{name:'fleet',bind:body=>body.querySelectorAll('.open-evidence').forEach(b=>b.onclick=()=>openEvidence(b.dataset.host))});filter();['search','group','status'].forEach(id=>$(id).oninput=filter);
 }else if(tab==='Evidence'){
  renderEvidence();
 }else if(tab==='Findings'){
  $('view').innerHTML=panel('Observed conditions and recommended actions',findings.map(f=>`<article class="finding"><h3>${E(f.host)} · ${E(f.title)}</h3>${B(f.severity)}<p>${E(f.detail||'')}</p><p>${E(f.recommendation)}</p></article>`).join('')||empty('No configured rule matched. This is not a compliance pass.'));
 }else if(tab==='Collections'){
  $('view').innerHTML=panel('Collection history',table(['Saved job','Operation','Outcome','Created','Host outcomes'],D.jobs.slice().reverse().map(j=>`<tr><td>${E(j.id)}</td><td>${E(j.operation)}</td><td>${B(j.status)}</td><td>${E(F(j.created_at))}</td><td>${Object.entries(j.hosts).map(([n,s])=>`${E(n)} ${B(s)}`).join('<br>')}</td></tr>`)))+(D.storage_errors.length?panel('Unreadable source records',empty(D.storage_errors.length+' records could not be read. Inspect the private workspace before relying on coverage.')):'');
 }else if(tab==='Readiness'){
  $('view').innerHTML=panel('Point-in-time checks',table(['Host','Readiness','Observed SSH connection','Cleanup','Checked at','Notes'],D.hosts.map(h=>`<tr><td>${E(h.name)}</td><td>${B(h.readiness?.state||'not_checked')}</td><td>${E(h.readiness?.connection||'Unknown')}</td><td>${B(h.readiness?.cleanup||'unknown')}</td><td>${E(F(h.readiness?.checked_at))}</td><td>${(h.readiness?.notes||[]).map(E).join('<br>')}</td></tr>`)))+empty('Run a readiness collection before visiting or changing a host. Missing modules, low disk space and cleanup expiry are reported separately from collection completion.');
 }else if(tab==='Maintenance'){
  $('view').innerHTML=panel('Reviewed maintenance batches',table(['Batch','Action','Canary','State','Host results'],D.batches.slice().reverse().map(b=>`<tr><td>${E(b.id)}</td><td>${E(b.action)}</td><td>${E(b.canary)}</td><td>${B(b.status)}</td><td>${Object.entries(b.hosts).map(([n,h])=>`${E(n)} ${B(h.status)}`).join('<br>')}</td></tr>`)))+empty('Preview the exact scope, run one canary, review its saved result, then explicitly continue. Already dispatched operations may finish after the failure limit is reached. This page runs no maintenance.');
 }else{
  $('view').innerHTML=panel('Appliances and cloud services','<div class="manual-grid" id="manual-records"></div>',`<div class="filters"><input id="manual-search" aria-label="Find manual evidence" placeholder="Search evidence…"><select id="manual-status" aria-label="Manual status"><option value="">All statuses</option>${['open','verified','unknown','not_applicable'].map(x=>`<option value="${x}">${E(label(x))}</option>`).join('')}</select></div>`)+(PRIVATE?`<details class="panel manual-editor" id="manual-editor"><summary>Add or update evidence</summary>${manualForm()}</details>`:empty('Manual notes and editing are omitted in a shareable export.'));
  const filterManual=()=>{const records=D.manual.filter(r=>(r.asset+' '+r.note+' '+r.category+' '+r.control).toLowerCase().includes($('manual-search').value.toLowerCase())&&(!$('manual-status').value||r.status===$('manual-status').value));$('manual-records').innerHTML=records.map(r=>`<article class="evidence-card"><div class="card-top"><span class="category">${E(r.category)} · ${E(r.control)}</span>${B(r.status)}</div><h3>${E(r.asset)}</h3><p>${E(r.note)}</p>${r.reference?`<p class="reference">${E(r.reference)}</p>`:''}<div class="card-foot"><span>${E(r.reviewer)} · Revision ${E(r.revision)}</span>${PRIVATE?`<button class="edit-record" data-id="${E(r.id)}">Edit record</button>`:''}</div></article>`).join('')||empty('No evidence matches these filters.');if(PRIVATE)document.querySelectorAll('.edit-record').forEach(b=>b.onclick=()=>editManual(b.dataset.id))};filterManual();['manual-search','manual-status'].forEach(id=>$(id).oninput=filterManual);
  if(PRIVATE){$('manual').onsubmit=stageManual;$('download').onclick=downloadManual}
 }
}
function manualForm(){return `<div class="panel"><div class="panel-head"><h2>Add or update manual evidence</h2><span id="pending" class="subtle">${changes.length} staged changes</span></div><form id="manual" class="form"><input type="hidden" name="record_id"><input type="hidden" name="expected_revision"><label>Asset<input name="asset" required maxlength="160"></label><label>Category<select name="category">${['nas','m365','router','cctv','backup','other'].map(x=>`<option>${x}</option>`).join('')}</select></label><label>Control<select name="control"><option value="0001">0001 · Cyber Essentials</option><option value="2314">2314 · GDPR</option><option value="2500">2500 · Resilience</option></select></label><label>Operator status<select name="status">${['open','verified','unknown','not_applicable'].map(x=>`<option>${x}</option>`).join('')}</select></label><label class="wide">Notes<textarea name="note" maxlength="4000"></textarea></label><label>Evidence reference<input name="reference" maxlength="1000" placeholder="Document or evidence location; never passwords"></label><label>Reviewer<input name="reviewer" required maxlength="160"></label><input type="hidden" name="job_id"><div class="wide toolbar"><button type="submit" class="primary">Stage change</button><button type="button" id="download">Download manual updates</button><span class="subtle">Import the downloaded JSON with the CLI to persist it. Closing without downloading loses staged changes.</span></div></form><div class="notice" style="margin:0 22px 22px">Changes stay in this page until downloaded. Import with: <code>remote-fleet-agent workspace import-manual --config fleet.local.json --input manual-updates.json</code>. Import validates all changes and rejects stale revisions.</div></div>`}
function editManual(id){$('manual-editor').open=true;const r=D.manual.find(r=>r.id===id),f=$('manual');for(const k of ['asset','category','control','status','note','reference','reviewer','job_id'])f.elements[k].value=r[k]||'';f.elements.record_id.value=r.id;f.elements.expected_revision.value=r.revision;f.scrollIntoView({behavior:'smooth'})}
function stageManual(event){event.preventDefault();const f=new FormData(event.target),fields={};for(const k of ['asset','category','control','status','note','reference','reviewer','job_id'])fields[k]=f.get(k)||'';const change={fields};if(f.get('record_id')){change.id=f.get('record_id');change.expected_revision=Number(f.get('expected_revision'))}const previous=change.id?changes.findIndex(c=>c.id===change.id):-1;if(previous>=0)changes[previous]=change;else changes.push(change);$('pending').textContent=changes.length+' staged changes';event.target.reset()}
function downloadManual(){if(!changes.length){alert('Stage a change first.');return}const blob=new Blob([JSON.stringify({schema:'fleet-manual-import-v1',client:D.client,changes},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='manual-updates.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
render();
</script></body></html>"""
    )
