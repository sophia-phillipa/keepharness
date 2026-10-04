"""Console entry point, usable from an installed wheel or a source checkout."""

import argparse
import asyncio
import json
import os
import socket
import sys
import time
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
    sign_in = commands.add_parser(
        "open", help="Sign a browser on this computer in with a one-time link, and open it"
    )
    sign_in.add_argument(
        "--revoke", action="store_true", help="Sign every browser on this computer out instead"
    )
    save = commands.add_parser(
        "backup", help="Write a backup of the state (without provider logins and keys by default)"
    )
    save.add_argument("--output", help="Archive to create (default: keepharness-backup-<time>.tar.gz)")
    save.add_argument(
        "--with-secrets",
        action="store_true",
        help="Also include provider logins, keys and sessions, in clear in the archive",
    )
    put_back = commands.add_parser(
        "restore", help="Show how a backup would be restored; --apply restores it"
    )
    put_back.add_argument("archive")
    put_back.add_argument("--apply", action="store_true", help="Restore (default: print the plan)")
    put_back.add_argument(
        "--replace",
        action="store_true",
        help="Move the data already in the state folder aside (never deleted) before restoring",
    )
    args = parser.parse_args(argv)
    os.umask(0o077)
    default_state = args.state == parser.get_default("state")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.command == "approve-device":
        approve_device(parser, args, default_state)
        return
    if args.command in ("backup", "restore"):
        run_backup(args)
        return
    if args.command == "open" and args.revoke:
        revoke_browsers(parser, Path(args.state))
        return
    if args.command == "open":
        open_browser(parser, Path(args.state), args.port)
        return
    if args.scan:
        print(json.dumps(asyncio.run(scan()), indent=2, ensure_ascii=False))
        return
    serve(args, default_state)


def approve_device(parser, args, default_state):
    """`keepharness approve-device`: enroll a browser, or revoke human approval authority."""
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


def run_backup(args):
    """`keepharness backup` and `keepharness restore`: a refusal or failure exits with its reason."""
    import sqlite3

    from . import backup

    state = Path(args.state)
    try:
        if args.command == "restore":
            print(backup.restore(Path(args.archive), state, apply=args.apply, replace=args.replace))
            return
        output = Path(args.output or f"{PRODUCT.slug}-backup-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz")
        manifest = backup.create(state, output, with_secrets=args.with_secrets)
    except (backup.BackupRefused, OSError, sqlite3.Error) as exc:
        raise SystemExit(f"{args.command.capitalize()} failed: {exc}") from None
    print(f"Backed up {state} to {output}: {manifest['files']} files, {len(manifest['databases'])} databases.")
    print(
        "It includes provider logins and keys in clear: keep it private."
        if args.with_secrets
        else "It is without secrets: sign in to the providers again after a restore on another computer."
    )


def revoke_browsers(parser, state):
    """`keepharness open --revoke`: every browser signed in on this computer must open again."""
    from .local_access import revoke_sessions

    if not state.is_dir():
        parser.error(f"No {PRODUCT.name} state at {state}; start {PRODUCT.slug} first.")
    print(f"Signed out {revoke_sessions(state)} browser session(s).")


def open_link(state, port):
    """A one-time link that signs this computer's browser in; the secret only signs it."""
    from .local_access import OPEN_PATH, ensure_secret, open_ticket

    return f"http://127.0.0.1:{port}{OPEN_PATH}?ticket={open_ticket(ensure_secret(state))}"


def open_browser(parser, state, port):
    """`keepharness open`: print a one-time link for this computer's browser and open it."""
    import webbrowser

    if not state.is_dir():
        parser.error(f"No {PRODUCT.name} state at {state}; start {PRODUCT.slug} first.")
    link = open_link(state, port)
    print("Open this single-use link in your browser within 5 minutes:")
    print(link)
    webbrowser.open(link)


def serve(args, default_state):
    """Start the admin server once its port is free and its state folder may be used."""
    import uvicorn

    from agent_service.log_config import configure_logging

    from .env import warn_legacy_names
    from .server import create_app

    if busy := port_taken(args.port):
        raise SystemExit(busy)
    # Only install.sh moves Tail Harness state (decision D24). Starting now would create an
    # empty new folder beside the old one, and the move would then never happen.
    if default_state and (waiting := legacy_waiting()):
        raise SystemExit(migration_refusal(Path.home()) or waiting)

    configure_logging(args.state, filename="admin.log")
    warn_legacy_names()

    app = create_app(args.state, args.port)
    print(f"Local management: http://127.0.0.1:{args.port}/", flush=True)
    # A link in a service journal could reach other accounts; only a terminal gets one.
    if sys.stdout.isatty():
        print("Sign this computer's browser in: " + open_link(Path(args.state), args.port))
    else:
        print(f"Run `{PRODUCT.slug} open` to sign this computer's browser in.", flush=True)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        proxy_headers=False,
        access_log=False,
    )
