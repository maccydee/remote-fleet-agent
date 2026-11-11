# Local fleet workspace

The local app opens on Operations across all permitted clients. Use your chosen AI client for conversations; use this workspace to oversee investigations, review repairs, approve changes and inspect outcomes. Device evidence, reviewed collections, manual records, follow-ups, comparisons, collection schedules and reports remain available. The dashboard listens on **127.0.0.1 only**. Owner-created Mac and Linux kits temporarily open a separate certificate-pinned listener on the chosen private network address. It is a local engineer workspace, not a hosted remote-access service.

## Start with fictional devices

```sh
remote-fleet-agent workspace-app init --directory ./fleet-workspace
remote-fleet-agent workspace-app serve --directory ./fleet-workspace --open
```

Initialization creates two clearly labelled fictional clients with Windows, macOS and Linux devices across head-office and warehouse sites. Every simulated collection stays local and opens no SSH connection. The `--open` option opens the owner’s browser session; its URL fragment is removed immediately after sign-in. The private owner token lives in `.state/owner-access.json`. Keep that file private and keep `.state` out of source control and shared bundles.

Without `--open`, visit the printed local address and enter the owner or operator token. Sessions last eight hours. Tokens are stored as hashes in the workspace database, apart from the private bootstrap owner file. The app uses same-origin requests, a session cookie and per-session anti-forgery tokens. This protects the local browser boundary; it does not protect against another process running with your local account’s file permissions.

## Register a client

A registered client defaults to saved evidence only:

```sh
remote-fleet-agent workspace-app add-client --directory ./fleet-workspace \
  --config ./client/fleet.json --jobs ./client/jobs --name "Client name"
```

To enable real read-only collections, explicitly supply `--enable-collection`, `--key /private/path/to/client_key` and `--authorisation-reference "Approved engagement reference"`. The fleet file must have approved hosts and a nonempty verified known-hosts file. Optionally set `--access-expires-at 2026-12-01T18:00:00+00:00` and `--enable-schedules`.

Mac targets use the [connection kit](MAC-ENROLLMENT.md) by default: **Settings → Create Mac connection kit**. Owners can add an authorized real client directly in Settings. Running the kit on a Mac enables approved SSH access and returns its identity automatically. The controller verifies SSH before registering it; private Python setup follows by default and can be deselected when creating the kit. Native Mac evidence needs no Python.

The app performs no network discovery and creates no endpoint users. Inventory collection uses the existing fixed inventory, diagnostics and readiness checks. The separately enabled administration workflow supports target-confirmed custom diagnostics and exact engineer-approved repair plans. Custom privileged diagnostics cannot be guaranteed read-only. Existing Windows maintenance workflows retain their separate policy checks. Client configurations and key paths are held locally; no endpoint action is enabled merely by viewing saved evidence.

Add an optional `"site": "head-office"` to each approved host. Older configurations default to `main`. Site names use the same short-name format as host names. Moving a host between sites changes who can see its saved evidence, so review operator scope when changing the fleet file.

Windows onboarding shows how to prepare the existing assessment kit, with a placeholder for the engineer’s approved IPv4 address. Mac onboarding defaults to the connection kit above. Existing manually provisioned, pinned Mac SSH accounts remain supported. Linux onboarding also defaults to a [connection kit](LINUX-ENROLLMENT.md), with native fallback checks and Python setup after verified SSH. Docker Linux remains a separate disposable SSH lab; simulated macOS is not native macOS validation.

## Operators and scope

The local owner can create and disable operator tokens. The token is shown once after creation.

| Role | Capabilities |
| --- | --- |
| Observer | Read assigned evidence and save personal views |
| Technician | Observer access plus collections, manual records, follow-ups and baselines |
| Manager | Technician access plus schedules, shared views and report settings |
| Owner | All clients/sites and operator administration |

Each non-owner has a client-to-site scope. `*` means all sites in that client. Site-limited operators see only their hosts, job entries, manual records, tasks, schedules, baselines and journal events. Client-wide records without a site are visible only to operators with full-client scope. Client-wide report settings require full-client scope. Choose a specific site when creating a scoped record.

Disabling an operator rejects their existing sessions and blocks future scheduled dispatches. Access policy, host scope, expiry and operator rights are checked again when a collection starts and before each device worker runs. Already dispatched read-only commands may finish; revocation is not an endpoint kill switch.

## Collections and comparisons

Select the devices and operation, inspect the exact preview, then start it. Previews expire after five minutes, belong to one operator/client and can be used once. A fleet, host-key or access-policy change invalidates a preview. Section names must be supported on every selected platform; leave the section field empty to collect each platform’s supported set.

Jobs show per-device progress and terminal outcomes. A retry targets unsuccessful devices in the operator’s permitted scope. Jobs spanning additional sites cannot be cancelled by a site-limited operator. Cancellation stops owned pending work; it cannot undo queries already sent.

Device pages combine observations, capabilities, evidence provenance, manual evidence and collection history. The evidence table offers a section selector, column filters, column visibility, sorting and CSV download. CSV cells beginning with spreadsheet formula characters are escaped. Personal and manager-shared views preserve the current page, scope, columns, filters and sort order.

Compare two finished inventory collections or select a baseline for a client/site. Added and absent devices describe **collection coverage**, not proof of deployment or removal. Missing, failed or truncated evidence stays unavailable; it is not an unchanged result or a pass.

## Follow-ups and reports

Follow-ups have an owner, priority, due date, evidence reference and status. Verification requires a saved collection with a completed device in the task’s scope. This is an operator-recorded conclusion linked to evidence; the app does not claim that a successful query proves the issue fixed. Manual evidence edits use revision checks, and the signed-in operator supplies the reviewer identity.

Reports include executive coverage counts, freshness, observations, action progress and baseline changes. Private handovers include owners and action details. Shared copies omit device/account identities, raw records, manual notes, references and operator names. Client name, prepared-by text, accent colour and freshness threshold are configurable. Use the browser’s Print command to save a PDF. Review every report before distributing it.

Freshness defaults to 24 hours and can be configured per client. Uncollected evidence, stale evidence and future timestamps are distinguished. A fresh timestamp does not mean a device is online now.

## Optional recurring collections

Scheduling must be enabled in the client policy and explicitly enabled by a manager or owner for selected devices. Intervals range from five minutes to seven days. Runs occur only while this app is open. There is no installed background service or endpoint agent. Missed runs are skipped, overlapping client jobs are skipped, and scope/policy changes disable the schedule until it is reviewed. A schedule runs under its creator’s current permissions. Pause remains available after access expires or collection policy is disabled.

The app does not enforce SSH account/key expiry on endpoints. That remains the responsibility of the existing assessment installer or your approved remote-access management process.

## Local storage and validation

Workspace records use a private SQLite database with optimistic revisions. The HMAC-chained activity journal is verified at startup. A failed check blocks writes and collections while permitting read access. This is a local journal check, not independent evidence against an administrator who controls the database and signing key. Stop the app before backing up the workspace directory, and protect client keys separately.

Tests exercise simulated collections without SSH, role and site isolation, access expiry, preview replay, concurrent edit conflicts, journal tampering, verification references, report redaction, schedules and HTTP authentication/origin/CSRF boundaries. Native macOS/Linux and Windows endpoint validation still requires approved real machines.

## Operations navigation

The five main destinations are Operations, Devices, Approvals, History and Settings. Client and site filters apply on the server. Operations puts uncertain outcomes before waiting decisions and active work. Devices search names, platforms and last-observed evidence; the timestamp does not establish current connectivity. Each work row names its client and site.

Cases open on permanent pages rather than dialogs. Existing `?client=…&support_case=…` links still open after sign-in. Browser Back and Forward restore route filters. A case review retains its captured client, case id, revision and repair digest. If another engineer changes the record, refresh and make a new decision; the UI does not automatically retry the stale approval. Checks passed and user issue resolved remain distinct.

| Former destination | Current location |
| --- | --- |
| Support | Operations case links and permanent case pages |
| Fleet | Devices search → Open device |
| Overview, Evidence, Jobs | Device/client tools |
| Follow-ups | Client tools → Follow-ups |
| Compare, Reports | Client tools → Compare / Reports |
| Onboarding, manual evidence | Settings → Onboarding |
| Schedules | Settings → Schedules, explicitly collection schedules |
| Operators | Settings → Operators, owner only |
| Activity | Client tools → Activity journal |
| Branding | Client tools → Reports → Edit identity |

Choose a client before opening client tools from Settings. Old page query links and saved views remain available. The AI connections page provides a host-local configuration command; it does not embed a chat or change AI-client settings. Workspace approval is the universal fallback.

## Browser validation

Install the optional `browser` dependency group and run `python -m playwright install chromium`, then `python -m pytest tests/browser -q`. Browser fixtures launch the actual HTTP application with fresh fictional clients, authenticate through the login form and forbid endpoint SSH. Unit CI can use `--ignore=tests/browser`; a dedicated browser job installs the runtime. Recordings and screenshots are opt-in, private output artifacts and never substitute for native endpoint tests.

Device pages open with Summary and Support history, with Inventory, Evidence and Access as separate tabs. Start investigation requests confirmation for the exact enrolled device; it does not execute a command. Operations supports platform and assigned-engineer filters; Devices supports platform filtering. These filters are preserved in the URL and enforced by the server. Client audit tools remain under Settings.
