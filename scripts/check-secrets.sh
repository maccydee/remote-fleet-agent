#!/bin/sh
# Runs locally; never uploads file contents to a scanning service.
set -eu
root=$(git rev-parse --show-toplevel)
scanner=${GITLEAKS_BIN:-$(git config --get remoteFleet.gitleaksPath || true)}
scanner=${scanner:-gitleaks}
if ! command -v "$scanner" >/dev/null 2>&1; then
    echo "Commit blocked: install Gitleaks or set GITLEAKS_BIN to its executable." >&2
    exit 1
fi
exec "$scanner" git --pre-commit --staged --redact=100 --ignore-gitleaks-allow --no-banner --config "$root/.gitleaks.toml" "$root"
