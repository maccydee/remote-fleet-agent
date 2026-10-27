# Assumptions

One entry per place the design left a gap. Each should be revisited before
this goes in front of a real client if it still matters.

## A-001 | gap-fill | OPEN
**Gap:** DESIGN.md leaves "exact GDPR/PII matching patterns" as an open
follow-up.
**Assumed:** v1's `gdpr_scan` ships three starter patterns — UK National
Insurance number (format-validated, excludes reserved prefixes), a
Luhn-validated card-number pattern, and email addresses.
**Alternatives considered:** (a) a broader off-the-shelf PII pattern set
(e.g. Microsoft Purview's sensitive-info-type list) — more thorough but a
much bigger surface to tune for false positives before first use; (b)
NI-number only, since that's the one most specific to a UK Cyber
Essentials/DCC context — too narrow, misses the most common accidental
exposure (card numbers in old finance exports).
**Why this:** three well-understood, format-checkable patterns give
real coverage without a tuning project blocking v1.

## A-002 | gap-fill | OPEN
**Gap:** DESIGN.md's plan-then-confirm rule triggers on deletes or moves
"touching more than a trivial number of files," with no number given.
**Assumed:** the threshold is **1** — i.e. *any* delete/quarantine action,
and any copy/move of more than one file, requires the plan-then-confirm
step. There is no "small enough to skip confirmation" tier in v1.
**Alternatives considered:** a numeric threshold (e.g. 10+ files) to skip
confirmation on small, low-risk operations — rejected because a single
wrong file in a client's data is still a real incident, and the
confirmation step costs the engineer one read, not real time.

## A-003 | gap-fill | RESOLVED
**Gap:** DESIGN.md doesn't specify a key type for the per-client SSH
keypair.
**Assumed:** `ed25519`. Supported by Windows OpenSSH on every version this
project targets (Win10 1809+/Server 2019+), smaller and simpler than RSA.
**Alternative considered:** RSA-4096, for maximum compatibility with very
old SSH servers — unnecessary since the target OpenSSH versions are already
pinned by the design.

## A-004 | gap-fill | OPEN
**Gap:** DESIGN.md flags "required privilege level for the SSH session" as
an open follow-up.
**Assumed:** the engineer's SSH key is added to a **local administrator**
account's `authorized_keys`, since Cyber Essentials-style checks
(`Get-MpComputerStatus`, firewall profile state, local admin group
membership) need admin rights to read. `install.ps1` does not attempt to
self-elevate or create a new account — it assumes it is itself being run
from an elevated session, and documents that requirement in the README.
**Alternative considered:** a dedicated low-privilege service account with
only the specific rights each check needs — more correct in principle, but
a significant scoping exercise per-check that isn't needed to prove the v1
design out.
