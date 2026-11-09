"""Provider-neutral stdio entry point. Diagnostics go only to stderr."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(prog="remote-fleet-mcp")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--jobs", type=Path)
    parser.add_argument(
        "--enable-maintenance",
        action="store_true",
        help="Expose only maintenance actions already approved in the fleet configuration",
    )
    args = parser.parse_args()
    os.environ["REMOTE_FLEET_AGENT_CONFIG"] = str(args.config.expanduser().resolve())
    # Avoid inherited client credentials and policy accidentally selecting another engagement.
    os.environ.pop("REMOTE_FLEET_AGENT_KEY_PATH", None)
    if args.key:
        os.environ["REMOTE_FLEET_AGENT_KEY_PATH"] = str(args.key.expanduser().resolve())
    os.environ["REMOTE_FLEET_AGENT_JOB_DIR"] = str(
        (args.jobs or Path.home() / ".remote-fleet-agent/jobs").expanduser().resolve()
    )
    inherited_audit = os.environ.get("REMOTE_FLEET_AGENT_AUDIT_ONLY") == "1"
    os.environ["REMOTE_FLEET_AGENT_AUDIT_ONLY"] = (
        "1" if inherited_audit or not args.enable_maintenance else "0"
    )
    try:
        from remote_fleet_agent.server import main as serve

        asyncio.run(serve())
    except (OSError, ValueError, RuntimeError) as exc:
        print("Fleet MCP could not start: " + str(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
