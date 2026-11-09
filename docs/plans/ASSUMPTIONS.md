# Planning assumptions, 8 November 2026

These fill details the user delegated. They are implementation defaults, not additional product approvals. Read with the operations and administration designs.

## A-001 | gap-fill | OPEN

Keep plain JavaScript and the existing Python application for the first UI release. Alternatives were a framework rewrite or a separate frontend service. Existing packaging and authentication can support the proposed screens without that migration.

## A-002 | gap-fill | OPEN

Use bounded offset pagination for the initial all-client work table and reset paging on refresh. Alternatives were signed keyset cursors or snapshot sessions. Offset pagination keeps the first change small; the UI acknowledges that live updates can move rows and deduplicates them. Backend counts must still consider all permitted cases, including cases older than the current last-100 API cap.

## A-003 | gap-fill | OPEN

Ship workspace-based human approval as the first proven implementation. Expose an in-client decision only after a provider-specific authenticated mechanism passes binding/replay/identity tests. Alternatives were promising universal provider approval or accepting model-supplied yes/no; neither establishes the required human authority.

## A-004 | gap-fill | OPEN

Use verified SSH local forwarding for the first remotely accessed self-hosted controller. Keep application login and local HTTP binding. Match the forwarded local port to the configured controller origin, or implement an explicit tested origin configuration before allowing different ports. Direct network HTTP/TLS and a vendor relay are separate later work.

## A-005 | gap-fill | OPEN

Native smoke tests modify unique scratch files and disposable processes, with engineer decisions through the normal approval flow. Alternatives were changing production services or relying on simulated success. These fixtures prove native transport and execution without creating an Outlook or printer outage. Real application, driver, elevated service and desktop-session scenarios remain separate documented release evidence.

## A-006 | gap-fill | OPEN

Start with one durable active scheduler per controller store and one exclusion claim per verified endpoint. Multiple UI/API readers can coexist. Alternatives were distributed worker claims or process-only locks. Single scheduling ownership makes crash recovery inspectable; uncertain remote steps remain held for reconciliation.

## A-007 | gap-fill | OPEN

A queued change waits for its execution window. An in-flight bounded step can finish after the window ends; further steps wait or expire. Recovery after that window requires a separately declared approved recovery window. Cancellation stops future dispatch and requests best-effort termination, with uncertainty retained when remote completion cannot be established.

## A-008 | gap-fill | OPEN

Keep credentials in existing private local files with POSIX permissions or Windows ACL enforcement, and never place approval credentials in the AI bridge. A new OS keychain/vault backend is optional future work. Test file privacy on each host and document the local administrator trust boundary.

## A-009 | gap-fill | OPEN

Use owner or scoped-manager authority for target confirmation initially, matching current repair approval roles. Technician MCP credentials can request a confirmation but cannot supply the human decision. A distinct investigation-confirmation permission could be added later if real team workflows require it.
