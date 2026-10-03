"""Console entry point, usable from an installed wheel or a source checkout."""

import argparse
import asyncio
import json
import os
import socket
import sys
from pathlib import Path

from .discovery import scan
from .product import (
    PRODUCT,
    describe,
    ensure_lineage,
    legacy_waiting,
    migration_refusal,
    port_holders,
)


def port_taken(port):
    """Why 127.0.0.1:``port`` cannot be bound, or None.

    uvicorn runs the lifespan (discovery, provider checks) before it binds, so a busy port is
    checked first instead of after that work, at every restart of the unit.
    """
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # as uvicorn binds
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            holders = describe(port_holders(port)) or "another program"
            return f"127.0.0.1:{port} is already in use by {holders} ({exc.strerror})."
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"{PRODUCT.name} — local AI services")
    parser.add_argument("--scan", action="store_true", help="Read-only local inventory")
    parser.add_argument("--port", type=int, default=8094)
    parser.add_argument("--state", default=str(PRODUCT.state_path()))
    commands = parser.add_subparsers(dest="command")
    enroll = commands.add_parser(
        "approve-device", help="Enroll a browser or revoke human approval authority"
    )
    target = enroll.add_mutually_exclusive_group(required=True)
    target.add_argument("--owner", help="Existing owner id or configured tailnet login")
    target.add_argument(
        "--all", action="store_true", help="Revoke every owner's sessions and links"
    )
    enroll.add_argument(
        "--revoke", action="store_true", help="Revoke sessions and pending enrollment links"
    )
    enroll.add_argument(
        "--yes", action="store_true", help="Confirm enrollment without an interactive terminal"
    )
    args = parser.parse_args(argv)
    os.umask(0o077)
    default_state = args.state == parser.get_default("state")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.command == "approve-device":
        from agent_service.approval_sessions import issue_enrollment, revoke_sessions
        from agent_service.errors import APIError

        if args.all and not args.revoke:
            parser.error("--all requires --revoke")
        # Creating the new folder here would make the pending move refuse to merge.
        if default_state and (waiting := legacy_waiting()):
            parser.error(waiting)
        try:
            ensure_lineage(Path(args.state), PRODUCT)
            config = json.loads((Path(args.state) / "runtime.json").read_text())
            ensure_lineage(Path(config["state_dir"]), PRODUCT)
            owner = config.get("tailscale_logins", {}).get(args.owner, args.owner)
            if args.revoke:
                revoke_sessions(config, owner)
                print("Revoked approval sessions and pending links for " + (owner or "all owners"))
                return
            if owner not in config["clients"]:
                raise APIError("approval_owner_unknown", 403)
            origin = (config.get("browser_url") or f"http://127.0.0.1:{config['port']}").rstrip("/")
            if origin not in config.get("origins", []):
                parser.error("The browser URL must be a configured harness origin")
            if not args.yes:
                if not sys.stdin.isatty():
                    parser.error(
                        "Enrollment requires terminal confirmation; use --yes for automation"
                    )
                answer = input(f"Enable browser approval authority for owner {owner!r}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    parser.error("Enrollment cancelled")
            nonce = issue_enrollment(config, owner)
        except (OSError, ValueError, KeyError, EOFError, APIError) as exc:
            parser.error(f"Could not update approval authority: {exc}")
        print("Open this single-use link in the owner's browser within 10 minutes:")
        print(origin + "/approve-device?nonce=" + nonce)
        return
    if args.scan:
        print(json.dumps(asyncio.run(scan()), indent=2, ensure_ascii=False))
        return
    serve(args, default_state)


def serve(args, default_state):
    """Start the admin server once its port is free and its state folder may be used."""
    import uvicorn

    from agent_service.log_config import configure_logging

    from .env import warn_legacy_names
    from .server import create_app

    if busy := port_taken(args.port):
        raise SystemExit(busy)
    configure_logging()
    warn_legacy_names()
    # Only install.sh moves Tail Harness state (decision D24). Starting now would create an
    # empty new folder beside the old one, and the move would then never happen.
    if default_state and (waiting := legacy_waiting()):
        raise SystemExit(migration_refusal(Path.home()) or waiting)

    print(f"Local management: http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run(
        create_app(args.state, args.port),
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
    )
