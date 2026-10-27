from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

KEYS_DIR = Path.home() / ".remote-fleet-agent" / "keys"
SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def init_client(client: str, *, stage_dir: Path, force: bool = False) -> None:
    """Generate (or reuse) an ed25519 keypair for `client` and stage the
    install kit into `stage_dir` (e.g. a USB stick handed to the client's
    site).

    Only the public key is ever copied to `stage_dir` - the private key
    stays under KEYS_DIR on the engineer's own laptop. `install.ps1` and
    `uninstall.ps1` are copied in too when they exist in SCRIPTS_DIR (they
    are created by a later task), and skipped silently otherwise.
    """
    client_key_dir = KEYS_DIR / client
    priv_path = client_key_dir / "id_ed25519"
    if priv_path.exists() and not force:
        raise FileExistsError(f"keypair for client '{client}' already exists at {priv_path}")

    client_key_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(priv_path),
            "-C",
            f"remote-fleet-agent:{client}",
        ],
        check=True,
        capture_output=True,
    )
    priv_path.chmod(0o600)

    # WHY: only the public key is ever copied to the USB stick - the
    # private key never leaves ~/.remote-fleet-agent on the engineer's own
    # laptop. install.ps1/uninstall.ps1 are staged too, if present - they
    # may not exist yet this early in the build.
    shutil.copy(client_key_dir / "id_ed25519.pub", stage_dir / "id_ed25519.pub")
    for script_name in ("install.ps1", "uninstall.ps1"):
        src = SCRIPTS_DIR / script_name
        if src.exists():
            shutil.copy(src, stage_dir / script_name)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="remote-fleet-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init_parser = sub.add_parser(
        "init-client", help="Generate a keypair and stage the USB install kit"
    )
    init_parser.add_argument("client")
    init_parser.add_argument("--stage-dir", required=True, type=Path)
    init_parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.command == "init-client":
        init_client(args.client, stage_dir=args.stage_dir, force=args.force)
        print(f"Staged install kit for '{args.client}' at {args.stage_dir}")
