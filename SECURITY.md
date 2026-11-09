# Credentials and private data

Private storage checks ownership and parent-directory permissions before use. Existing directories owned by the controller account are restricted again; untrusted owners and replaceable shared parents are rejected. Keep workspaces, keys and the shared device identity/lock directory on a private local filesystem. Administrator or root access remains trusted.

AI technician roles restrict the HTTP/MCP workflow. An AI process running as the controller's OS account may also read that account's owner token and SSH keys. Use separate identities or hosts with restricted filesystem access when approval must survive a compromised AI process. Custom investigation scripts retain the endpoint account's privileges and can change state after target confirmation.

The chained local journal shares the controller's trust boundary. Keep external audit copies when an independent history is required after controller compromise.

Keep provider API keys, workspace operator tokens, SSH private keys, enrollment records and customer evidence outside Git. The toolkit does not need an AI provider key of its own; the connected AI client handles its credentials. A private repository still needs these exclusions.

Use `fleet.example.json` as the committed configuration example. Real `fleet.json` files, token files, `.state` directories, databases, local evidence, generated workspaces and captures belong in private storage. `.gitignore` covers the standard locations; arbitrary filenames still need review.

## Before committing

Install Gitleaks 8.30.1 or newer. Run `sh scripts/check-secrets.sh` after staging changes. It scans the staged patch locally, redacts findings and returns a failure if a secret is detected. The repository configuration extends Gitleaks defaults with a rule for quoted operator tokens and other credential values.

To enable the check as a local Git hook, preserve any existing hook and arrange for it to run `sh scripts/check-secrets.sh`. Git hooks do not transfer automatically when a repository is cloned. This checkout also retains the existing global author-email check.

The GitHub secret-check workflow scans the full fetched history with the same configuration. Scanners reduce risk but cannot prove that every possible secret format is absent. Review staged files as well as scanner output. Do not add exclusions just to make a credential finding pass.

If a live credential is committed, revoke or rotate it before addressing the repository history. Deleting the current file alone does not remove earlier commits. Avoid putting credentials or real customer evidence into issues, workflow logs or pull requests.

## Dependency maintenance

The supported minimums are AsyncSSH 2.24.1 and MCP 2.3.0. The dependency-security workflow audits installed packages on Windows, macOS and Linux for pushes, pull requests and weekly runs. Dependabot configuration covers Python requirements and GitHub Actions. Repository administrators should retain dependency alerts and automated security updates alongside secret scanning and push protection.
