# Remote Fleet Agent

Version 0.6 adds platform-aware macOS/Linux read-only collection, a seven-device simulator and a local Linux Docker lab. The existing MCP tool count stays at 21 by default. See [the cross-platform guide](CROSS-PLATFORM-LAB.md) for launch commands, supported evidence and native testing limits.

The engineer runs one Python MCP server per engagement. Approved Windows hosts are reached through their installed OpenSSH Server, with a private engagement key retained on the engineer laptop and verified public host keys pinned in the engagement configuration.

The default server has 21 tools, including offline evidence management and readiness: capabilities, approved-host listing, inventory, diagnostics, two legacy read-only checks, saved collection jobs, progress/results, retries, cancellation and inventory comparison. Inventory has 12 sections and diagnostics has six. Completed collection is evidence, not a compliance verdict.

An engagement can explicitly enable three maintenance action IDs: start Print Spooler, start Windows Time, and clear the DNS cache. That adds nine tools: single-host preview, inspection, execution and rollback, plus batch preview, inspection, canary execution, explicit continuation and cancellation. Previews expire after ten minutes. Uncertain outcomes are saved and never automatically replayed. DNS clearing has no rollback. Stopping a service does not undo printing or clock changes. The digest binds the reviewed preview; operator authorization is still required.

`init-client` stages the packaged assessment installer and public key, including TXT copies and a copyable command. The installer records pre-existing state, protects the engineer-only firewall scope and schedules SYSTEM cleanup. Reinstallation preserves the baseline. Read [DEPLOYMENT.md](DEPLOYMENT.md) before installing or recovering access.

The application offers no arbitrary command tool. The account behind the SSH key retains its Windows privileges; the tool boundary does not make the underlying credential read-only. File triage and personal-data scan modules exist as legacy code, but are not registered as MCP tools.

See [FLEET-WORKFLOW.md](FLEET-WORKFLOW.md), [MAINTENANCE.md](MAINTENANCE.md) and the README for current commands and coverage limits. Client configurations, private keys and evidence stay outside public source distributions.

The offline dashboard, report, manual evidence and retention workflow is documented in [OPERATIONS-WORKFLOW.md](OPERATIONS-WORKFLOW.md). Endpoint read-only mode still permits local evidence management; reviewed local deletion is explicitly annotated as destructive.
