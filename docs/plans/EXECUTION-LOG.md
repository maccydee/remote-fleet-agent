# Execution Log

One entry per assumption or blocker hit while executing
`2026-10-27-PLAN-remote-fleet-agent.md`.

## A-005 | Task 10 | assumption | OPEN
Plan's implementation sketch for `quarantine_move` collision handling used
a `while True` loop that re-checks `Test-Path` after every suffix bump
(unbounded collision resolution). The task's own test
(`test_quarantine_move_avoids_collision`) only mocks two `run_powershell`
calls (one `Test-Path` check, one move), which only fits a single-check,
single-bump implementation — the literal `while True` sketch would need a
third mocked call to re-verify the `-2` candidate before moving.
**Resolved:** implemented a single `Test-Path` check with one `-2` bump on
collision, no re-verification loop, matching both the test and the
behavioral contract as written ("a second call... gets a `-2` suffix").
**Residual risk:** if a quarantine folder named `<timestamp>` AND
`<timestamp>-2` both already exist (two prior runs in the same UTC second,
an extremely narrow race), the second `quarantine_move` would silently
reuse `-2` without checking it's also taken. Not fixed here since it wasn't
in the test's contract — flag for a real second look before relying on this
in an engagement where multiple quarantine operations might land in the
same second (unlikely in practice, since it requires human-triggered
confirmations each time).

## A-006 | Task 14 | assumption | RESOLVED
Plan's Task 14 placeholder (`server.register_tool(name)`) was explicitly a
guess at the `mcp` SDK's registration API, flagged in the plan itself as
needing adaptation to whatever version installs. Investigated directly:
the installed `mcp` package is 2.x, where the old low-level
`mcp.server.Server` takes callback functions in its constructor (no
decorator API), and `mcp.server.fastmcp.FastMCP` from v1 docs/tutorials no
longer exists - it was renamed to `mcp.server.mcpserver.MCPServer`, which
does have the expected `@server.tool()` decorator and an (async)
`list_tools()` matching the plan's test contract.
**Resolved:** built `server.py` on `MCPServer`, with each of the five
`@server.tool()` handlers resolving `host` through `ConnectionPool` before
calling into the corresponding `tools.*` function and returning
`dataclasses.asdict(...)` results. Verified with a real smoke test
(`list_tools()` returns exactly the five names) - see Task 14 in PLAN.md.
Also added a `key_path: str` parameter to `build_server()` and a `main()`
stdio entrypoint reading `REMOTE_FLEET_AGENT_KEY_PATH` from the
environment - the plan didn't specify how the server learns which
client's keypair to use at runtime, so this assumes **one server process
per client engagement**, matching the "one keypair per client" design
decision. Multi-client-in-one-process was never a requirement, so this
wasn't engineered for.

## A-007 | Task 21 | assumption | RESOLVED
Pester 6's `Mock` requires the target command to already resolve via
`Get-Command` before it can attach to it. `Unregister-ScheduledTask`,
`Get-NetFirewallRule`, `Remove-NetFirewallRule`, and
`Disable-WindowsOptionalFeature` ship in Windows-only modules
(ScheduledTasks, NetSecurity, DISM) that don't exist on this (macOS) dev
machine, so the plan's test file as written couldn't run here at all - not
a Pester 5-vs-6 syntax gap, a missing-module gap.
**Resolved:** `scripts/uninstall.ps1` opens with four
`if (-not (Get-Command <name> -ErrorAction SilentlyContinue)) { function <name> {...} }`
guards that define no-op stubs for exactly those four cmdlets, and only
when the real one isn't already present. On real Windows, where all four
resolve from their native modules, the guard body never runs and the
stubs are never defined - genuine cmdlets are called as-is. Verified by
reading the file directly (not just the subagent's report) and confirming
the guard condition and that no caller-visible behavior changed. The same
pattern will be needed in `scripts/install.ps1`'s own Pester run if it
mocks any of the same Windows-only cmdlets (`Enable-WindowsOptionalFeature`,
`New-NetFirewallRule`, `Register-ScheduledTask`, etc.) - check its report
for whether it hit the same issue.

## A-008 | Tasks 20-21 | skeptic finding | FIXED
Post-build skeptic pass over the actual merged code (not just the design)
caught a real bug: `uninstall.ps1`'s `Remove-EngineerKey` called
`Clear-Content` on the whole `administrators_authorized_keys` file.
That file is shared across every local administrator account, and the
design itself anticipates SSH sometimes being enabled before this tool
ever runs (`ssh_was_already_enabled: true`) - meaning other, unrelated
keys could already be in that file, and uninstall would have destroyed
them along with ours.
**Fixed:** `install.ps1`'s `Invoke-Install` now records the exact public
key line it added as `ssh_key_added` in `state.json` (read-modify-write
after `Add-EngineerKey`). `uninstall.ps1`'s `Remove-EngineerKey` now takes
`-KeyContent` and removes only the matching line via
`Where-Object { $_ -ne $KeyContent }`, doing nothing if no key was
recorded (e.g. a state file predating this fix). Added a real
(non-mocked) `Remove-EngineerKey` test proving another admin's key
survives removal, and an `Invoke-Install` test proving the key gets
recorded - both confirmed RED against the original code before the fix,
then GREEN after. 18 Pester tests total now (was 15), 40 Python tests
unaffected.

## A-009 | Task 20 | skeptic finding | FIXED
Same post-build skeptic pass caught a second, more serious bug:
`Invoke-Install` pointed the self-destruct scheduled task at
`(Join-Path $PSScriptRoot "uninstall.ps1")`. `$PSScriptRoot` is wherever
`install.ps1` was launched from - per the README's own workflow, that's
the USB stick, run directly from its mount point. The scheduled task
fires 3 days later, almost certainly after the engineer has moved on and
the stick is unplugged - `powershell.exe -File "<gone-drive>:\uninstall.ps1"`
would silently fail to find anything to run. The entire self-destruct
dead-man's-switch, the feature specifically added in response to the
"forgotten uninstall leaves a permanent backdoor" skeptic finding during
design, would not have actually worked.
**Fixed:** `Invoke-Install` now copies `uninstall.ps1` into the same
local `C:\ProgramData\remote-fleet-agent` state directory it already
creates (`Copy-Item` from `$PSScriptRoot` to that local path), and points
`Register-SelfDestructTask` at the local copy, never `$PSScriptRoot`.
Added a test asserting the exact local path is what's passed to
`Register-SelfDestructTask`. 19 Pester tests total now.

## A-010 | Task 22 | manual verification finding | FIXED
First manual run on a real Windows machine (2026-11-03) surfaced three
real problems in `Enable-OpenSSHServer`, in sequence:
1. `Enable-WindowsOptionalFeature -FeatureName "OpenSSH.Server"` silently
   no-oped - OpenSSH Server is a Windows **Capability**, not an Optional
   Feature, so the cmdlet was wrong from the start. `Get-Service sshd`
   reported "Cannot find any service" after install.ps1 claimed success.
2. After switching to `Add-WindowsCapability -Online -Name
   "OpenSSH.Server~~~~0.0.1.0"`, a direct `dism /online /Add-Capability`
   call with the exact capability name (pulled from `Get-WindowsCapability`
   itself, so not a typo) still failed once with **Error 87: "A Windows
   capability name was not recognized"** - then the identical command
   succeeded immediately on retry with nothing else changed. Confirmed
   transient, not a permanent per-build gap (worried at the time this was
   an Insider/Canary-build limitation, since `DownloadSize`/`InstallSize`
   both showed 0 - that turned out to be a red herring for this machine).
3. `Add-WindowsCapability` installing the capability does not start the
   service or create the firewall rule - those are separate steps the
   script already did, but running DISM by hand to debug (1) and (2) left
   the capability installed without the rest of `Invoke-Install` having
   run against it.

**Fixed:** `Enable-OpenSSHServer` now retries `Add-WindowsCapability` up
to `-MaxAttempts` (default 3) with `-RetryDelaySeconds` (default 5)
between attempts, checking `Get-WindowsCapability` state after each one
rather than trusting a single call. Added an optional `-LocalSourcePath`
parameter (threaded through `Invoke-Install`) so an engineer can point
`-Source` at a local `sxs` folder from Windows install media when a
client network blocks Windows Update/WSUS for Feature-on-Demand content -
deliberately still Microsoft-signed content from a different source, not
a bundled third-party `sshd.exe`, which would reintroduce the exact
EDR/AV-flagging risk the design dropped a custom agent to avoid (see
DESIGN.md). Also added a post-`Start-Service` verification: waits 2
seconds then re-checks `Get-Service sshd`, throwing if it isn't
`Running`, since a service can report started and then exit immediately
(e.g. host-key generation failing) - `Start-Service` succeeding is not
proof the service stayed up. All failure paths now throw a specific,
actionable message instead of letting the script print "Install
complete" when something didn't actually work. 5 new Pester tests
(retry-then-succeed, local-source passthrough, post-start crash check,
throws-after-exhausting-retries, plus the existing bad-capability-state
test kept). 23 Pester tests total now, 40 Python tests unaffected.
**Residual:** if a machine's build genuinely has no published OpenSSH
package anywhere (the Insider/Canary case first suspected here, not yet
confirmed to actually occur), retries cannot fix that - the thrown error
says so and recommends a different machine. No amount of client-side
retrying can install a package Microsoft has not published.

## A-011 | Task 22 | manual verification finding | FIXED
Immediately after A-010 shipped, the next live run appeared to hang with
the terminal completely unresponsive (no output, Ctrl+C eventually
needed). Root cause: A-010's retry loop still called
`Add-WindowsCapability` unconditionally on every run, even when
`Get-WindowsCapability` already showed `State: Installed` (true here,
since the capability had been installed by hand a few messages earlier
while debugging A-010). Asking `Add-WindowsCapability` to re-verify an
already-installed capability still tries to contact Windows Update on
this machine's flaky network path, and that call had no timeout and
printed nothing while it sat there - indistinguishable from a true hang
from the engineer's side.
**Fixed:** `Enable-OpenSSHServer` now checks `Get-WindowsCapability`
*before* attempting anything, and skips `Add-WindowsCapability` entirely
when it already reports `Installed` - the retry loop only runs at all
when there's actually something to install. Also added `Write-Output`
progress lines before each potentially slow step ("Checking OpenSSH.Server
capability...", "Installing OpenSSH.Server capability - this can take a
minute or two...", "Starting sshd service...", "sshd service is
running.") so a long-but-working step is never visually indistinguishable
from a genuine hang again. One new Pester test
("skips Add-WindowsCapability entirely when the capability is already
installed"), plus the existing success-path test fixed to actually
exercise the NotPresent-then-Installed transition it claimed to test
(it had been passing for the wrong reason - the capability mock returned
Installed from the first call, so the retry loop was never really
exercised at all). 24 Pester tests total now, 40 Python tests unaffected.
**Not done:** a hard, unkillable-proof timeout around
`Add-WindowsCapability` itself (e.g. via a background job) was
considered and rejected for now - it would make the DISM call untestable
against Pester's in-process mocking (a background job can't see mocked
functions defined in the parent session) and would buffer all progress
output until the job finishes, defeating the live-feedback fix just
made. Ctrl+C remains the escape hatch for a true hang on a genuine
first-time install against a bad network; the progress messages now make
clear when that's actually necessary versus when it's just slow.

## A-012 | Task 22 | manual verification finding | FIXED
Triggering `uninstall.ps1` remotely over SSH (Invoke-Install already
copies it to `C:\ProgramData\remote-fleet-agent\uninstall.ps1`, so this
is a real, useful path - no need to be at the keyboard to tear down) hung
indefinitely. `Invoke-Uninstall` runs key removal, firewall rule removal,
then scheduled task removal, then (unconditionally, when
`ssh_was_already_enabled` was false) `Remove-WindowsCapability` with no
`-ErrorAction`, no check-first, and no progress output - the exact same
unprotected-DISM-call shape already fixed on the install side in A-010/
A-011, just never mirrored on the uninstall side. Confirmed live: the
firewall rule was already gone (first two steps completed) while `sshd`
still showed Running (capability removal was the step actually stuck),
and the backgrounded SSH process was still alive on the Mac side minutes
later holding the hung remote session open.
**Fixed:** `Invoke-Uninstall` now checks `Get-WindowsCapability` first
and skips `Remove-WindowsCapability` entirely when already `NotPresent`,
with `Write-Output` progress lines before each step, matching the
install-side pattern exactly. Also wrapped the actual removal call in
try/catch so a genuine failure there doesn't prevent returning the
receipt for the higher-value steps (key, firewall rule, scheduled task)
that had already succeeded - previously an exception here would have
silently dropped the whole receipt even though most of the teardown had
worked. 3 new Pester tests (skip-when-absent, resilience to a removal
error, and the existing "disables OpenSSH" test fixed to actually mock
`Get-WindowsCapability` - it had been relying on the Windows-only stub's
default `$null` return, which happened to make `-eq "Installed"` false
and would have silently skipped removal even in the success-path test
without ever failing it). 26 Pester tests total now, 40 Python tests
unaffected.
**Not yet re-verified live:** the hung remote PowerShell session this
exposed was still running when the fix landed - killed the local SSH
client process from this end, but the remote `uninstall.ps1` invocation
on the Windows machine may still be sitting there. The machine currently
has no SSH access (firewall rule already removed by the time it hung),
so confirming the fix needs physical access: Ctrl+C or close the hung
PowerShell window, then re-run the (also-fixed) `install.ps1` to restore
access and refresh the locally-copied `uninstall.ps1`, then retry
uninstall.

## A-013 | 8 November 2026 | Packaged assessment lifecycle and validation

The supported setup path now stages `assessment/Install-Assessment.ps1` and
`Remove-Assessment.ps1` from package resources, with TXT copies, an engagement
name and a single engineer IPv4 address. The earlier prototype scripts remain
historical and are not staged. Restaging reuses an existing key instead of
replacing it. The scripts are included in built wheels and source distributions.

The assessment kit preserves the original baseline across repairs, writes it
atomically, validates effective firewall coverage and registers protected local
SYSTEM cleanup before access is enabled. Scheduled cleanup uses an encoded
launcher and saves launch diagnostics where storage permits. The first live
scheduled launch failed without a receipt; cleanup was diagnosed explicitly and
the revised launcher then passed the SYSTEM task lifecycle test.

On the existing Windows 11 test PC: first/repeat installation, separate-key
login, baseline preservation, SYSTEM cleanup, temporary key rejection and exact
baseline restoration passed. Both fixed service actions passed start and guarded
rollback tests. DNS clearing and no-op/replay guards passed. A real reboot
verified SSH autostart, pinned-key recovery and preservation of the original
assessment cleanup task. The old assessment access remains available under its
original lifecycle; no client machines were contacted.

A simulated 30-host workload checks the shared concurrency limit, retained
successes and selective retries. A real controller subprocess crash checks
interrupted-host recovery without silently replaying completed work. Packaging
checks stage a kit from the wheel rather than a source checkout.

Remaining validation requires fresh Windows feature installation/removal,
additional Windows versions and centrally managed policy, and a real multi-PC
rollout. Underlying Windows servicing still has no hard cancellation guarantee.
A public repository, copyright/licence selection and publication remain separate.

## A-014 | 8 November 2026 | Offline engagement workflow

Version 0.5 adds a saved-evidence dashboard and report export, fixed readiness
queries, explicit canary/continuation batch maintenance, allowlist-based shareable
summaries, reviewed managed retention, and manual evidence with revision history.
The offline workspace works before SSH pairing. Remote operations still require
approved hosts and pinned keys. No new Windows query or change was performed in
this phase: tests use saved evidence, disposable stores, mocked hosts, syntax
checks, a real local MCP exchange, and browser download/import verification.

Manual and maintenance journals are retained by default. Retention previews bind
exact file hashes and are rechecked for new references before deletion. Batches
never auto-retry or roll back uncertain outcomes. The dashboard is a regenerable
snapshot, with observed connection timestamps rather than a live-host claim.


## 2026-11-08 — Cross-platform collection and isolated labs (0.6)

Added explicit windows/macos/linux host platforms and per-device SSH ports. Unix inventory, diagnostics and readiness use fixed, bounded Python 3 probes over pinned SSH; missing prerequisites remain unknown. Windows-only legacy and maintenance tools reject Unix and simulated devices before connecting. Maintenance and batch previews now bind the SSH port and platform.

Added a seven-device fictional fleet with healthy, attention, limited and offline scenarios. The simulator blocks SSH, excludes endpoint maintenance and writes to a separate simulation evidence store. A local Docker lab provisions two Ubuntu SSH endpoints with lab-only keys, loopback-only ports, pinned host identities, read-only filesystems, resource limits and an SSH health check. Docker templates exclude credentials from the build context.

Native Mac read-only probe smoke checks and a real Linux-container SSH collection supplement the unit/fixture tests. Neither enables Mac Remote Login nor changes Windows or client endpoints. macOS/Windows Docker examples remain simulations; container checks cannot establish physical-disk encryption or endpoint-protection health.


## 2026-11-08 — Operations/admin plan, batch 1 starts

Executing tasks 1–3 of 2026-11-08-PLAN-operations-and-administration.md. Baseline: 250 passed, 1 skipped; existing modified/untracked files preserved in a private source snapshot and diff under the Codex task workspace/work/operations-execution. No endpoint changes. Commits held until batch review.

### A-OPS-001 | Task 1 | assumption | OPEN
The executing-plans skill names haiku/sonnet model tiers unavailable in this runtime. Delegate the ordinary fixture task to the available inherited model; implement security-sensitive projection/API tasks in the orchestrator. Preserve the skill's independent verification and sequential file ownership.

### A-OPS-002 | Tasks 1–3 | assumption | OPEN
Task 1 is fixture extraction, so its example case-creation assertion already passes on the old code. Verify its substantive migration/credential-isolation contracts; do not fabricate a failing production behaviour for fixture refactoring. Task 2's missing operations behaviour and Task 3's missing authenticated route supply real RED assertions. Native skip infrastructure stays in task 18.

### A-OPS-003 | Task 2 | assumption | OPEN
Global load_registry validates all client configs together, conflicting with the partial-source read contract. Operations enumerates current registry entries independently and uses the startup identity cache only to label a now-broken source. Current file contents and live grants determine scope. The shared select_permitted helper supplies both selected() and Operations device filtering. Existing mutation/API registry validation remains unchanged. Projection regressions are in tests/test_operations_projection.py to avoid fixture task file overlap.

### Batch 1 progress
Task 1 fixture extraction independently reviewed. Task 2 projection RED: 20 failures on missing LiveService.operations; GREEN: 20 passed, including 151 cases, partial permitted source, cross-client/site denial and current actor revocation. Task 3 real HTTP RED: authenticated GET returned 404 instead of 200; route and strict filter parsing added. Combined regression verification in progress. No endpoint access or provider configuration changed.

### Batch 1 verified — tasks 1–3

- Shared simulated fixtures preserve legacy tuple callers, real roles and persisted repair digests. Explicit forbidden SSH regression confirms endpoint access is blocked in these fixtures.
- Operations projection and GET /api/operations compute permitted all-client/site summaries, bucket counts, full-history pagination, safe partial-source notices and existing collection schedules. Host selection is shared with the existing service.
- Strict query parsing rejects unknown/repeated/empty numeric filters, excessive limits, bad offsets and unsupported platforms. Real HTTP tests retain Host/Origin/session/CSRF enforcement and reject POST mutation. Identity consistency race tested RED then GREEN.
- Combined targeted tests: 95 passed before the additional identity-race regression. Final full suite: 298 passed, 1 skipped in 25.79s. Changed files and all src pass Ruff. Full tests Ruff has four pre-existing import-order errors in test_collectors.py, test_connection_pool.py, test_fleet_config.py, test_fleet_runtime.py; each file is byte-identical to baseline.
- Substance proof: three fictional cases produce owner_total=3, attention=1, approval=1, open=1, next_offset=1 at limit=1. A head-office-only Northstar technician sees exactly one case and no Harbour or warehouse records. No hardcoded empty feed.
- Review artifacts: task workspace/work/operations-execution/batch-1-only.diff and batch-1-substance-proof.json. No commits or staging: preserve pre-existing untracked baseline and hold commits for batch review. No visible UI changes yet; next task is navigation/request-state separation.


### A-OPS-004 | Tasks 12, 15 | approval consistency | DECIDED
Final interface review found the legacy fleet MCP could still execute fixed maintenance/batches from a model-supplied digest. Keep its read-only previews/history, remove four endpoint mutation tools from that interface, and direct AI execution through the authenticated workspace support bridge. The engineer-operated legacy CLI remains explicitly opt-in. Recheck global audit-only mode in the maintenance manager itself. This closes an approval bypass rather than adding an exception to the new policy.


## 2026-11-08 — Remaining implementation and final verification

User requested completion without feedback pauses, overriding the skill's between-batch wait. Existing baseline and unrelated work preserved; no publishing, commits or blanket staging. Independent UI, native harness, portability and adversarial review work was checked and integrated.

Implemented Operations oversight, permanent case/device/review routes, target-confirmed privileged investigations, immutable bounded cross-platform repair plans, durable step intent and endpoint exclusion, checks and conditional recovery, trial/readiness/window/cancellation handling, controller lease fencing, full paginated execution history, workspace human decisions, health/drain and provider-neutral MCP tools. Fixed legacy MCP approval bypass by removing four endpoint mutation tools while retaining previews/history and explicit engineer CLI. Audit-only is enforced at the maintenance manager. Interrupted diagnostic dispatch can be reconciled only after its owning claim is no longer live, with a human observation; original uncertainty is retained.

Final adversarial regressions were observed failing before fixes: legacy MCP discovery/manager audit-only, archived diagnostic reconciliation, interrupted dispatch reconciliation. All now pass. Full suite: 382 passed, 11 skipped in 42.62s; final browser suite 26 passed in 35.60s. Actual final installed wheel: 83 passed on macOS (9.78s), 83 passed in isolated Linux container (21.14s). Ruff and JavaScript syntax pass. Earlier sandbox-only suite failures were socket permission errors, followed by the socket-enabled final run; one stale legacy tool-count expectation was updated from 30 to 26 to match removal of execution tools. No type checker is configured.

Native Windows pinned read-only preflight passed. Two real Docker Linux endpoints completed read-only collection with expected container limitations; bad pins were rejected. Mac native SSH TCP22 refused. Native mutation tests were NOT_RUN: no fabricated human decision or automatic enrollment was introduced. Ten opt-in native skips and one Windows ACL skip are release gaps, not passes. Windows controller and interactive provider sessions are NOT_RUN. Lab-specific containers/network were removed; evidence retained.

Final fictional marketing capture: 10 inspected PNGs plus an actual 23.72-second WebM, complete playback verified, all simulated and private pending publication review. No native/provider footage claimed. The local fictional preview was restarted on 127.0.0.1:8880. Final verification manifest, installed wheel, source delta and private gallery are under the task workspace outputs/operations-release and outputs/operations-showcase. Plan: 18 complete, 5 blocked on native/provider evidence and associated footage, with implementation complete.
