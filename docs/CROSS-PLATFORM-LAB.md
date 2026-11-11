# Cross-platform collection and local labs

Version 0.6 provides two ways to try the toolkit: a fictional fleet that works without Docker, and two disposable Linux containers with real SSH endpoints. Both keep their files in a directory you choose. They leave existing client registrations and Windows assessment installations alone.

## Start with the demo

```sh
remote-fleet-agent demo --directory ./fleet-demo
```

Open `fleet-demo/dashboard.html` in a browser. There are seven devices across Windows, macOS and Linux. Some have review findings, one has limited permissions and another is offline. The report and dashboard label this evidence as simulated. Run the command again to add another collection and regenerate the pages.

Simulation mode never opens an SSH connection. It does not need a private key or a working known-hosts file. Endpoint maintenance is disabled, including legacy Windows tools. Jobs, manual evidence and exports live under `fleet-demo/jobs/simulation/demo-fleet/`; real evidence uses a different store.

You can also run the usual collection commands against this configuration:

```sh
remote-fleet-agent run-fleet --config ./fleet-demo/fleet.json \
  --job-dir ./fleet-demo/jobs --operation inventory --group macos
```

An offline simulated device remains failed. Unsupported or inaccessible evidence remains unknown. A partial job may exit with code 2, which is expected when the selected scope contains failures.

The demo uses the normal job, report, filtering, manual-evidence and comparison workflows. It does not simulate executing maintenance. You can edit and stage manual records in the dashboard, download the changes, import them into this demo store and regenerate the dashboard.

```sh
remote-fleet-agent workspace import-manual --config ./fleet-demo/fleet.json \
  --job-dir ./fleet-demo/jobs --input ./manual-updates.json
remote-fleet-agent workspace dashboard --config ./fleet-demo/fleet.json \
  --job-dir ./fleet-demo/jobs --private --output ./fleet-demo/dashboard.html
```

## Start Linux devices in Docker

Install and start a local Docker engine with Docker Compose support. The first build downloads the official Ubuntu 24.04 image and installs OpenSSH Server, Python 3 and IP tools. It needs Internet access. The toolkit refuses a remote Docker context.

```sh
remote-fleet-agent lab init --directory ./linux-lab
remote-fleet-agent lab up --directory ./linux-lab
remote-fleet-agent lab collect --directory ./linux-lab
```

Open `linux-lab/dashboard.html`. These results come from two actual Linux containers over SSH. They appear as Linux devices. Collection may be partial: containers often lack systemd, a supported firewall manager or evidence of the physical disk's encryption. Those gaps are expected and stay visible.

Each device publishes SSH on a Docker-selected port bound to `127.0.0.1`. The generated fleet configuration records those ports. Connections use a lab-only client key and pinned host keys generated locally before startup; no unauthenticated host-key scan is trusted. Different ports get separate cached SSH connections.

The containers have a read-only root filesystem, CPU/memory limits, no privileged mode, no Docker socket and no host client credentials. They mount only their own lab host-key directory. The image contains the public client key. A minimal capability set lets OpenSSH read its key and drop to the unprivileged `fleet` account. Password login, root login, terminal allocation and SSH forwarding are disabled.

The build context allows only the Dockerfile, SSH configuration and public key. Client private keys, host private keys, evidence, fleet configuration and other files are excluded from the image.

```sh
remote-fleet-agent lab status --directory ./linux-lab
remote-fleet-agent lab down --directory ./linux-lab
```

`down` removes this lab's containers and network. It retains local evidence and lab keys so the lab can be started again. It does not prune other containers, images or volumes. Stop the lab before deleting its directory. Keep `.state/` private; never commit generated lab directories or distribute them as examples.

A modified Compose file or Dockerfile is rejected by these convenience commands. Use a separate directory if you want to maintain your own container setup.

## Collect from approved Mac and Linux machines

Mac targets default to the [connection kit](MAC-ENROLLMENT.md): create it in workspace Settings and double-click it on the target. The kit handles approved Remote Login access and identity return. Native Mac checks need no Python; private Python setup runs after verified SSH by default and can be deselected.

Linux targets default to the [Linux connection kit](LINUX-ENROLLMENT.md): run `sh ./Connect.sh` as the target account. It pairs automatically, provides native fallback checks and sets up Python after verified SSH by default. The manual configuration below remains available for independently provisioned Mac and Linux access.

Add an explicit platform to each approved device:

```json
{
  "client": "example",
  "known_hosts": "./known_hosts",
  "hosts": [
    {"name": "mac-01", "target": "engineer@192.0.2.20", "platform": "macos", "approved": true, "groups": ["office"]},
    {"name": "linux-01", "target": "engineer@192.0.2.21", "platform": "linux", "port": 22, "approved": true, "groups": ["office"]}
  ]
}
```

Existing configurations default to Windows and port 22. `platform` accepts only `windows`, `macos` or `linux`. For multiple devices sharing a username/address at different ports, select by device name; the bare target is ambiguous and is rejected.

For manual registration, verify each SSH host key through a trusted console or provisioning process before adding it to `known_hosts`. Mac connection kits return the key over certificate-pinned HTTPS and verify SSH automatically. A nonstandard port needs the OpenSSH `[address]:port` host-key format.

The commands and MCP tools remain `inventory`, `diagnostics`, `readiness`, `start_job` and the existing saved-evidence tools. `describe_capabilities` lists selectable sections by platform. A section unavailable on the selected platform is rejected before connecting; a mixed-platform job needs sections supported by every selected host, or separate jobs by platform/group.

| Evidence | Linux | macOS |
| --- | --- | --- |
| OS, architecture, disk space | Kernel, distribution and root filesystem | macOS version/build and root filesystem |
| Hardware | Account-visible architecture and memory | `sysctl` model and memory |
| Software | dpkg or rpm registrations | Native system_profiler application registrations |
| Accounts | Local users and direct sudo/wheel membership | Local users and direct admin-group membership |
| Network/DNS | IP-tool metadata and resolv.conf | ifconfig metadata and scutil resolver evidence |
| Firewall | Accessible UFW state; other managers remain unknown | Application Firewall global state |
| Encryption | Unknown pending separate LUKS/device verification | Recognized FileVault status when accessible |
| Antivirus health | Unknown pending product-specific evidence | Unknown pending product-specific evidence |
| Diagnostics | Load, failed systemd units, selected reboot marker, DNS and storage | Load, account-visible launchd units, DNS and storage; reboot state unknown |

Queries do not use `sudo`, install packages, repair devices or collect password hashes, encryption recovery keys, document contents or event-message bodies. Linux probes use the private or system Python when available, with native fallback while Python is unavailable. Mac probes use native tools without Python. Missing commands, permissions, unexpected output and timeouts become per-section unknown results.

Readiness reports the ability to collect metadata and available prerequisites. It does not approve Unix maintenance. `clear_dns_cache`, Windows service actions, `run_check` and `fit_check` remain Windows-only and reject a Mac, Linux or simulated host before connecting. Existing maintenance previews also bind the platform and SSH port, so a changed endpoint requires a fresh preview.

## Platform testing limits

Docker Desktop runs Linux containers in a Linux VM. Standard Docker containers cannot validate native macOS or Windows behavior. Use the simulator for examples, and a real Mac/Apple-supported VM or a Windows VM for those operating systems' integration tests.

A container shares its kernel and does not prove physical-disk encryption, endpoint-protection health, normal service-manager operation or client fleet compatibility. Treat those checks as gaps requiring native evidence.

Sources: [Docker's virtual-machine documentation](https://docs.docker.com/desktop/features/vmm/), [loopback port publishing](https://docs.docker.com/get-started/docker-concepts/running-containers/publishing-ports/), and [Apple Remote Login guidance](https://support.apple.com/en-sg/guide/mac-help/mchlp1066/mac).


## Administration validation in the Linux lab

Use a new empty lab directory for each release validation. Each container already has a separate writable `/tmp` tmpfs limited to 16 MB. A scratch fixture belongs under `/tmp/remote-fleet-support-<run-id>` and must retain its run sentinel. The read-only root filesystem and host-key-only mount remain in place. Support tests use the unprivileged `fleet` SSH account; a permission failure is a result to report.

Keep a separate support fleet configuration with `administration_enabled: true`, register it as a lab client in a private workspace, and give it an explicit authorisation reference and access expiry. This opt-in is independent of human approval for a repair. Confirm the exact device through the workspace workflow before custom diagnostics. Prepare the scratch fixture, repair it, verify it and remove it through reviewed plans. Direct Docker exec or helper SSH writes would bypass the workflow under test.

Run native tests only with `--run-native` and a private manifest as described in [native testing](NATIVE-SUPPORT-TESTING.md). The manifest references lab key files and trusted fingerprints; it must never contain private key bytes or be committed. Stop on an unknown execution outcome. A disconnected SSH session does not make replay safe.

For a staged test, approve both device names and the exact scripts, then make the first device's check fail. The second device must retain its original marker. Record the pause and any pre-approved recovery separately. A successful shell command does not prove a user's printing or desktop problem was resolved.

The 8 November 2026 prerequisite run started two fresh controlled containers and collected inventory, diagnostics and readiness through pinned SSH. Those collections were partial, as expected for container-only service, firewall and disk evidence. This confirms endpoint connectivity and collection, not the administration scratch cycle. That cycle needs its own native report.

Stop this project with `lab down --directory <exact-lab-directory>` after the tests. Keep the evidence and keys private. Never use a global Docker prune as test cleanup.
