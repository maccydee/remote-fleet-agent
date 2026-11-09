# Fleet evidence workflow

Start with an approved engagement configuration and a known-hosts file verified through the host console. The same policy is used by the CLI and MCP server.

## One request for a named scope

Use `list_hosts` to see available names and groups. `start_job(operation="inventory", group="office")` schedules only the approved members of that group. An explicit `hosts` list is also supported; an unknown host rejects the whole request before any connection is opened. A group and an explicit list can't be combined.

Jobs share a concurrency limit across the server. A host failure doesn't discard other hosts' results. Each host is queued, running, complete, partial or failed. Cancellation and server shutdown retain results already saved. Cancellation stops the controller's collection tasks; it isn't a claim that an already executing remote read has instantly stopped.

`get_job` returns summary data. `get_host_result` returns one host's detailed evidence without querying the machine again. `list_jobs` reads recent jobs for the current client. Files are saved atomically with private permissions; the client name separates engagement stores.

## Retry and interruption

`retry_job` starts a linked job for failed, partial, cancelled or interrupted hosts. Hosts that completed successfully aren't queried again. A partially collected host is collected again using the original requested sections. Its earlier successful sections remain available in the parent job.

Current host approval is checked again before scheduling a retry. A historical job isn't authority to reconnect to a host that was removed from the configuration. Work is never silently resumed after restart. If a saved active job's process no longer exists, its unfinished hosts are marked interrupted.

## Coverage and interpretation

Inventory defaults to all 12 sections; diagnostics defaults to all six. Use `describe_capabilities` for the exact section names. Inventory section output is capped at 500 records and diagnostics at 100. System event queries separately cap collection at 50 records from the previous 24 hours. Each query has a configurable timeout.

An unavailable module, access failure, malformed response or timeout produces unknown evidence for that section. Empty output means no matching records were returned; its meaning depends on the query. Completion means evidence collection finished, not that the machine is compliant or healthy.

Software inventory covers machine-wide uninstall registrations, not a guaranteed list of every user-installed, portable or Store application. Hotfixes don't establish update compliance. Defender status doesn't establish the status of a separate antivirus product. SecurityCenter2 can be unavailable on Windows Server. Stopped automatic services can be normal trigger-start services. Reboot indicators are selected registry signals rather than a complete servicing diagnosis.

## Compare two inventories

Pass two finished inventory job IDs to `compare_inventory`. The result identifies added/removed host names and changed sections for shared hosts. Item ordering is ignored. Unknown, missing and truncated sections are marked unavailable for comparison rather than treated as additions or removals.

The comparison includes collected operational values. Disk free space can change naturally between snapshots. It reports observed differences and doesn't label them security incidents.

## Local storage and lifecycle

Keep the server open while asynchronous jobs run. On normal shutdown, collection tasks are interrupted and pooled SSH connections close. The CLI waits for its job and closes its connections before exiting. Neither path uninstalls OpenSSH or changes the Windows assessment timer.

The earlier installer prototypes and their lifecycle need separate consolidation and Windows testing. Inventory and diagnostic jobs add no endpoint software and perform no remediation. Separately enabled maintenance uses the preview/execute flow described in [MAINTENANCE.md](MAINTENANCE.md); it never runs through a collection job.

Readiness is a third saved collection operation. For dashboard/report exports, manual evidence, reviewed retention and maintenance batches, see [OPERATIONS-WORKFLOW.md](OPERATIONS-WORKFLOW.md).
