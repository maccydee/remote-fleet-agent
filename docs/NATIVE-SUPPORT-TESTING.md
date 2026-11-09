# Native support testing

Native tests use an explicitly named private manifest. Without `--run-native`, pytest skips the suite before loading credentials or making connections. With the flag but no manifest, the result is BLOCKED, represented as a skipped pytest prerequisite; it is never native PASS.

The harness implements enrollment and pinned SSH preflight, a technician-only controller API adapter, reviewable scratch scripts and offline sentinel/cleanup tests. A preflight pass proves SSH identity. It does not prove a native repair, endpoint OS metadata or a human approval. Native scratch cycles, controlled-process recovery, canary failure and uncertain-dispatch tests remain separate release gates.

## Prepare the manifest

Keep the manifest and reports outside the repository, in an account-private directory. Use current enrollment rather than yesterday's screenshot. Each target must match the workspace client config, account, address, port, platform, authorisation reference and exact existing access expiry. The controller must already permit administration. The harness never enables administration, installs OpenSSH, turns on Remote Login or extends expiry.

Example shape, using fictional values:

```json
{
  "schema": 1,
  "workspace": "/private/path/workspace",
  "controller_platform": "macos",
  "workspace_url": "http://127.0.0.1:8877",
  "operator_token_file": "/private/path/lab-technician-token.txt",
  "approval_wait_seconds": 0,
  "targets": [{
    "client": "private-lab",
    "device": "linux-a",
    "platform": "linux",
    "account": "fleet",
    "address": "127.0.0.1",
    "port": 2222,
    "fingerprint": "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "scratch_parent": "/tmp",
    "authorisation_reference": "current-written-lab-authorisation",
    "expires_at": "2026-11-09T18:00:00+00:00"
  }]
}
```

Replace the example fingerprint with the independently verified public fingerprint. Private key paths come from current enrollment; the manifest contains no private key or token contents. A two-device Linux test needs two distinct enrolled loopback endpoints and distinct pinned identities. The Windows test expects DESKTOP-6PB6U6S. Mac tests require an already available enrolled SSH account; a local subprocess is not Mac endpoint proof.

## Run preflight

```sh
REMOTE_FLEET_NATIVE_MANIFEST=/private/path/native-manifest.json \
  .venv/bin/python -m pytest tests/native --run-native -q

.venv/bin/python scripts/validate-support.py preflight \
  --manifest /private/path/native-manifest.json --device linux-a \
  --output /private/path/native-results
```

Preflight connects with the enrolled private key and known-hosts file, then compares the actual server key to the manifest fingerprint before any endpoint command. No shell command runs during this check. Unknown keys or mismatches fail.

## Scratch proposals and human approval

`prepare`, `run`, `recover` and `cleanup` use a dedicated technician token to request a confirmed investigation, collect endpoint context and submit exact scratch plans through the live controller API. The token adapter refuses owner/manager credentials. Pending engineer confirmation or approval is BLOCKED, with a review link. It never approves on the engineer's behalf or executes a helper SSH mutation.

Approve the named investigation in the workspace, then resume with its `--session-id`. Review the generated repair plan and resume using `--plan-id`, the same session id and run id. The CLI checks that the saved plan contains exactly the expected scratch change and verification scripts before execution. `--approval-wait-seconds` can wait briefly for a separate engineer decision. No timeout implies consent. The full-cycle native test requests separate decisions for prepare, repair, recovery and cleanup. If it pauses after creating scratch, retain that path and plan record; cleanup still requires review.

Generate `prepare` first and retain its random run id. Later phases require that exact `--run-id`. The endpoint script creates a dedicated child directory with a sentinel containing the run id and a marker containing `broken`. Repair writes `healthy`; recovery writes `broken`. Each subsequent write verifies the sentinel and rejects symlink markers. Cleanup refuses unexpected children and removes only the named marker, sentinel and empty child directory. It never recursively deletes a parent.

Use the generated scripts in a controller plan, with a distinct engineer decision and the exact displayed digest. Retain the review link, engineer identity/time and result. The test technician must never possess an owner approval token. Label pending human review BLOCKED. Record unknown dispatch as unknown and stop; do not replay it to obtain a green test.

## Evidence and limits

For each run retain expected/observed identity, OS/account/session context, source revision or diff hash, executable plan digest, human decision actor/time, measured duration, output limits, verification, recovery and cleanup. A successful check does not establish a real user outcome. Report the latter as pending until confirmed.

Keep separate rows for controller host and endpoint OS. Mac driving a Windows endpoint does not prove a Windows controller. Linux Docker proves container behaviour, not native systemd, kernel management, physical printing or desktop sessions. Outlook repair and printer driver installation need their own supervised scenarios. Do not inject faults into those applications to satisfy the scratch suite.

Current native state must come from newly generated reports. Historical Windows test artifacts do not establish today's address, free space, access expiry or ability to run new administration plans.

On 8 November 2026, a separate authorised read-only check reached the Windows test PC, matched its previously pinned public key and observed DESKTOP-6PB6U6S, PowerShell 5.1, a running sshd service and available C: space. That check did not establish current enrollment expiry or execute any native scratch repair. Its private report is kept with this execution's evidence, outside the repository.
