# Engagement workflow: dashboard, reports and controlled work

Version 0.5 adds six features: an offline fleet dashboard, evidence reports, readiness checks, maintenance batches, shareable summaries with reviewed retention, and manual appliance/cloud evidence. They use the existing engagement configuration and job root. The default job root is `~/.remote-fleet-agent/jobs`; add `--job-dir` consistently to use another root.

## Dashboard and reports

```sh
remote-fleet-agent workspace dashboard --config fleet.local.json --private --output ./dashboard.html
remote-fleet-agent workspace report --config fleet.local.json --output ./shareable-report.html
```

Open the generated HTML in a browser. Both documents are self-contained, with no external assets or network requests. The report has a print/save-as-PDF button. Each export is also saved under the managed engagement `exports/` directory. Additional `--output` copies are outside managed retention.

The dashboard has Overview, Fleet, Evidence, Findings, Collections, Readiness, Maintenance and Manual evidence views. Filter hosts by name, group and saved outcome. Use View evidence on a host to open its saved sections and sources, then View records to browse individual values as columns. Choose Columns to show or hide fields, type below a heading to filter, or click a heading to sort. Numeric filters accept comparisons such as >=4. Larger tables have pagination. Column choices and filters stay in the open page; reloading starts a fresh view. Shareable summaries omit raw record browsing. Historical hosts removed from the current approved scope are labelled accordingly. The latest result for each section is chosen by collection timestamp, so a late-finishing older job does not hide newer evidence.

This is a saved snapshot, not a live control panel. Regenerate it after jobs or imports. A connected readiness result describes the time of that check; it does not claim the host is currently reachable. Unknown, empty and truncated evidence is not a pass. Unreadable source records are flagged rather than hidden.

The first review rules report explicit disabled firewall profiles, BitLocker protection off, Defender fields needing review, disks below 10% free space, selected reboot indicators, and missing security evidence. These are observations and suggested investigations. Defender can be passive with another antivirus product, disk usage changes naturally, and a selected registry signal is not a complete servicing diagnosis. No certification or legal-compliance score is generated.

## Shareable and private exports

Exports are shareable summaries by default. `--private` includes raw evidence, identities, manual notes and references. The MCP equivalent is `export_evidence(kind="dashboard"|"report", shareable=True|False)`.

Shareable output is built from an allowlist. It uses generic host/asset/job labels, fixed finding text, selected counts, statuses and timestamps. It omits client names, targets, usernames, addresses, host groups, raw query rows, free-text notes, reviewers, references and revision history. It does not perform regex substitution over private text. Do not treat a private export as shareable merely because one field was removed manually.

Reports and dashboards can themselves contain sensitive data. Managed files are written privately. Browser downloads and copies moved elsewhere are under the operator's control and are not covered by managed retention.

## Manual evidence

Manual tracking works before SSH setup and needs no private key. The configuration still names a `known_hosts` path, but the offline `workspace` command does not require that file to exist. Remote collection and MCP startup still require verified host keys.

```sh
remote-fleet-agent workspace manual-add --config fleet.local.json \
  --asset "Backup service" --category backup --control 2500 \
  --status open --reviewer "Engineer" \
  --note "Offsite status needs verification" --reference "Interview notes"
remote-fleet-agent workspace manual-list --config fleet.local.json
```

Categories are `nas`, `m365`, `router`, `cctv`, `backup` and `other`. Controls are `0001`, `2314` and `2500`. Operator statuses are `open`, `verified`, `unknown` and `not_applicable`. Verification is an operator assertion, not an automatic verdict. Reference documents by location; this feature does not upload or read them. Never store passwords, recovery keys or other secrets in notes.

Each record has an ID and revision. Updates replace its fields and require the current revision; earlier field values remain in history. `manual-update` needs `--record-id`, `--revision` and the replacement asset/category/control/reviewer/status/note/reference fields. An optional `--job-id` links a saved evidence job and protects it from retention.

In a private dashboard, use Edit or the form to stage changes. Changes remain in that browser page until **Download manual updates** is used. Import the JSON to persist it, then regenerate the dashboard:

```sh
remote-fleet-agent workspace import-manual --config fleet.local.json --input manual-updates.json
```

Imports require schema `fleet-manual-import-v1`, the matching client, and one to 500 changes. Every change is validated before saving. A stale revision rejects the whole import so newer edits are not overwritten. Local writes are protected by a cross-process lock. The browser form does not write directly to the original HTML or managed store.

MCP tools: `list_manual_evidence`, `add_manual_evidence`, `update_manual_evidence`. Updates supply complete replacement `fields` and `expected_revision`.

## Readiness before collection or changes

```sh
remote-fleet-agent run-fleet --config fleet.local.json --key-path /secure/id_ed25519 \
  --operation readiness --group office
```

Readiness reads administrator context, command availability, system-drive headroom and the current engagement's cleanup journal/task/expiry. It sends no external network probe, installs nothing, starts no service and makes no repair. Collection completion and readiness are separate fields. Missing modules, unavailable privileges, low disk space, missing or overdue cleanup and expiry within one day are reported.

The maintenance gate requires established administrator access and at least 256 MiB of system-drive headroom. That threshold is for these small maintenance actions, not the installer's two-GB preflight. Missing or legacy/custom cleanup metadata remains a visible review item; a generic journal lookup cannot prove how another installer expires.

MCP provides `readiness(host)` and `start_job(operation="readiness", ...)`. The new probes have local parser/response tests and simulated runtime validation; they have not been exercised live on Windows in this development phase.

## Reviewed maintenance batches

Maintenance remains disabled unless the engagement permits the fixed action. `REMOTE_FLEET_AGENT_AUDIT_ONLY=1` suppresses both single-host and batch maintenance. Read the single-action limits in [MAINTENANCE.md](MAINTENANCE.md) first.

```sh
remote-fleet-agent batch preview --config fleet.local.json --key-path /secure/id_ed25519 \
  --action start_time_service --group office --canary office-01 \
  --wave-size 2 --max-failures 1
```

The preview checks readiness, creates per-host previews and records the exact targets, action, preconditions, effects, recovery limits and any skipped preview failures. Unknown scope rejects the request before connecting. An unready canary blocks execution; it is never silently replaced. The batch digest binds the full reviewed specification. Its expiry is the earliest child-plan expiry, normally within ten minutes; use smaller scopes if preparing the batch takes too long.

Run only the canary using the returned ID and digest:

```sh
remote-fleet-agent batch canary --config fleet.local.json --key-path /secure/id_ed25519 \
  --batch-id BATCH_ID --digest PREVIEW_DIGEST
```

Review the saved result. Only a completed canary permits the separate continuation command:

```sh
remote-fleet-agent batch continue --config fleet.local.json --key-path /secure/id_ed25519 \
  --batch-id BATCH_ID --digest PREVIEW_DIGEST
```

Remaining hosts execute in waves bounded by engagement concurrency. Execution failures or unknown outcomes stop later waves once the failure limit is reached. Other operations already dispatched in that wave can finish. Preview failures are visible skipped hosts, not hidden retries. No automatic rollback or retry runs. Inspect each child plan before any recovery decision; successful service starts retain their existing single-host guarded rollback option.

`batch show --batch-id ...` reads the saved record. `batch cancel --batch-id ...` prevents later waves; it does not promise to cancel a command already running remotely. Both commands also take `--config` and the same job root. The canary, continuation and terminal states are single-use. A transport cancellation/interruption is not a safe retry signal.

Fleet MCP tools: `preview_batch`, `get_batch` and `cancel_batch`, available when maintenance is enabled. Batch endpoint execution is available through the engineer-operated CLI; AI execution belongs in the workspace support bridge with a recorded human decision. Keep the owning process alive. After an abrupt process failure, inspect endpoint and child-plan outcomes before clearing a stale lock. Batches never silently resume. The underlying account retains its own privileges.

The batch workflow has been tested with simulated hosts, failures, cancellation, expiry and concurrency. Its existing single-host actions were tested previously on one Windows PC, but the new batch workflow itself has no live Windows validation.

## Reviewed retention

```sh
remote-fleet-agent workspace retention-preview --config fleet.local.json --days 90 --keep-recent 5
remote-fleet-agent workspace retention-apply --config fleet.local.json --plan-id PLAN_ID --digest PREVIEW_DIGEST
```

A preview lists the exact relative filenames, hashes and byte count. It expires after ten minutes. Apply permanently deletes those managed copies after validating the entire preview, current file hashes, identities and newly added references. Changed files cause a fresh-preview requirement before anything is deleted. Local write locking prevents competing retention or manual changes. The deletion receipt remains for review; a partial failure records completed deletions and is not automatically replayed.

By default only complete collection jobs and older managed report/dashboard exports are eligible. `--include-partial` explicitly includes older unsuccessful terminal collection jobs too. Active jobs, manual evidence, maintenance/batch journals, jobs referenced by current or historical manual evidence, retained retry lineage, and external copies are protected. `--keep-recent` is applied per collection operation and to managed exports. Retention does not promise secure media erasure or purge backups/external copies. Manual evidence and change journals require a separate operator retention decision.

MCP tools: `preview_retention`, `apply_retention`. Deletion is a local evidence-store operation, not a Windows endpoint operation. Thus endpoint read-only mode can still expose local evidence writes and reviewed retention. Tools carry the corresponding read-only/destructive annotations.

## Validation and privacy boundary

No new Windows connection or operation was performed for this release phase. Validation uses saved results, disposable local stores, mocked hosts, PowerShell syntax checks, an actual local MCP stdio exchange and browser interaction. Browser download/import was exercised end to end against a separate test workspace. These tests do not extend the prior one-PC compatibility claim. No public publication or licence choice is implied.
