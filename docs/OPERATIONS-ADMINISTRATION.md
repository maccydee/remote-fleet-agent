# Operations and approved administration

Engineers converse in their chosen AI client. Remote Fleet provides device access, review controls and the shared support record. Operations is the workspace home, with all permitted clients selected by default. Devices, Approvals, History and Settings provide the rest of the workflow. Existing evidence, comparison, reports and collection schedules remain available through client tools.

## Investigate a device

Use `request_investigation` to select explicit enrolled devices. The returned review link names each client, device, platform and SSH account. An owner or scoped manager confirms that target set in the workspace. The AI then uses `run_investigation_command` for bounded PowerShell or POSIX shell commands. Confirmation is reused for that investigation; a changed target, account, port, key reference, pinned identity or site requires a fresh confirmation. Sessions close explicitly or expire after eight hours.

Arbitrary privileged diagnostic code can change state. The tool enforces identity, scope, session and access policy; it cannot prove a script is read-only. Deliberate repairs belong in a separately approved plan. Audit-only mode and disabled real administration block custom administrative scripts while retaining the fixed evidence tools.

## Review and execute a repair

`propose_repair_plan` submits schema 1: named targets, diagnosis and uncertainty, impact, an execution window with timezone, disruption policy, trial device, batch ceiling, stop threshold and exact executable steps. Each step names its platform, targets, command and timeout. Conditional steps reference earlier approved checks; recovery requires an explicit known condition. Package-dependent changes reference reviewed version/hash checks. Windows steps include a reviewed terminating-error preference.

An engineer reviews and approves the exact digest. `execute_repair_plan` queues that approved revision. The first target is the trial; a failed or unknown trial always stops the rollout. Dispatch is currently sequential within the approved batch ceiling. Optional readiness gates pause before disruption or after a successful trial. Scheduled work proceeds only while the controller is running and reachable.

Command completion, post-change checks and confirmation that the user's issue is resolved remain separate. An Outlook process check does not prove the application works on the affected desktop. Driver installation does not establish a physical print result.

## Interruptions and history

Cancellation stops future dispatch; it does not promise to undo a command already sent. A lost SSH connection or interrupted controller leaves an uncertain result. Automatic replay is blocked. Only an authorised engineer can reconcile that result, with a recorded observation; a command still owned by a live worker cannot be reconciled away. Reconciliation preserves the original uncertainty and never authorises a new repair.

Device exclusion is shared with the original support and maintenance interfaces. Production locks default to the controller account's persistent `~/.remote-fleet-agent/endpoint-locks` directory. All controller processes under that account use the same namespace; distinct independent controller accounts require coordinated deployment rather than an assumption of distributed exclusion. Configure an absolute persistent directory with `REMOTE_FLEET_ENDPOINT_LOCK_DIR` when needed. Keep it private and available across restarts. Never clear uncertain locks blindly.

The controller stores a persistent device UUID and its verified address/key aliases in `device-identities.sqlite3` alongside the locks. Different workspaces, SSH accounts and overlapping host-key lists share that ID. A key rotation at an existing enrolled address retains the ID while changed pins still invalidate earlier approvals. No new service or installation is needed on the device.

Different addresses with entirely disjoint verified key sets cannot establish identity automatically. Set the same `device_id` UUID on those approved host entries after verifying that they identify the same machine. An automatically assigned ID appears in real target review records; reuse that existing value when adding aliases. A fresh device can be given a generated UUID before its first registration. Conflicting existing IDs block administration and require enrollment review; the controller does not merge them while work may be active. Copied SSH server keys conservatively serialize the associated registrations.

Stop all old controller processes before upgrading device locking. Outstanding old lock files remain effective and block new aliases. Reconcile the original work through its saved target record before removing a lock; an unknown result remains unknown. The new identity field requires fresh review of existing real investigation sessions and plans. Back up the identity registry with the workspace, and retain the lock directory when restoring either one.

Full new diagnostic and execution history is retained in SQLite. Detail responses show recent entries; authenticated `/history?offset=0&limit=10` routes page through the remainder. Historical client/site permissions apply to both summaries and full records.

## Controller and AI clients

The same Python controller supports Windows, macOS and Linux. It stays bound to loopback. For an always-on self-hosted deployment, use a dedicated account and verified SSH local forwarding, retaining application login. Keep the forwarded local origin consistent with the configured controller origin. Native GUI/session helpers and a public HTTP service are separate work.

Queue defaults are 64 runs and four active workers. `REMOTE_FLEET_MAX_QUEUE` and `REMOTE_FLEET_MAX_ACTIVE` accept validated bounds. Health is available to the owner at `/api/health`. Logs record lifecycle and correlated execution transitions without credentials or script bodies. First administration migration creates a private database backup. Restore into a separate directory and reconcile uncertain endpoint work before resuming.

Provider-neutral MCP exposes request, read, proposal and approved-execution operations. Human decisions use the authenticated workspace. Claude, Codex, Gemini and other generated client formats use that fallback; a provider-specific human approval channel is not claimed without its own identity/binding verification. Existing client settings are not changed automatically.

These roles separate API workflows. An AI process with filesystem access as the controller's OS account can also read that account's stored owner token or SSH key. Use separate OS identities or hosts, with restricted file access, when independent approval must withstand compromise of the AI process. Target confirmation permits arbitrary diagnostic scripts with the SSH account's privileges; those scripts can change state. Separate repair approval does not turn that investigation session into a read-only sandbox.

## Validation evidence

See the execution log and release evidence for actual results. Tests use simulated devices, fake transports and actual local HTTP/SDK sessions to verify the enforcement boundaries. Read-only checks reached the enrolled Windows test PC and two pinned Linux Docker SSH devices. This Mac's SSH target prerequisite is blocked while Remote Login is unavailable. Native repair/recovery, Outlook, printer drivers, GUI outcomes and unattended OS-service installation remain separate validation gates; simulation is not evidence that they ran.

The legacy fleet MCP retains fixed-action previews and history but no longer exposes endpoint execution, rollback or batch continuation. AI changes go through the workspace support MCP and its human decision record. The explicitly operated maintenance CLI remains available for engineers; global audit-only mode blocks that manager too.
