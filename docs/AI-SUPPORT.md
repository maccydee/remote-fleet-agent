# AI-assisted support and approved repairs

Version 0.9 adds a support workflow shared by the local workspace and a dedicated MCP server. An engineer can ask an AI to investigate a device, propose a repair, wait for approval, execute the approved plan and report its checks. The existing inventory MCP server and legacy maintenance actions remain separate interfaces.

## Work through a case

Open **Operations** in the workspace, or a device’s support history, and create a case against one enrolled device. Describe the problem and select an investigation profile:

| Profile | Evidence |
| --- | --- |
| Outlook, Windows | Outlook processes and session IDs, Click-to-Run installation information, recent Application event metadata, SSH and interactive account context |
| Printing, Windows | Spooler state, installed queues, printer drivers, port configuration and bounded print-job metadata |
| General, Windows | OS, process and service metadata |
| General, macOS/Linux | OS, execution identity, disk usage and process names |

These are fixed read-only investigations. Missing modules, inaccessible records and empty results do not establish a cause. Outlook event metadata is from the Application log and may concern other applications. The profiles do not inspect mailbox content or print-job document names. The SSH session can differ from the affected user's desktop and account.

The AI reads the case evidence and proposes a repair containing:

- The named device, SSH target and platform.
- Its diagnosis, remaining uncertainty and expected disruption.
- The exact PowerShell script on Windows or POSIX shell script on macOS/Linux.
- A separate, exact verification script and a timeout for each step.
- Recovery instructions and limits.

The script facility supports administrative work beyond the original three fixed fixes. Its authority comes from the enrolled SSH account. It does not automatically elevate that account, supply interactive credentials or place a process into another user's desktop session.

An engineer with an **owner** or appropriately scoped **manager** session reviews the full plan in the workspace and clicks **Approve this exact repair**. A technician cannot approve through the HTTP API or MCP. The AI tools expose no approval, rejection or issue-resolution operation. Approval authorizes both scripts; the verification script also needs review because arbitrary code cannot be proved read-only by labelling it a check.

After approval, the AI or engineer can execute the plan once. A digest binds its scripts, scope, evidence and current access configuration. Plans expire within 30 minutes of their investigation evidence; stale evidence requires a fresh investigation. A changed proposal needs a fresh approval. Disabling the approver, removing their scope, changing the engagement or enabling audit-only mode blocks later execution. An engineer can reject or revoke an approved plan before execution starts. An operation already sent to an endpoint may continue.

The repair and verification run in the background; poll the case or use **Refresh case**. Output is bounded and truncation is reported. Repairs are not retried automatically. Host locks coordinate support repairs with legacy maintenance using the same job store, but cannot coordinate unrelated tools, other administrators or different stores.

## Interpret the outcome

| State | Meaning |
| --- | --- |
| `awaiting_approval` | A saved plan exists; no repair has been executed |
| `approved` | An authorised engineer approved this exact plan |
| `executing` | Intent was saved before sending the repair; it may have run |
| `execution_failed` | The repair returned a failing exit status; partial changes may remain |
| `verification_failed` | The repair returned zero, but its defined check did not establish success |
| `checks_passed` | The defined check passed; confirm the actual user outcome |
| `unknown` | The controller cannot establish the outcome; investigate before proposing another repair |
| `resolved` / `issue_persists` | An engineer recorded the observed user outcome |

Verification must return exit zero and emit one JSON object with a boolean `passed` field, for example `{"passed":true,"detail":"Spooler is running"}`. String values such as `"true"`, malformed JSON and truncated verification output do not count as success. A script returning zero alone does not establish that Outlook opens or a physical page prints.

PowerShell plans display an added `$ErrorActionPreference='Stop'` prefix before approval. Native programs still need explicit checks of their exit codes. Recovery text does not trigger automatic rollback; a recovery operation requires its own reviewed plan.

After an interrupted controller, an `executing` record or host lock may remain. Treat it as uncertain. Inspect the endpoint and establish that the original controller is no longer running before manually clearing a stale lock. There is no automatic crash recovery, lock expiry or replay. Prior investigations, plans, approvals and outcomes are retained in the private case record. Local file/database owners can alter this storage; it is not an independent immutable audit service.

## Connect an AI client

Keep the local workspace running. In its **Operators** page, use the owner session to create a separate **technician** operator for the AI, limited to the intended clients and sites. Save that technician token privately outside the repository. Never give the support bridge the engineer's owner or manager token. The bridge refuses those roles.

On the engineer host, launch the provider-neutral stdio server with that host's Python environment:

```sh
python -m remote_fleet_agent.support_mcp \
  --workspace-url http://127.0.0.1:8877 \
  --operator-token-file /private/path/ai-technician-token.txt
```

Use `python3`, the virtual environment's Python, or Windows `py -3` as appropriate. The token file can contain the plain token or a JSON object with a `token` field. Protect it with private filesystem permissions. The bridge talks only to a loopback HTTP origin; it does not expose the app to the network or provide a cloud relay.

Generate mergeable configurations for Codex, Claude Code, Claude Desktop, Gemini CLI, Cursor, VS Code and generic MCP clients:

```sh
python -m remote_fleet_agent.support_mcp \
  --workspace-url http://127.0.0.1:8877 \
  --operator-token-file /private/path/ai-technician-token.txt \
  --write-configs ./support-ai-setup
```

Choose an empty output directory. These profiles contain the current Python path and token-file path, with no token contents. Generate them on the host where the AI client executes. Merge only the `remote-fleet-support` entry into the existing client settings, then reconnect/restart the client. Generation does not modify those settings. Interactive provider-specific and native Windows/Linux host verification remain pending.

The seven support tools are `support_workspace`, `list_support_cases`, `open_support_case`, `investigate_support_case`, `get_support_case`, `propose_support_repair` and `execute_support_repair`. Tool results include a direct engineer-review URL for the case. Read each returned revision before the next change; stale revisions are rejected.

A useful first instruction to Claude is:

> Use Remote Fleet support to investigate why Outlook will not open on the enrolled device. Read the evidence before proposing a repair. Explain your uncertainty and the effect on the user's work. Give me the engineer-review link and wait for workspace approval. Execute only the approved plan, report its checks, and ask me to confirm whether Outlook now works.

The controller supplies tools, records and enforcement. The connected AI supplies the reasoning. Creating a case in the browser does not start an embedded autonomous AI agent.

## Enable real administration deliberately

Existing engagements remain unchanged and real administration is disabled by default. To enable it for an authorised engagement, add the boolean field to its private fleet configuration:

```json
"administration_enabled": true
```

The workspace entry must also allow collection, identify the approved SSH key and retain a current authorisation reference and any applicable access expiry. Ensure that authorisation covers administration, not just evidence collection. Known host keys remain mandatory for real access. Refresh/reconnect after changing configuration and obtain new approvals; old plans are invalidated.

`REMOTE_FLEET_AGENT_AUDIT_ONLY=1` blocks repair proposals/execution in the workspace controller and in the support bridge process. Fixed investigations remain available within the existing collection policy. The legacy `maintenance_actions` setting does not enable arbitrary support scripts; it controls only the original maintenance interface.

The separation is enforced through application roles and distinct credentials. It does not protect against a local administrator who can read the engineer's token, change configuration or edit the database. Protect the engineer session and approval credentials accordingly.

## Outlook and printer repairs

An Outlook process termination can lose unsaved work. A proposed script should identify the affected process/session from evidence and recheck its identity before acting. Closing a process is not an Office repair or proof that a mailbox/profile works. Opening Outlook in the user's desktop still requires an appropriate session mechanism or user confirmation.

A printer-driver repair can be proposed as an exact reviewed PowerShell script. Identify the precise printer/driver, trusted package source and expected version. Pin and recheck the package hash if a file is involved, preserve the queue/port settings, and make disruption explicit. Check native installer exit codes. The toolkit does not automatically source drivers, infer that reinstalling is necessary, or provide a tested universal driver-reinstallation recipe. The engineer must review the proposed package and commands. A driver command succeeding still requires a print test.

Microsoft references for the underlying Windows investigation/administration surface: [Get-PrinterDriver](https://learn.microsoft.com/powershell/module/printmanagement/get-printerdriver), [Get-Printer](https://learn.microsoft.com/powershell/module/printmanagement/get-printer), [Add-PrinterDriver](https://learn.microsoft.com/powershell/module/printmanagement/add-printerdriver).

## Validation boundary

The simulator exercises the full case, approval, execution, check and confirmation sequence without opening SSH or interpreting submitted scripts. Its synthetic success is a workflow demonstration only. Automated tests cover role/site/client separation, denied AI approval, stale revisions, plan/policy changes, revocation, cross-controller claims, bounded output, cancellation and uncertain outcomes, plus a real SDK stdio connection through the authenticated workspace API. Native Outlook repairs, driver installation and the new general script transport still require supervised endpoint tests. Existing live tests of the three legacy actions do not validate these new repairs.

## Operations and versioned plans

The new workspace leads with Operations, Devices, Approvals, History and Settings. Target-confirmed custom investigation and versioned multi-step repair plans are described in [Operations and approved administration](OPERATIONS-ADMINISTRATION.md). The original exact-script workflow above remains available for existing cases. Validation gaps remain visible in the release evidence.
