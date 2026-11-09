# Assessment deployment and recovery

`init-client` stages the packaged assessment scripts rather than the older prototypes in `scripts/`. This works from a wheel installation without a source checkout.

```sh
remote-fleet-agent init-client example --stage-dir ./example-kit --allowed-ip 192.0.2.10
```

Use the engineer laptop's actual IPv4 address. If omitted, the generated command contains `ENGINEER_IPV4`, which must be replaced before use. The kit includes only the public key, PS1 and matching TXT script copies, instructions and a command to run from elevated Windows PowerShell. `--force` means reuse the existing keypair and restage, not key rotation. Client names use lowercase letters, numbers and hyphens, up to 48 characters. Private keys stay on the engineer laptop.

## Endpoint lifecycle

The assessment installer requires administrative PowerShell, an existing Microsoft OpenSSH Server capability or a working installation source, effective local firewall rules, and two GB of free system-drive headroom. This threshold is a conservative preflight, not a guarantee that Windows servicing has enough space for every build.

The installer records the baseline under `%ProgramData%\RemoteFleetAgent-<engagement>`, protects it for administrators and SYSTEM, and writes state atomically before changing access. The baseline includes original OpenSSH state, whether this key was already present, original authorized-key permissions, firewall profile state and cleanup details. Repairs keep the original baseline and refresh expiry.

The kit includes `Protect-Assessment.ps1` alongside installation and cleanup. Keep these files together. Privileged cleanup storage is created with an Administrators/SYSTEM owner and protected ACL; existing state and script files are checked before being trusted. A user-owned folder, unexpected writable grant or reparse point stops installation before saved state is read. The prototype `scripts/install.ps1` is retired and refuses new installation. Existing prototype cleanup tasks need administrator review and are not silently removed.

Before enabling access, it copies cleanup locally and registers a SYSTEM task. It enables firewall profiles and creates a TCP/22 allow rule for the engineer address, with temporary blocks covering all other IPv4 addresses and all IPv6 sources. Existing unrelated allow rules stay intact. The effective address coverage is checked rather than relying on Windows' textual representation. Group policy that prevents effective local rules causes installation to stop. Existing SSH blocks can still prevent connectivity; the installer does not weaken central policy or promise to override every machine configuration.

The default expiry is three days, configurable through `-ExpiryDays` from one to fourteen. The task starts when available and offers hourly retries for seven days. Its launcher uses an encoded PowerShell command so paths do not depend on command-line quote interpretation. Launch errors are saved under the protected state directory where storage permits. This is a best-effort lifecycle, not a guaranteed revocation deadline if the machine stays offline, Task Scheduler is broken or cleanup is denied.

The installer checks for an installed OpenSSH capability before adding it. `-LocalSourcePath` supports a matching Microsoft feature source where Windows Update is unavailable. Windows servicing can still take a long time; a hard deadline for the underlying servicing operation is not implemented. Disk-full and restart-required outcomes stop retries. The script does not delete user files or force a restart to overcome those conditions.

After installation, verify the displayed host fingerprint at the Windows console and populate the engagement's known-hosts file. The result is `ready_for_connection_test`, not proof that a laptop connection succeeded. Run one approved inventory query before deploying further. The resulting SSH credential retains administrator privileges; read-only and maintenance settings are application controls.

## Cleanup and recovery

Use the copied local cleanup script and exact state directory:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:ProgramData\RemoteFleetAgent-example\Remove-Assessment.ps1" -StateDirectory "$env:ProgramData\RemoteFleetAgent-example"
```

Cleanup removes only a key newly added by this assessment, removes its rules, restores recorded service and firewall state, and removes its task after successful restoration. If OpenSSH was originally installed, it is retained. Failure preserves the baseline and retry task. Receipt storage is best-effort and cannot block access revocation. Missing state is an error; cleanup does not guess the original setup. Run from a console if removal would cut the only management connection.

After a failure, retain `state.json`, any backup and `scheduled-cleanup-error.txt` or `cleanup-result.json`. Resolve the reported condition and retry cleanup explicitly. Do not delete the journal just to make installation continue. Avoid concurrent assessments or engineer-address changes on one endpoint: assessment block rules can conflict, and the scripts do not coordinate independent engagements or unrelated administrators.

## Validation record

On the Windows 11 test PC, a separate temporary key authenticated after first installation and repeat installation. Repeat installation preserved the original baseline. The revised encoded launcher successfully ran cleanup as SYSTEM: the temporary key, three rules and task were removed, the original key still connected, and original key-file contents/permissions, SSH state and firewall profile state matched the baseline. A separate reboot confirmed SSH autostart, pinned-key reconnection and preservation of the original assessment cleanup task. This tests an endpoint where OpenSSH was already installed; a fresh feature installation/removal and other Windows versions/policies still need separate machines. Local tests cover the address calculations, atomic journal replacement, key staging and reuse, maintenance controls, fleet concurrency and controller interruption. Those tests cannot substitute for real Windows policy, feature installation, reboot or endpoint validation. No public source licence has been selected and no publication is implied by these packages.


## Controller deployment and retained work

An engineer can run the controller locally while using their chosen AI client for conversations. An optional self-hosted controller needs continuous endpoint reachability and a persistent private workspace. Moving it to another computer requires host-native Python paths, SSH key protection and freshly generated AI setup profiles. Keep endpoint access expiry in force.

The workspace contains operator credentials, engagement paths, case evidence and executable repair plans. Store it on a private local filesystem and back it up as a unit with its referenced configurations and verified host keys. Use a consistent SQLite backup while the service is running, or stop it cleanly before copying the database and associated files. Test restoration into a different private directory before relying on the backup. Keep keys and owner access files out of marketing captures and public packages.

The current HTTP application defaults to loopback. A persistent controller is not permission to expose that listener publicly. A supported remote-access deployment needs an authenticated access layer, transport protection and tested session/Origin/Host handling; a reverse proxy alone does not establish those properties. Run with the least endpoint privilege needed for the intended task. An arbitrary diagnostic script can still mutate anything its SSH account can change.

Before an upgrade, preserve the workspace and review the release's capability flags. Reading an old case or approval must not expand its execution authority. Retain the approved plan digest and original result. After an interrupted remote send, inspect the recorded unknown outcome before proposing new work; restarting the controller does not prove the endpoint command stopped.

A scheduled job also needs the controller to be running. AI-client shutdown is independent of controller shutdown. Verify the release's scheduler health, drain and restart tests before treating it as an always-on service. Installer expiry, backup restoration, controller hosting and endpoint repair are separate release checks.
