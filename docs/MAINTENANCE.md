# Controlled Windows maintenance

This guide describes the original fixed-action interface. Version 0.9 adds a separate [engineer-approved support workflow](AI-SUPPORT.md) with reviewed PowerShell/shell repairs. Its approval model and opt-in are separate from the legacy actions below.

Maintenance is disabled unless the engagement configuration lists permitted `maintenance_actions`. An empty list keeps the endpoint tool surface read-only. `REMOTE_FLEET_AGENT_AUDIT_ONLY=1` overrides an enabled list in the server and CLI. Restart the MCP server after configuration changes.

The first actions are deliberately small:

| Action | Effect | Recovery limit |
| --- | --- | --- |
| `start_print_spooler` | Start a stopped Spooler; queued printing may resume | A guarded stop can interrupt printing; it cannot undo printed jobs |
| `start_time_service` | Start a stopped W32Time; configured synchronization may run | A guarded stop does not reverse clock adjustments |
| `clear_dns_cache` | Clear the local resolver cache | Cache entries cannot be restored; new queries may immediately repopulate it |

There is no service-name parameter, startup-type change, restart, reboot, arbitrary script, deletion, patch installation or security-setting modification. Disabled services and transitional service states cannot be previewed for starting.

## Preview and execute

Create a separate private engagement configuration with the same approved-host and verified-host-key requirements as inventory. List only permitted actions, for example:

```json
"maintenance_actions": ["start_time_service"]
```

Permission applies to every approved host in that engagement. Use a smaller engagement for a smaller maintenance scope. Keep keys and real configurations private.

Using the same configuration, key and job directory for each invocation:

```sh
remote-fleet-agent maintenance preview --config fleet.local.json \
  --key-path /secure/engagement/id_ed25519 --host office-01 \
  --action start_time_service
```

Read the output: named host, target, effect, observed state, recovery limits, expiry and digest. Confirm that the operator has authorized that specific effect on the named machine. Then pass the returned ID and digest:

```sh
remote-fleet-agent maintenance execute --config fleet.local.json \
  --key-path /secure/engagement/id_ed25519 --plan-id PLAN_ID --digest PREVIEW_DIGEST
```

The fleet MCP provides `preview_maintenance(host, action)` and `get_maintenance` for inspection. It cannot execute or roll back endpoint changes. For AI-led changes use the workspace support bridge and a separate human approval. The explicit maintenance CLI below remains available to the engineer.

A preview expires after ten minutes. Execution rechecks approved scope, enabled action, target and host-key-file contents. For services, the remote command rechecks status and startup type immediately before acting, waits for the requested state and reads it back. An already running service produces a no-op and is not eligible for rollback. DNS verification records cmdlet completion and a subsequent cache count; it does not require the cache to remain empty.

A saved plan is single-use. Both plan and host locks prevent concurrent controller changes within the same engagement/job store, including across server processes. These locks do not coordinate other administrators, Windows or different job stores. There is still a small race between a remote state check and the service operation.

## Saved outcomes and uncertain changes

The controller persists intent before sending a change. Change records are private JSON files under `<job-root>/<client>/maintenance/`, with initial state, timestamps, effect, result and an attempt history. They contain no recovery keys or private SSH keys. This journal supports operational review; a local file owner can modify it, so it is not a tamper-proof audit ledger.

Use `get_maintenance(plan_id)` or:

```sh
remote-fleet-agent maintenance show --config fleet.local.json --plan-id PLAN_ID
```

`complete` means the defined post-check succeeded. `unknown` or `rollback_unknown` means the controller cannot establish the outcome; an SSH error can happen after the remote command ran. A precondition error also conservatively produces unknown. Inspect actual service/cache state before deciding what to do next. These records cannot be replayed. No automatic retries or rollback run after an uncertain outcome.

If the controller dies mid-change, the record may remain `executing` or `rolling_back` and lock files may remain. Treat those as uncertain. Inspect the endpoint and confirm the owner process is gone before removing a stale lock. There is no automatic lock expiry or background recovery. If storage fails after a change, the last durable intent record remains authoritative about uncertainty; do not assume that absence of a final result means no change happened.

## Guarded service reversal

For a completed service start that changed a stopped service to running, an operator can request:

```sh
remote-fleet-agent maintenance rollback --config fleet.local.json \
  --key-path /secure/engagement/id_ed25519 --plan-id PLAN_ID --digest PREVIEW_DIGEST
```

CLI rollback rechecks engagement permissions and host identity, requires the service still to be running with its original startup type, then stops it and verifies the stopped state. Review current service use before invoking it. The guard cannot determine whether other people have begun using the service since the initial start. It does not reverse downstream work such as printing or clock adjustments. DNS clearing has no rollback. Fleet MCP rollback is unavailable; AI recovery must be part of the workspace-approved plan.

## Validation boundary

Automated tests cover scope, configuration opt-in, expiry, digest mismatch, changed keys/targets, durable intent, concurrency, replay, no-op, failure, cancellation and rollback eligibility. Generated PowerShell runs locally against simulated cmdlets, including state-drift cases. A supervised live MCP test on the approved Windows test PC completed a DNS cache clear and read back zero entries immediately afterward. An already-running Print Spooler produced a verified no-op. Both plans rejected execution replay and ineligible rollback. A fresh server launch after disabling the temporary action list exposed the original 13 tools. On 8 November 2026, the user additionally authorized testing on the existing PC. A live MCP test started Windows Time from Stopped/Manual, verified Running/Manual, then used the saved plan to perform guarded rollback and verify Stopped/Manual. Execution replay and duplicate rollback were rejected; both attempts were retained. An independent endpoint read matched the original service states, including an unchanged Running/Automatic Print Spooler. Maintenance was disabled and the original tool surface rechecked afterward. A subsequent Print Spooler test required an empty print queue and no running dependents, temporarily stopped the service, verified toolkit start and guarded rollback, then restored its original Running/Automatic state. A real Windows reboot also recovered the pinned-key SSH connection. These results validate the two service actions on that one PC; other editions and policies remain unvalidated. The collection-job controller has a separate process-crash recovery test; uncertain maintenance outcomes still require inspection and are not automatically recovered. Inventory and diagnostic live tests remain separate.
