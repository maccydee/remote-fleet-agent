# Remote Fleet operations workspace

Agreed direction, 8 November 2026. Read alongside `2026-11-08-DESIGN-ai-assisted-remote-administration.md`. This design changes the engineer experience; capabilities still need implementation and validation.

## Where the work happens

Engineers converse in Claude, Codex, Gemini or another supported AI client. Remote Fleet supplies the authorised device access, execution controls and shared support record. The workspace is where engineers oversee investigations, review proposed changes and check outcomes. It does not need a second AI chat interface.

The user approved this principle, Operations as the landing page, full case pages with a summary and activity timeline, and an all-permitted-client view. They delegated the remaining UI decisions and requested an implementation plan with Windows, Docker Linux and native Mac coverage.

## Operations is home

Show all clients the signed-in operator can access. Persist client/site filters in the URL, with All permitted clients as the default. Label every row with its client, site and exact device. Viewing a combined list grants no additional execution authority.

Put work needing attention first: unknown execution outcomes, failed checks and blocked work, then approvals waiting, active investigations, running repairs and upcoming maintenance. Use a compact table with Issue, Client/site, Devices, Engineer, State, Updated and Next action. Provide filters for these columns and platform. Counts use the same authorised dataset as the rows and say when results are partial.

An empty workspace offers Connect your AI client and Enrol devices. With devices present, show a short example request an engineer can copy into their AI client. Keep manual case creation available as a secondary action.

The previous home page emphasises evidence coverage and collection health. Those still belong in device evidence and client reports. The alternative, landing on the device inventory, makes a known machine quick to find but hides the team's work across clients. A conversation-led UI would duplicate the engineer's AI client.

## Navigation

Use five primary destinations: Operations, Devices, Approvals, History and Settings. Keep global device search accessible from every page. Search results include client, site and platform; duplicate hostnames remain distinguishable.

Devices has tabs for Summary, Support history, Inventory, Evidence and Access. Evidence and provenance retain filterable columns, with raw output available on expansion. Last observed and last connection attempt are separate fields. Stored evidence never implies that a device is currently online.

Approvals collects pending target confirmations and repairs the operator can review. Each item links to the full case. Observers can see permitted status but cannot decide. Technicians can request approval; owners and scoped managers decide. Counts reveal only permitted records.

History provides completed and interrupted cases, past attempts and collection jobs. Reports and comparison remain reachable from the relevant client or device. Settings contains client/site configuration, access, enrollment, AI connection instructions and schedules. Existing capabilities remain reachable during migration, with redirects for old links.

## Full case page

A permanent case URL opens a normal page rather than a large dialog. Retain existing `?client=...&support_case=...` links as a supported entry point. Back, forward, refresh and direct links work after sign-in. No URL contains an access token.

At the top show the symptom, named client and devices, platform, assigned engineer if recorded, SSH execution account, current state and last update. For older cases without an assignee show Unassigned; do not infer ownership from the AI provider.

A prominent review panel shows the proposed change, diagnosis and uncertainty, exact targets, disruption, timing, verification and recovery limits. The executable commands are available for inspection before approval. After approval, keep its plan revision and decision visible. Present the currently supported single-script plan faithfully until multi-step plans exist.

Below it, a timeline shows investigation, evidence, proposal revisions, decisions, execution, verification and recorded user outcomes. Collapsible entries expose bounded stdout/stderr and provenance. Use text with status icons; colour is supplementary. Keep keyboard focus stable during refresh and provide a readable narrow-screen layout.

A console-first design makes large command output easy to read but buries review decisions. Workflow columns separate facts the engineer needs to assess together. The summary and timeline layout supports a quick review while retaining the technical record.

## Decisions and outcomes

Target confirmation is once per investigation and device, with a new confirmation for a changed identity or connection context. It names client, device, OS and account. Repair approval reviews the exact current plan and its targets. The same decision record backs workspace and trustworthy AI-client approval; providers without a proven human-decision channel use a workspace link.

Pending decisions are explicit. A disabled or expired approval does not appear as a runnable change. Hide unavailable execution controls and explain the reason. UI visibility supports the engineer; server checks enforce authority.

Use distinct labels for Command completed, Checks passed, Issue confirmed resolved and Outcome unknown. A successful script does not establish a working desktop application or printed page. Unknown outcomes offer investigation and manual reconciliation, never an unqualified Retry repair button. Pending work may be cancelled; work already sent to a device is reported accurately.

## Visual treatment

Keep the existing Remote Fleet logo and purple accent. Use a neutral, compact interface with readable tables, a restrained sidebar, clear state labels and a prominent client/device context. Avoid an audit score or compliance percentage as the main visual. Show a small number of useful operational totals above the work table.

Simulation is clearly marked on the workspace, row and case. Distinguish simulated results from native evidence, including in screenshots. Hide planned features until their backend capability is available. A fake terminal animation or invented live-presence indicator would make the overview misleading.

## Release sequence and validation

First ship the operations navigation, authorised combined feed and case pages over the current v0.9 backend. Then add the investigation/plan/execution capabilities from the administration design. This gives engineers a useful workspace without presenting future controls as working today.

Test server authorisation independently of browser controls. Exercise duplicate hostnames across clients, stale approval pages, refresh races, no-data states, disabled operators, limited roles, old links, long output and keyboard navigation.

The live validation matrix uses Cal's enrolled Windows test PC DESKTOP-6PB6U6S, two Linux Docker SSH devices and this native Mac. Resolve their current identity, access policy and SSH prerequisites at execution time. Use unique scratch directories and disposable processes. Docker validates actual Linux shell execution and SSH transport, but does not establish native macOS, Windows, systemd, driver or desktop-session behaviour. Record unavailable environments as blocked or not run, with the reason. Keep real application and driver repairs as separate scenario evidence.

## Marketing capture during testing

Capture screenshots and short source recordings while validating Operations, device search, investigation, repair approval, staged rollout and outcomes. Include the actual AI-client/terminal-to-workspace handoff where available. Use clearly labelled fictional clients for public scenes and keep native Windows/Linux/Mac evidence private until reviewed. Link each asset to its scenario, actual result and source revision. Capture tooling and review are part of the implementation plan; publishing is separate.
