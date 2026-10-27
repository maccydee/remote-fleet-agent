# Design: Remote Fleet Agent

**Date:** 2026-10-27 (revised same day, post-skeptic-review)
**Working name:** `remote-fleet-agent` (placeholder — rename once a real name lands)
**Status:** Design validated, ready to turn into an implementation plan.

## Revision note

The original version of this design used a custom installed agent (its own
binary, its own listener, its own pairing crypto) on each target. A skeptic
review (2026-10-27) flagged that as the riskiest part of the design — an
unsigned process that opens a port and accepts remote commands looks exactly
like a RAT to client EDR (Defender/CrowdStrike/SentinelOne), and most of what
it did (secure channel, host registry, remote execution, file transfer) is
already solved, signed, and trusted: **SSH**. This revision replaces the
custom agent with Windows's built-in OpenSSH server plus one MCP server on
the engineer's own laptop. The decisions below marked **(revised)** changed
as a result; everything else from the original design stands.

## Problem

IT support engineers and consultants often need to inspect, troubleshoot, or
reconfigure a machine that isn't theirs — a client's desktop, server, or NAS
— without sitting at it. SSH's bad reputation here is really "it's often
disabled by default and limited to a raw shell/file pipe," not a problem with
SSH itself. Installing and configuring full Claude Code on every target
machine is too heavy for a one-off support visit or audit.

The goal: let an engineer run Claude, from their own laptop, against other
machines on the same network — with a lighter footprint than "install Claude
here," richer structure than raw SSH output, and a trust/safety model that
holds up even though the engineer is sometimes auditing their own tool's
security practices.

**First concrete use case:** Cyber Essentials / DCC-style audits across a
client's fleet, including GDPR-sensitive file discovery, data-hygiene tasks
like restructuring a NAS share (move/copy/delete at scale), and ad-hoc
questions like "check the patch version on asset 1."

## Architecture (revised): where the brain lives and how it reaches a target

**Decision:** The reasoning stays on the engineer's laptop — their normal,
already-configured Claude Code session — exactly as before. What changed is
the transport and the shape of the tool layer:

- **Transport is SSH**, specifically Windows's built-in OpenSSH Server
  (Microsoft-signed, included in Windows 10 1809+ and Server 2019+). No
  custom listener, no custom encryption to design or audit.
- **One MCP server runs locally on the engineer's laptop**, not on each
  target. It holds the per-client registry of machines, opens SSH
  connections to run commands and PowerShell (`Get-HotFix`,
  `Get-MpComputerStatus`, `Get-NetFirewallProfile`,
  `Get-LocalGroupMember`, `ConvertTo-Json` for structured output), and is
  the *only* thing Claude talks to. It exposes a small, fixed set of safe
  tools (e.g. `run_check`, `quarantine_move`, `dry_run_copy`, `fit_check`) —
  Claude never gets a raw "delete" or "run anything" primitive; the laptop
  server enforces the guardrails below.
- Fan-out across many machines (see below) lives in this same laptop-side
  server, not in Claude adding dozens of individual remote connections.

**Why this, not the original custom agent:**
- A custom binary that opens a port and executes remote commands is
  behaviourally identical to malware from an EDR's point of view — exactly
  the kind of thing that gets quarantined mid-audit, with the engineer then
  explaining a "hacking tool" to the client's security team.
  OpenSSH is signed by Microsoft and recognised by every IT team.
- SSH already provides the encrypted channel, host identity, and key-based
  auth the original design was trying to build from scratch — rebuilding
  that ourselves was pure risk with no added capability.
- A full PowerShell session covers every Cyber Essentials/DCC check
  discussed, with zero custom protocol work.

**Alternatives considered (original, still valid as the rejected options):**
- *Push/one-shot via existing remoting (PsExec/WinRM)* — functionally
  similar to the SSH approach, but SSH is cross-platform and the more
  broadly recognised, auditable choice for this use case.
- *Full remote-desktop / computer-use on the target* — most capable, but a
  much higher trust bar than v1's use cases need.

## Capability tier (v1 scope)

**Decision:** v1 covers file operations, remote command/PowerShell
execution, process listing, logs, and system/config queries — everything
reachable through an SSH session. No screen capture or GUI control. Every
scenario discussed (troubleshooting, setup, patching, security audits, NAS
restructuring, GDPR file discovery) is achievable this way; screen-level
access is deferred to a possible future tier, added per-task rather than
always-on.

## Deployment (revised)

**Decision:** Manual install via USB stick — **one stick per client**, not
reused across engagements. The stick carries two scripts:

- **`install.ps1`** — enables the Windows OpenSSH Server feature (recording
  whether it was *already* enabled before we touched it, so uninstall never
  disables something the client was independently relying on), adds the
  engineer's public SSH key to the target account's `authorized_keys`, adds
  a Windows Firewall rule scoping SSH to the engineer's laptop IP/subnet
  only, and creates the self-destruct scheduled task (below).
- **`uninstall.ps1`** — reverses all of it: removes the key, removes the
  firewall rule, disables the OpenSSH service *only* if our install turned
  it on, removes the scheduled task, and prints a confirmation receipt of
  what was removed versus what was left alone (e.g. "SSH was already
  enabled before this install — left running").

**Why one stick and one SSH keypair per client:** a lost stick or laptop
then only exposes machines at a single client, not every network the
engineer has ever touched.

**Registry keyed by hostname, not IP** — DHCP can reassign addresses between
visits; hostnames hold up across a multi-day engagement.

**Authorisation:** written client sign-off is required before installing
any remote-access tooling on their infrastructure — this is Computer Misuse
Act territory without it.

## Trust, pairing, and the self-destruct dead-man's switch (revised)

**Decision:** Pairing is a standard SSH keypair, one per client, generated
on the engineer's laptop. The public key is deployed by `install.ps1`; the
private key stays passphrase-protected on the laptop and is deleted when the
engagement closes. The laptop-side MCP server keeps the registry (hostname,
nickname like "asset 1", client, last-seen).

**New: self-destruct after 3 days.** `install.ps1` also creates a Windows
scheduled task that automatically performs the same revocation as
`uninstall.ps1` — removing the key, the firewall rule, and itself — three
days after install, unless the engineer has re-run `install.ps1` on that
machine in the meantime (which resets the clock).

**Why:** this closes two risks the skeptic review raised: a forgotten manual
uninstall leaving permanent remote access into a client network, and a lost
laptop carrying working keys indefinitely. With the timer, access on any
given machine lapses on its own within days even if nobody remembers to
clean up.

**Important distinction, kept from the original design's hard rule:** the
3-day timer is a safety net fixed *at install time*, independent of any
task's outcome. It is not Claude or the tool judging "this task is done, so
remove yourself" — that remains forbidden. Only two things ever cause
removal: this pre-set timer, or the engineer's own explicit `uninstall.ps1`/
remote uninstall command.

**Trade-off to flag:** a multi-day audit now needs the engineer to revisit
(or re-run `install.ps1` remotely over the still-active SSH session) before
day 3 to avoid losing access mid-engagement. That's a deliberate change from
the original "persists until revoked" model — short-lived by default,
renewed on purpose, rather than indefinite.

## Destructive file operations

**Decision (unchanged in spirit, revised in mechanism):** plan-then-confirm,
plus soft-delete instead of hard delete, now implemented through the
laptop-side MCP server over SSH — a dry-run preview (`robocopy /L`) is shown
before anything runs, and "delete" moves files into a timestamped quarantine
folder on the same volume rather than removing them.

**Revised: quarantine purge is explicit, not automatic.** Actually freeing
the quarantined space after the retention window is a deliberate step the
engineer runs at client sign-off, not a timer running on the target —
because once the engagement (and SSH access) ends, nothing is left running
to enforce a timer. This also avoids the quarantine folder itself becoming
the "redundant kit" the engineer wants to avoid leaving behind.

## Large data moves (pre-flight) (revised)

**Decision:** Keep the fit-check (source size vs. destination free space,
hard stop if it won't fit). Drop the separate throughput-benchmark step —
`robocopy`'s own live progress output gives a real throughput figure within
the first update of a running transfer, so a dedicated benchmark copy adds a
step without adding information.

## Platform scope

**Decision:** Windows first for v1, unchanged. Mac/Linux support deferred.

## NAS and non-Windows devices (new)

**Decision:** NAS boxes (often Linux-based — Synology, QNAP) are not direct
SSH/PowerShell targets in v1. File operations on a NAS share are instead run
from an already-paired, trusted Windows machine on the same network that has
the share mounted (e.g. via SMB) — using the same SSH + PowerShell +
`robocopy` toolchain as any other target. Nothing is installed on, or
enabled on, the NAS itself.

## Multi-machine (fan-out) queries (revised)

**Decision:** Claude talks to the one laptop-side MCP server, which holds
the registry and fans a request out over SSH to every machine in scope,
merging results before they reach Claude. This replaces the original idea of
Claude adding each paired target as its own remote MCP server, which doesn't
scale — ~30 machines × ~10 tools each would put hundreds of near-duplicate
tools into Claude's context. For anything heavy or destructive, the scope is
still confirmed with the engineer first ("that's all 12 machines, or a
subset?").

**Still out of scope:** a dedicated network discovery/recon module. Fleet
count and network info still come from the laptop's own registry and from
querying an already-paired machine's own OS commands — no scanning.

## GDPR-sensitive file discovery (revised: back in scope)

**Decision:** This is explicitly part of the DCC audit and stays in scope.
**Hard rule:** pattern matching for personal data (emails, UK National
Insurance numbers, card numbers, etc.) runs *on the target machine*, inside
the same PowerShell/SSH session — Claude never receives raw file contents.
The tool returns only paths, counts, and match-type labels (e.g. "3 files on
\\server\share\HR contain what looks like a National Insurance number").
Sending actual client personal data up to Claude's API would risk breaching
the exact regulation the audit is checking for.

**Open:** the precise matching patterns/heuristics are an implementation
detail for the plan stage, not the design.

## Reporting / compliance frameworks

**Decision (unchanged):** No compliance framework is hard-coded into the
tool. Agents (now: the laptop-side MCP server, via SSH) return clean,
structured raw data; Claude synthesizes that into a report or answer as an
ordinary conversational task, using whatever framework is relevant at the
time.

## Summary of v1 scope

| Area | Decision |
|---|---|
| Brain location | Operator's laptop (normal Claude Code), never the target |
| Target capability tier | File/command/process/log/PowerShell — no screen/GUI control |
| Transport | **SSH (Windows OpenSSH, Microsoft-signed)** — no custom agent or protocol |
| Tool layer | One MCP server on the laptop; Claude never gets raw delete/shell access |
| Deployment | USB stick **per client**, `install.ps1` / `uninstall.ps1` |
| Trust anchor | SSH keypair per client, deployed via the USB stick |
| Pairing persistence | **Self-destructs after 3 days** unless re-installed; explicit uninstall available anytime |
| Uninstall | Remote or local, always explicit; never triggered by task outcome — the 3-day timer is a separate, deliberate safety net |
| Destructive file ops | Plan-then-confirm + quarantine; purge is an explicit sign-off step, not automatic |
| Large data moves | Fit-check + robocopy's own live progress (no separate benchmark) |
| Platform | Windows first; NAS reached via a trusted Windows proxy machine, nothing touches the NAS itself |
| Multi-machine queries | One laptop-side MCP server fans out over SSH, with scope confirmed first |
| Network discovery | None — fleet count/network info come from the registry and paired machines |
| GDPR file discovery | **In scope.** Matching runs on-target; only paths/counts/match-types ever leave the machine |
| Reporting | No built-in framework; Claude synthesizes from structured raw data |

## Open follow-ups (not yet designed)

- Exact MCP tool surface / schema on the laptop-side server (run_check,
  quarantine_move, dry_run_copy, fit_check, GDPR-scan, etc.) and the
  structured fields each check returns, so results compare cleanly across
  machines.
- GDPR/PII matching patterns and performance at scale across many machines.
- A precise definition of what "a full DCC audit" checks, used as the v1
  acceptance test.
- A concrete threshold for "more than a trivial number of files" in the
  plan-then-confirm trigger.
- Required privilege level for the SSH session (likely local admin or
  SYSTEM-equivalent for patch/AV/firewall/admin-group checks) and how
  `install.ps1` requests or verifies that.
- Mac/Linux equivalents once Windows v1 is proven.

## Next step

Turn this into an implementation plan via the `writing-plans` skill once
ready to start building.
