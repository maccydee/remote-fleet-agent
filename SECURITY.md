# Credentials and private data

Keep provider API keys, workspace operator tokens, SSH private keys, enrollment records and customer evidence outside Git. The toolkit does not need an AI provider key of its own; the connected AI client handles its credentials. A private repository still needs these exclusions.

Use `fleet.example.json` as the committed configuration example. Real `fleet.json` files, token files, `.state` directories, databases, local evidence, generated workspaces and captures belong in private storage. `.gitignore` covers the standard locations; arbitrary filenames still need review.

## Before committing

Install Gitleaks 8.30.1 or newer. Run `sh scripts/check-secrets.sh` after staging changes. It scans the staged patch locally, redacts findings and returns a failure if a secret is detected. The repository configuration extends Gitleaks defaults with a rule for quoted operator tokens and other credential values.

To enable the check as a local Git hook, preserve any existing hook and arrange for it to run `sh scripts/check-secrets.sh`. Git hooks do not transfer automatically when a repository is cloned. This checkout also retains the existing global author-email check.

The GitHub secret-check workflow scans the full fetched history with the same configuration. Scanners reduce risk but cannot prove that every possible secret format is absent. Review staged files as well as scanner output. Do not add exclusions just to make a credential finding pass.

If a live credential is committed, revoke or rotate it before addressing the repository history. Deleting the current file alone does not remove earlier commits. Avoid putting credentials or real customer evidence into issues, workflow logs or pull requests.
