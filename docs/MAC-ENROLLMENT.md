# Connect a Mac

This is the default Mac target setup for the public tool. Create the kit on the engineer computer. Run it on the Mac you want to support. The target needs no Python, Homebrew or developer tools to connect.

1. Open **Settings** as the workspace owner. Add an authorized client with its authorization reference, or select an existing real client with collection enabled and a private SSH key.
2. Choose **Create Mac connection kit**. Confirm the controller's private IPv4 address and the target's site. Python setup is checked by default and runs only after verified SSH. Deselect it when you only need native checks.
3. Keep the controller and workspace running. Copy the downloaded ZIP to one Mac, extract it and double-click `Connect.command`.
4. Enter the administrator password when macOS requests it. If macOS opens Sharing settings, approve Remote Login for the current account. Return to the workspace to watch SSH verification and optional Python setup separately.

The account, address and SSH host key return automatically. There is no fingerprint or username to transcribe. macOS privacy settings and device-management policies can still require an OS approval; the kit cannot bypass them. Downloaded scripts may also require approval through Finder's Open action or Privacy & Security.

## Network and trust

The controller binds a temporary HTTPS listener to the selected private IPv4 address and a random port. The Mac must reach that port, and the controller must reach the Mac's SSH port 22. Keep both machines on the same trusted network. The main dashboard remains on loopback; port forwarding and public enrollment are unsupported. Controller firewall or managed network policies may require an inbound exception for the temporary listener.

Each kit contains a random single-use credential, valid for 30 minutes, and the controller's public TLS certificate. The Mac verifies that certificate before returning its identity. The controller uses the observed source address and verifies SSH against the returned Ed25519 key before saving the device. Existing host pins are preserved; conflicting enrollment stops for review. Revocation or controller shutdown closes an unused listener. Restarting the controller invalidates pending kits.

Kits contain an enrollment secret. Keep them private, delete them after use and never commit them. They contain neither an engineer private key nor an operator access token. Enrollment is restricted to workspace owners and is blocked for simulated clients and audit-only mode.

## Access on the Mac

The kit enables Remote Login and adds the existing Mac account to the SSH access group when that group is present. It adds one source-bound public key to that account's `authorized_keys`, preserving other entries. It disables forwarding, PTY allocation and user SSH startup files for that key. It creates no new account and changes no global SSH configuration.

Access persists until removed, or until a configured client access expiry encoded in the key restrictions. The kit's 30-minute pairing expiry does not remove the SSH key. Moving the controller to another IP requires a new reviewed access setup. A failed pairing attempt may leave its public key installed; use the removal file to revoke that access.

Run `Remove.command` as the same Mac account to remove this exact source-bound key. It preserves other keys and leaves Remote Login enabled, since another administrator may already depend on it. Removing access also prevents other kits using the same client key and controller address from connecting to this account. The workspace device record and saved evidence remain available for review.

General administration stays separately controlled. A connection kit does not enable arbitrary repairs or bypass engineer approval.

## Optional Python

After SSH verification and device registration, the controller downloads the official Python Build Standalone archive for Apple Silicon or Intel. The release, version and SHA-256 hashes are pinned in the application. Downloads and unpacked sizes are bounded, archive paths are checked, and the Mac checks the uploaded archive again before extraction.

The controller transfers it over the existing SSH connection, then installs Python 3.13.18 under the target account's `~/.remote-fleet-agent/python-3.13.18`. This uses no sudo and changes no system Python or shell PATH. The runtime is available for explicitly reviewed support work; native collectors continue using macOS tools. Existing runtime paths are never overwritten automatically.

The dashboard shows downloading, uploading, installing and ready independently of SSH connection state. A download failure leaves the verified device connected. An interrupted remote installation is recorded as unknown and retains the device's exclusion claim until reconciled; inspect it before retrying. Remove the private runtime separately if it is no longer needed.
