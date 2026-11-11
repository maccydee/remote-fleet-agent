# AI clients and engineer host operating systems

The AI client, engineer computer and assessed device are independent choices. The toolkit runs on Python 3.11+ and uses local **MCP stdio** for AI integration. Your AI application handles model credentials. The toolkit needs no OpenAI/Anthropic/Google API key; approved real collections use the engagement SSH key. Hosted chat websites do not automatically gain local access, and the dashboard port is not an MCP endpoint.

## Install on the engineer computer

From the downloaded source directory (substitute the local wheel filename for `.` when using a wheel):

macOS/Linux:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m remote_fleet_agent.cli doctor
.venv/bin/python -m remote_fleet_agent.cli workspace-app init --directory ./fleet-workspace
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m remote_fleet_agent.cli doctor
.\.venv\Scripts\python.exe -m remote_fleet_agent.cli workspace-app init --directory .\fleet-workspace
```

Shell activation, Bash and external `ssh-keygen` are unnecessary. Local PowerShell is not required for Python/MCP collections or simulation. The assessment installer runs in elevated Windows PowerShell **on the Windows endpoint**. macOS endpoints use native probes over approved SSH and need no Python for connection or evidence. Owners can generate a [one-device Mac kit](MAC-ENROLLMENT.md) and provision private Python after connection. Linux endpoints default to a [connection kit](LINUX-ENROLLMENT.md) with automatic identity pairing, native fallback checks and Python setup after verified SSH. The optional Docker Linux lab requires a local Linux-container engine; it does not emulate Windows/macOS.

Windows private keys and workspace storage need an ACL-capable filesystem, such as NTFS. New private storage receives a protected DACL for the current account and SYSTEM; private key/database files are protected explicitly. Protection errors fail the write. macOS/Linux use private directory/file modes. USB kits contain only the public key and endpoint installation material.

## Generate setup files

macOS/Linux:

```sh
.venv/bin/python -m remote_fleet_agent.cli ai-config --provider all \
  --config ./fleet-workspace/northstar-demo/fleet.json --directory ./ai-setup
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m remote_fleet_agent.cli ai-config --provider all --config .\fleet-workspace\northstar-demo\fleet.json --directory .\ai-setup
```

For real clients, add `--key` pointing to the private engagement key; verified known-hosts are mandatory. `--jobs` optionally selects the evidence root. Snippets contain absolute paths for **the execution host where generation runs**. Generate again after moving the environment or switching between native Windows, WSL, a container or another computer. WSL uses Linux paths and its own Python environment.

Choose an empty output directory. Review and merge only the generated entry into your AI client's existing settings. Generation never edits those settings. No private key contents, provider API keys or workspace owner tokens are embedded; engagement paths can still be private, so keep generated files out of public Git.

| Provider | Format | Merge destination |
| --- | --- | --- |
| `codex` | TOML `mcp_servers` | Execution host's Codex `config.toml`, commonly `~/.codex/config.toml` |
| `claude-code` | JSON `mcpServers` | Project `.mcp.json` or Claude Code MCP configuration workflow |
| `claude-desktop` | JSON `mcpServers` | Developer → Edit Config in Claude Desktop |
| `gemini` | JSON `mcpServers` | Gemini CLI `.gemini/settings.json` |
| `cursor` | JSON `mcpServers` | Cursor `.cursor/mcp.json` |
| `vscode` | JSON `servers` | VS Code `.vscode/mcp.json` |
| `generic` | `command` / `args` | Other local stdio MCP clients |

The shared launcher is `remote-fleet-mcp --config /absolute/path/fleet.json`. Generated settings use the environment's absolute Python executable and `-m remote_fleet_agent.launcher`, with separate arguments, so no shell quoting or GUI PATH assumptions are required. The server has no provider-specific model prompts or agent SDK dependency.

Default setup is audit-only. `--enable-maintenance` intentionally exposes only actions already approved in the fleet configuration; it does not add actions or bypass previews/digests. An inherited `REMOTE_FLEET_AGENT_AUDIT_ONLY=1` still wins. Gemini retains `trust: false`; no client auto-approval or bypass is generated. Restart/reconnect the client after merging, then call `describe_capabilities` and check the expected engagement and simulation mode.

MCP engagement scope is independent of browser app operator sessions and site roles. For a restricted MCP operator, provide a separate fleet configuration containing only their approved hosts and an appropriate account/key. Installing the same MCP entry into another client grants that client the same engagement scope; it does not inherit an observer role from the dashboard.

## Diagnostic probe

```sh
remote-fleet-agent doctor --config ./fleet-workspace/northstar-demo/fleet.json --probe
```

Use the same platform-specific Python prefix above when the environment is not activated. `doctor` performs local dependency/configuration checks. `--probe` requires `simulation: true`: it starts a subprocess, negotiates MCP, lists tools, calls simulated inventory and confirms out-of-scope rejection. Temporary evidence is discarded. It refuses real endpoint configurations.

The launcher reserves stdout for MCP protocol messages and sends startup diagnostics to stderr. Explicit arguments replace inherited engagement key/config/job settings. The legacy environment-variable entry point `python -m remote_fleet_agent.server` remains available.

## Verification limits

All seven config formats are tested against macOS, Windows and Linux paths, including spaces, Unicode and Windows backslashes. A real SDK stdio handshake and simulated tool call are tested on the current macOS host. These are configuration and protocol checks, not end-to-end certification of every AI application release or model.

GitHub Actions is configured for macOS, Windows and Linux with Python 3.11 and 3.13. It runs tests, private storage/process checks, MCP integration and packaging. The Windows job checks actual protected ACLs. The workflow only runs once the repository is on GitHub with Actions enabled; its presence does not mean those jobs passed. Native Windows/Linux host runs and interactive provider-specific tests remain pending. Provider policies and model tool-use quality vary.

Scheduling and the local UI remain independent of the AI provider. Endpoint permissions and limitations remain as described in the platform and maintenance guides.

Official references checked:

- [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Claude Desktop local MCP](https://modelcontextprotocol.io/docs/develop/connect-local-servers)
- [Gemini CLI MCP](https://geminicli.com/docs/tools/mcp-server/)
- [Cursor MCP](https://cursor.com/docs/mcp)
- [VS Code MCP](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [MCP stdio](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [Windows process signalling behavior in Python](https://docs.python.org/3/library/os.html#os.kill)


## Support conversations and workspace oversight

Use the support bridge for AI-led troubleshooting against an operator's workspace permissions. The older engagement launcher above remains useful for inventory, but its configuration scope is independent of workspace roles. Keep the two profiles clearly named.

Generate support profiles on the computer that will run them:

```sh
python -m remote_fleet_agent.support_mcp \
  --workspace-url http://127.0.0.1:8877 \
  --operator-token-file /absolute/private/path/technician-token.txt \
  --write-configs /absolute/private/path/new-ai-setup
```

On Windows, substitute the environment's `.venv\Scripts\python.exe` and native absolute paths. The token file is a private technician credential; keep owner and manager credentials out of AI-client configuration. Generated profiles reference the file rather than embedding its contents. Review the generated entry and merge it into the chosen client's existing settings. Configuration generation does not edit those settings.

Conversation happens in the AI client. The Operations workspace supplies shared case history, device context and engineer review. The generic MCP bridge cannot authenticate a model's claim that a human approved a change. Use the workspace decision flow unless a provider adapter has been separately implemented and verified to supply a trusted human decision tied to that exact plan.

Keep these evidence categories separate when reporting compatibility:

| Evidence | What it establishes |
| --- | --- |
| Generated profile parses | Configuration format and path handling |
| MCP SDK subprocess passes | Actual stdio protocol startup and tool schema |
| Interactive client smoke passes | That installed application performed the tested workflow |
| Endpoint native test passes | Commands ran on that endpoint OS with its actual permissions |
| Controller native test passes | The controller, HTTP service and subprocesses ran on that host OS |

Presence of Claude Code or Codex on a computer proves installation only. An untested provider stays `NOT_RUN`. A Mac driving Docker Linux endpoints is macOS-controller-to-Linux-container evidence; it does not validate Windows hosting. Docker Linux controller tests must be identified as container runs. Native macOS requires an actual Mac SSH session, and native Windows requires the currently enrolled test PC. Do not enable Remote Login or reinstall OpenSSH merely to turn a missing prerequisite into a pass.

## Verified native controller matrix — 2026-11-09

The [complete GitHub matrix](https://github.com/maccydee/remote-fleet-agent/actions/runs/42546966870) passed on Python 3.11 and 3.13 for Windows, macOS and Linux. Windows passed 395 tests with 18 skips per version; macOS/Linux passed 400 with 13 skips per version. The native Windows check verifies actual NTFS DACLs and runs local PowerShell scratch repair/recovery/cleanup and refusal tests. All six wheel/source builds and the 26-test Chromium suite passed.

This establishes native controller coverage and local script components. It does not validate every 3×3 controller/target combination, an interactive provider application or a full live endpoint repair. Opt-in endpoint tests still require their enrolled scope and separate human decision.
