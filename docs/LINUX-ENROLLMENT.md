# Connect a Linux device

Linux targets default to a connection kit. Create it on the engineer computer, then run it on the device you want to support. You don't need to copy a username, IP address or SSH fingerprint, or install Python before connecting.

1. Open **Settings** as the workspace owner. Add an authorized real client with its authorization reference, or select an existing client with collection enabled and a private SSH key.
2. Choose **Create Linux connection kit**. Confirm the controller's private IPv4 address and the target site. Python setup is checked by default; you can deselect it for native checks only.
3. Copy the ZIP to one Linux device and extract it. As the named account you want to connect, run `sh ./Connect.sh`. Don't run the whole script as root. Approve sudo if the kit needs to set up SSH.
4. Keep the controller and workspace running on the same private network. Watch SSH verification and Python setup separately in Settings.

The kit contains a single-use pairing credential that expires after 30 minutes. Keep it private and delete it after use. A controller restart invalidates unused kits. Revoking pairing closes an unused listener; it does not remove a key already installed on the target.

## What the kit changes

The script adds a public key restricted to the controller's source IP to the current account's `authorized_keys`. It preserves independent keys, creates no account and changes no global SSH configuration. Forwarding, PTY allocation and user SSH startup files are disabled for this key.

If curl or OpenSSH Server is missing, the script requests sudo and installs the missing prerequisites using apt, dnf or apk. It also supports the distribution packages provided through zypper and pacman. It can start SSH through systemd, OpenRC or the distribution's service command. It preserves the firewall and existing SSH policy. Managed restrictions such as AllowUsers, a nonstandard AuthorizedKeysFile or a different SSH port may require administrator setup. The automatic pairing path uses port 22.

The target must reach the controller's temporary private-network HTTPS listener, and the controller must reach target SSH. The kit verifies the controller's embedded public TLS certificate; the controller pins the returned Ed25519 host key before registration. Target addresses come from the observed callback connection. The dashboard stays on loopback. Public forwarding and NAT relays are unsupported.

Existing manually configured Linux targets remain supported. Creating a kit does not enable general administration or bypass repair approval. Simulated clients and audit-only mode reject real enrollment.

## Python comes after connection

The controller first checks for a suitable Python 3.11+ installation over verified SSH. If one is available, it reuses it. Otherwise it transfers a pinned Python Build Standalone archive over SSH and installs Python 3.13.18 under the target account's `~/.remote-fleet-agent/python-3.13.18`. It uses no sudo, changes no system Python and leaves shell PATH alone. Linux evidence probes select this private runtime automatically.

Private runtimes cover x86_64 and aarch64/arm64 with glibc or musl. The release and SHA-256 digests are fixed in the application. The controller checks download size, unpacked size and the complete archive link graph; the target checks the archive digest again before extraction. Existing private runtime paths are never overwritten automatically. Unsupported architectures or libc detection leave the connection available and report that Python setup needs attention.

While Python is unavailable, fixed native probes provide account-visible OS, storage and other available evidence. Missing commands and unavailable security evidence remain unknown. A failed download leaves verified SSH connected. If a remote installation is interrupted, its outcome is recorded as unknown and its device exclusion claim remains until reconciled.

## Remove access

Run `sh ./Remove.sh` as the same target account. It removes the exact key added by the kit, preserving other keys and the running SSH service. Kits for the same client and controller IP share this source-bound access entry, so removing it revokes that account's access for those kits too.

Access otherwise persists until removed, or until a configured client expiry encoded in the key restriction. Pairing expiry does not remove it. A failed pairing attempt may still leave the key installed; use the removal script if you abandon setup. Remove the private Python runtime separately when it is no longer needed. Saved device records and evidence remain in the workspace.

## Validation

On 11 November 2026, an isolated Docker Linux controller paired a non-root Ubuntu target with no Python installed, collected native OS evidence, installed private Python 3.13.18 over pinned SSH, completed readiness checks and removed the kit's key. This exercised aarch64 with glibc. All four pinned Linux runtime archives passed digest and archive-safety checks. Those checks do not establish execution on every distribution, architecture, service manager or package manager.
