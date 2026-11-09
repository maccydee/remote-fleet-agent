# Remote Fleet Agent

<img src="docs/brand/remote-fleet-logo-primary.svg" alt="Remote Fleet" width="300">

A laptop-side MCP toolkit for collecting Windows, macOS and Linux inventory and diagnostic evidence over approved OpenSSH access. Endpoint operations are read-only by default. Version 0.9 adds AI-assisted support cases: investigate, propose an exact repair, get separate engineer approval, execute and verify. The support workflow can run approved PowerShell or POSIX shell scripts; real administration is disabled until the engagement explicitly enables it. The separate legacy maintenance interface retains its three fixed actions. See [AI-assisted support](docs/AI-SUPPORT.md).

Version 0.6 adds explicit platform selection, a fictional seven-device demo and a disposable Linux Docker lab. macOS/Linux collection requires Python 3 and existing approved SSH access; Windows maintenance stays Windows-only.

Version 0.5 added an offline fleet dashboard, evidence reports, readiness probes, canary-gated maintenance batches, shareable summaries with reviewed retention, and revisioned manual evidence tracking. The packaged assessment installer and existing maintenance controls remain available. A job can finish with partial evidence; unknown results are never compliance passes.

## Try the simulated fleet

```sh
remote-fleet-agent demo --directory ./fleet-demo
```

Open `fleet-demo/dashboard.html`. It contains seven fictional Windows, Mac and Linux devices, including limited permissions and an offline host. The simulator opens no SSH connections and needs no Docker engine or private key. Its jobs are kept in a separate `simulation` subdirectory, even when a client name matches a real engagement.

For two real Linux containers, see [the cross-platform and lab guide](docs/CROSS-PLATFORM-LAB.md). Docker containers run Linux; macOS and Windows examples in the demo use fictional responses. Container evidence cannot establish a physical machine's firewall, disk encryption or service-manager behavior.

## Dashboard, reporting and manual evidence

```sh
remote-fleet-agent workspace dashboard --config fleet.local.json --private --output ./dashboard.html
remote-fleet-agent workspace report --config fleet.local.json --output ./shareable-report.html
```

Dashboards and reports use saved evidence without contacting a Windows host. Open the self-contained HTML in a browser; regenerate it after collection or manual imports. Reports are shareable summaries by default. Private exports include identities, raw evidence, notes and references. Manual appliance/cloud evidence can be entered before SSH setup, tracked by revision and imported from dashboard downloads.

[The operations workflow guide](docs/OPERATIONS-WORKFLOW.md) covers all six additions, exact commands, privacy boundaries, retention and batch recovery.

## What you can collect

Windows inventory has 12 selectable sections: operating system, hardware, machine-wide software registrations, disks, encryption status, network configuration, local users, administrators, firewall profiles, Defender, registered antivirus products and installed hotfixes.

Windows diagnostics has six sections: performance, stopped automatic services, recent System error metadata, selected reboot indicators, DNS configuration and storage. Event messages, document contents, passwords, hashes and recovery keys aren't collected. No external network probe is made.

Collections are bounded and return per-section status, counts and truncation markers. Some commands depend on Windows edition, modules and privileges. See [the workflow guide](docs/FLEET-WORKFLOW.md) for coverage limits.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Use authorised Windows hosts with OpenSSH already configured. Verify each host's SSH fingerprint at its console, and save the verified public host keys in a known-hosts file. Keep the engagement private key on the engineer's laptop.

Copy `fleet.example.json` to `fleet.local.json`, enter the verified host-key file and explicitly approved Windows usernames/addresses, and assign groups. Relative `known_hosts` paths resolve against the configuration file, not the current directory.

## Run a fleet job from the terminal

```sh
.venv/bin/remote-fleet-agent run-fleet \
  --config fleet.local.json --key-path /secure/engagement/id_ed25519 \
  --operation inventory --group office
```

Use `--hosts office-01 office-02` instead of a group for an explicit selection. Add `--sections os disks software` for a smaller inventory. Choose `--operation diagnostics` for diagnostics or `--operation readiness` for read-only access, module, disk-space and cleanup-expiry checks. The command waits for completion and prints the job ID, progress summary and saved evidence path. Exit 0 means collection completed; exit 2 means some hosts or sections were unsuccessful. Neither is a security certification result.

Jobs are stored under `~/.remote-fleet-agent/jobs/<client>/` by default. Override the root with `--job-dir`. Retry unsuccessful hosts using the original configuration, key and job root:

```sh
.venv/bin/remote-fleet-agent run-fleet \
  --config fleet.local.json --key-path /secure/engagement/id_ed25519 \
  --retry-job JOB_ID
```

## Connect an MCP client

Launch the stdio server with these environment variables set:

```sh
REMOTE_FLEET_AGENT_KEY_PATH=/secure/engagement/id_ed25519 \
REMOTE_FLEET_AGENT_CONFIG=/absolute/path/fleet.local.json \
  .venv/bin/python3 -m remote_fleet_agent.server
```

Optional `REMOTE_FLEET_AGENT_JOB_DIR` selects the job root. The server offers:

- `describe_capabilities`, `list_hosts`
- `inventory`, `diagnostics`, `readiness`, `run_check`, `fit_check`
- `start_job`, `get_job`, `get_host_result`, `list_jobs`
- `retry_job`, `cancel_job`, `compare_inventory`
- `fleet_status`, `export_evidence`
- `list_manual_evidence`, `add_manual_evidence`, `update_manual_evidence`
- `preview_retention`, `apply_retention`

`start_job` returns quickly so you can inspect progress while collection continues. Keep the server running. Terminal users should use `run-fleet`, which waits for their job to finish. A stopped server doesn't keep working in the background; interrupted work can be retried explicitly.

## Controlled maintenance

Keep `maintenance_actions` empty for audit work. To enable maintenance, list only the permitted action IDs in the engagement configuration: `start_print_spooler`, `start_time_service`, `clear_dns_cache`. Those actions become available for all approved hosts in that configuration. Keep narrower maintenance engagements if different hosts need different permissions.

Enabled fleet MCP servers additionally offer `preview_maintenance`, `get_maintenance`, `preview_batch`, `get_batch` and `cancel_batch`. These interfaces cannot execute endpoint changes. AI repairs use the workspace support MCP: the engineer reviews the exact devices and scripts, records approval in the workspace, then the AI requests execution. A plan digest binds content and never substitutes for human approval. Engineer-operated fixed maintenance remains available through the CLI. No maintenance actions run through fleet inventory jobs.

The CLI provides the same flow. See [the maintenance guide](docs/MAINTENANCE.md) for commands, uncertain outcomes and rollback limits. `REMOTE_FLEET_AGENT_AUDIT_ONLY=1` suppresses maintenance even when configured, in both the server and CLI.

## Scope and permissions

Every SSH operation resolves through the configured approved host list and uses the verified known-hosts file. The server doesn't scan the network or approve new targets. Select one configured group or a nonempty explicit host list for each job. Read-only queries need no repeated per-host confirmation after that selection.

The Windows SSH account still has its normal account privileges, typically administrator privileges for these queries. The application tool boundary doesn't turn that SSH key into a technically restricted read-only credential.

Configuration and evidence belong in private storage. Evidence may contain usernames, device addresses and application information. Keep real keys, deployment kits, evidence and client configurations out of public repositories.

## Stage the assessment installer

```sh
remote-fleet-agent init-client example --stage-dir ./example-kit --allowed-ip 192.0.2.10
```

This creates a private per-engagement key on the engineer laptop and stages the public key, bundled `Install-Assessment.ps1` / `Remove-Assessment.ps1`, matching TXT copies and a copyable installation command. The command runs from the kit directory in elevated Windows PowerShell. Replace the example address with the engineer laptop's actual IPv4 address. `--force` reuses an existing keypair and restages the kit; it never replaces that key.

The installer preserves the original state across repairs, scopes TCP/22 to the engineer address, verifies effective firewall coverage, and registers protected local SYSTEM cleanup before enabling access. The default expiry is three days with hourly retry opportunities for seven days. See [deployment and recovery](docs/DEPLOYMENT.md) for the lifecycle, validation evidence and remaining limits.

The earlier `scripts/install.ps1` and `scripts/uninstall.ps1` remain historical prototypes for comparison and tests. The CLI no longer stages them.

## Legacy modules

The file-triage and personal-data scanner modules remain in the codebase for future development and tests, but the MCP server doesn't register them. The audit-only environment switch suppresses the new maintenance tools; it cannot expose those legacy modules.

## Validation

```sh
.venv/bin/python3 -m pytest -q
```

Inventory has been tested live on one Windows PC. The Python suite covers inventory, maintenance, key staging, shared fleet concurrency and controller-crash recovery; PowerShell tests cover maintenance scripts and installer helpers. A live MCP test on the approved Windows test PC completed DNS cache clearing, verified an already-running service as a no-op, rejected execution replay and ineligible rollback, and restored the read-only tool surface. On 8 November 2026, a separate live MCP test started Windows Time, verified Running/Manual, then used guarded rollback to restore Stopped/Manual. An independent endpoint read confirmed the original service states, and replay and duplicate rollback were rejected. Further live tests on the same PC verified Print Spooler start/rollback with an empty queue, restored its original Running/Automatic state, installed and repaired the packaged assessment kit, authenticated a separate temporary key, ran SYSTEM scheduled cleanup, rejected the removed key, and restored the baseline. A reboot test confirmed SSH autostart, pinned-key reconnection and preservation of the original cleanup task. A 30-host simulated workload and real controller-process crash were also tested. These results do not establish fresh OpenSSH feature installation/removal, other Windows editions/policies or a real 30-machine rollout. No public release or licence selection is implied by this development milestone.

The version 0.5 workflow additions were validated offline using saved evidence, simulated hosts, local PowerShell syntax checks, MCP protocol tests and browser interaction. No new Windows tests were performed. The dashboard is a snapshot and no compliance score is generated.


## Live local workspace

Version 0.7 adds an authenticated local app with client/site navigation, device pages, reviewed collections, manual evidence edits, follow-ups, inventory baselines and comparisons, saved views, freshness, optional recurring collections and branded handover reports. Start with `remote-fleet-agent workspace-app init --directory ./fleet-workspace`, then `remote-fleet-agent workspace-app serve --directory ./fleet-workspace --open`. The initial clients are simulated. See [the workspace guide](docs/LIVE-WORKSPACE.md) for access policies, roles and limitations.


## AI clients and host operating systems

Version 0.8 adds provider-neutral MCP setup for Codex, Claude Code/Desktop, Gemini CLI, Cursor, VS Code and other stdio MCP clients. The engineer host may be macOS, Windows or Linux independently of the endpoint OS. `remote-fleet-agent ai-config` generates reviewable client snippets with native absolute paths; `remote-fleet-agent doctor --config ./fleet-workspace/northstar-demo/fleet.json --probe` validates the protocol with simulated devices. See [AI and host compatibility](docs/AI-AND-HOST-COMPATIBILITY.md) for setup commands and verification limits. Native Windows/Linux CI and interactive client runs are tracked separately from configuration/schema tests.
