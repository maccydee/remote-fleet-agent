# Remote Fleet Agent

A laptop-side MCP server that lets Claude run Cyber Essentials/DCC-style
audits and safe file operations against Windows machines over SSH, without
installing anything beyond a signed, in-box Windows feature on the target.

**What this is:** one Python MCP server you run on your own laptop, talking
SSH to targets that had `install.ps1` run on them from a USB stick.

**What this isn't:** a custom remote-access agent. There's no listener, no
custom protocol, and no persistent software on the target beyond Windows's
own OpenSSH Server feature (which self-destructs after 3 days by default).

See [`docs/plans/2026-10-27-DESIGN-remote-fleet-agent.md`](docs/plans/2026-10-27-DESIGN-remote-fleet-agent.md)
for the full reasoning behind every decision below, and
[`ASSUMPTIONS.md`](ASSUMPTIONS.md) for every place a decision was made
without a client-specific answer yet (GDPR pattern set, confirmation
threshold, key type, required privilege level).

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Per-engagement workflow

1. **Stage a USB stick for this client:**
   ```bash
   .venv/bin/remote-fleet-agent init-client <client-name> --stage-dir /Volumes/<usb-stick>
   ```
   This generates a fresh ed25519 keypair under `~/.remote-fleet-agent/keys/<client-name>/`
   and copies only the **public** key, plus `install.ps1`/`uninstall.ps1`,
   onto the stick. The private key never leaves your laptop.

2. **On each target machine**, from an elevated PowerShell session:
   ```powershell
   .\install.ps1 -AllowedIP <your-laptop-ip> -PublicKeyPath .\id_ed25519.pub
   ```
   This enables Windows OpenSSH Server (recording whether it was already
   on), adds your key, scopes a firewall rule to your laptop's IP only, and
   sets a scheduled task that reverts all of it automatically after 3 days.
   Re-running it on a later visit resets that 3-day clock.

3. **Point Claude Code at this MCP server** (stdio), with
   `REMOTE_FLEET_AGENT_KEY_PATH` set to that client's private key:
   ```bash
   REMOTE_FLEET_AGENT_KEY_PATH=~/.remote-fleet-agent/keys/<client-name>/id_ed25519 \
     .venv/bin/python3 -m remote_fleet_agent.server
   ```

4. **Run the audit from Claude** — ask it to run checks, scan for
   GDPR-sensitive files, or tidy up a NAS share. Every destructive file
   operation is plan-then-confirm, and "delete" always means quarantine,
   never a real delete.

5. **When the engagement ends**, run `uninstall.ps1` on each target (or
   just let the 3-day timer handle it), then delete that client's keypair:
   ```bash
   rm -rf ~/.remote-fleet-agent/keys/<client-name>/
   ```

## The hard rules

These aren't implementation details — they're the reasons this tool is
safe to use on a client's infrastructure, and they should never be worked
around:

- **Uninstall is always explicit.** It only ever runs from a direct command
  you issue, or the pre-set 3-day timer. Nothing about a task's outcome —
  success, failure, or an unexpected result — ever triggers removal.
- **GDPR scan results never include matched text.** Pattern matching runs
  on the target; only a path, a match type, and a count ever come back.
- **One USB stick and one SSH keypair per client.** A lost stick or laptop
  then only exposes one client's machines, not every engagement you've run.
- **"Delete" means quarantine.** Files move into a timestamped folder on
  the same volume; nothing is actually removed until a deliberate,
  client-approved purge at sign-off.
- **Every heavy or multi-machine operation is scope-confirmed first.**
  Claude states the exact host list before fanning anything destructive
  out across more than one machine.

## Project layout

- `src/remote_fleet_agent/` — the MCP server, SSH client, host registry,
  connection pool, fan-out helper, and the five tools
  (`run_check`, `fit_check`, `dry_run_copy`, `quarantine_move`, `gdpr_scan`).
- `scripts/install.ps1` / `scripts/uninstall.ps1` — the target-side scripts
  carried on the USB stick.
- `tests/` — the Python test suite (`pytest`); `scripts/*.Tests.ps1` — the
  PowerShell test suite (`Pester`, run via `pwsh`).
- `docs/plans/` — the design doc, implementation plan, and execution log.
