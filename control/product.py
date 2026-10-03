"""Product identity and deterministic generation of standalone packaging assets.

Forks change PRODUCT (and their assets), then run ``python -m control.product``.
The build backend also refreshes generated assets. No services are started.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import json
import logging
import os
import re
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProductIdentity:
    name: str = "KeepHarness"
    slug: str = "keepharness"
    env_prefix: str = "KEEPHARNESS"
    state_dir: str = ".local/share/keepharness"
    config_dir: str = ".config/keepharness"
    mcp_name: str = "keepharness"
    icon: str = "keepharness"
    desktop_icon: str = "utilities-terminal"
    theme_light: str = "paper"
    theme_dark: str = "graphite"
    lineage: str = "keepharness"

    def __post_init__(self):
        for value in (self.slug, self.mcp_name, self.icon, self.desktop_icon, self.theme_light, self.theme_dark, self.lineage):
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
                raise ValueError("Invalid product identifier")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", self.env_prefix):
            raise ValueError("Invalid product environment prefix")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .-]{0,79}", self.name):
            raise ValueError("Invalid product name")
        for value in (self.state_dir, self.config_dir):
            path = Path(value)
            if path.is_absolute() or ".." in path.parts or not path.parts or not re.fullmatch(r"[a-zA-Z0-9._/-]+", value):
                raise ValueError("Product directories must be relative to home")

    def state_path(self, home=None):
        return Path(home if home is not None else Path.home()) / self.state_dir

    def config_path(self, home=None):
        return Path(home if home is not None else Path.home()) / self.config_dir


# identity-source:begin
PRODUCT = ProductIdentity()
# identity-source:end

# KeepHarness was named Tail Harness before 0.15.0. Its state belongs to this lineage: the
# original identity moves those folders once and keeps their markers, so 0.14.0 can still
# open them after a rollback (see migrate_legacy_state).
LEGACY_MARKER = {"slug": "tail-harness", "lineage": "tail-harness"}
LEGACY_FOLDERS = (".local/share/tail-harness", ".config/tail-harness")
LEGACY_UNIT = ".config/systemd/user/tail-harness.service"
STOP_AND_INSTALL = "Stop Tail Harness (systemctl --user stop tail-harness.service), then run ./install.sh"

logger = logging.getLogger(__name__)


def is_original(product):
    return (product.slug, product.lineage) == ("keepharness", "keepharness")


def write_private(path, text):
    """Replace ``path`` with owner-only ``text`` atomically and durably; no temporary survives."""
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_marker(state):
    """The identity marker in ``state``, ``None`` when there is none."""
    try:
        descriptor = os.open(Path(state) / "harness.identity.json", os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor) as stream:
        return json.load(stream)


def ensure_lineage(state, product=PRODUCT):
    """Claim new state; only the historical identity may adopt unmarked old state."""
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / "harness.identity.json"
    expected = {"slug": product.slug, "lineage": product.lineage}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        old_state = any((state / name).exists() for name in ("settings.json", "runtime.json", "jobs.sqlite3", "runs", "approval_sessions.sqlite3"))
        if old_state and not is_original(product):
            raise ValueError("Refusing unmarked state from another product identity")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return ensure_lineage(state, product)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(expected, stream)
        return
    except OSError as exc:
        raise ValueError("Cannot verify product identity") from exc
    with os.fdopen(descriptor) as stream:
        try:
            actual = json.load(stream)
        except ValueError as exc:
            raise ValueError("Invalid product identity marker") from exc
    # The original identity opens Tail Harness state as is: rewriting its marker would stop
    # 0.14.0 from opening it after a rollback.
    if actual != expected and not (actual == LEGACY_MARKER and is_original(product)):
        raise ValueError("State belongs to another product identity")


def legacy_folders(home=None, product=PRODUCT):
    """The Tail Harness folders this identity adopts that exist, as ``(old, new)`` pairs."""
    if not is_original(product):
        return []
    home = Path(home if home is not None else Path.home())
    found = []
    for legacy, current in zip(LEGACY_FOLDERS, (product.state_dir, product.config_dir)):
        old = home / legacy
        try:
            marker = read_marker(old) if old.is_dir() else False
        except (OSError, ValueError):
            continue
        if marker in (None, LEGACY_MARKER, {"slug": product.slug, "lineage": product.lineage}):
            found.append((old, home / current))
    return found


def holds_only_bridge(folder):
    """Whether ``folder`` holds nothing but a client MCP bridge, which has no state to adopt.

    Before 0.15.0 ``setup-mcp.sh`` installed the bridge in the state folder, so it can sit
    where the real state must go. An empty folder is not this case: it is never merged into.
    """
    if folder.is_symlink() or not folder.is_dir():
        return False
    try:
        names = [entry.name for entry in folder.iterdir()]
    except OSError:
        return False
    return bool(names) and all(name == "venv" or name.startswith("mcp_bridge.") for name in names)


def waits_to_move(new):
    """Whether the state of the Tail Harness folder may still move to ``new``."""
    return not os.path.lexists(new) or holds_only_bridge(new)


def legacy_waiting(home=None, product=PRODUCT):
    """Why the default folders cannot be used yet (Tail Harness folders still to move), or None."""
    waiting = [str(old) for old, new in legacy_folders(home, product) if waits_to_move(new)]
    if not waiting:
        return None
    return f"{' and '.join(waiting)} have not moved to {product.name} yet. {STOP_AND_INSTALL}."


def runtime_ports(state):
    """The local ports the runtime.json in ``state`` says its harness and admin listen on."""
    try:
        runtime = json.loads((state / "runtime.json").read_text())
        ports = [runtime.get("port"), urlsplit(str(runtime.get("admin_url", ""))).port]
    except (OSError, ValueError, AttributeError):
        return []
    return [port for port in ports if type(port) is int and 0 < port < 65536]


def legacy_in_use(home):
    """Why the Tail Harness state may still be in use, or None."""
    state = home / LEGACY_FOLDERS[0]
    unit = home / LEGACY_UNIT
    if unit.exists():
        try:
            status = subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", unit.name], check=False, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            status = None  # no systemctl: the port check below still applies
        if status is not None and status.returncode == 0:
            return f"{unit.name} is active"
    if Path(sys.prefix).resolve().is_relative_to(state.resolve()):
        return f"this program runs from it ({sys.prefix})"
    for port in runtime_ports(state):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        except OSError:
            continue
        return f"127.0.0.1:{port} still answers"
    return None


def move_legacy_folders(home, product):
    """Move each waiting folder; the reason when one stays behind (in use or stuck), else None."""
    waiting = []
    for old, new in legacy_folders(home, product):
        if waits_to_move(new):
            waiting.append((old, new))
            continue
        logger.warning(
            "Both %s and %s exist: %s uses %s and leaves %s untouched. To use the older data "
            "instead, stop %s, move %s aside and run ./install.sh again; remove %s once you no "
            "longer need it.",
            old, new, product.name, new, old, product.name, new, old,
        )
    if not waiting:
        return None
    reason = legacy_in_use(home)
    if reason:
        return f"Not moving {waiting[0][0]} yet: {reason}. {STOP_AND_INSTALL}."
    for old, new in waiting:
        try:
            if os.path.lexists(new):
                set_bridge_aside(new)
            old.rename(new)
        except OSError as exc:
            return f"Could not move {old} to {new} ({exc}). {STOP_AND_INSTALL}."
        logger.warning("Moved %s to %s: the product is now %s", old, new, product.name)
        if new == product.state_path(home):
            adopt_moved_state(old, new)
    return None


def set_bridge_aside(folder):
    """Rename a bridge-only ``folder`` out of the way of the state; nothing in it is deleted."""
    aside = folder.with_name(f"{folder.name}.bridge-{time.strftime('%Y%m%d-%H%M%S')}")
    folder.rename(aside)
    logger.warning(
        "%s held only a client MCP bridge and no state: set it aside as %s. Run setup-mcp.sh "
        "again to install the bridge in its own folder.",
        folder, aside,
    )


def adopt_moved_state(old, new):
    """Point runtime.json at the moved state and mark it as Tail Harness state if unmarked."""
    runtime = new / "runtime.json"
    if runtime.is_file() and not runtime.is_symlink():
        # Rewritten on the next start too; until then the CLI reads these paths.
        text = re.sub(re.escape(json.dumps(str(old))[1:-1]) + r'(?=[/"])', lambda _: json.dumps(str(new))[1:-1], runtime.read_text())
        write_private(runtime, text)
    if read_marker(new) is None:
        write_private(new / "harness.identity.json", json.dumps(LEGACY_MARKER))


def migrate_legacy_state(home=None, product=PRODUCT):
    """Move the Tail Harness state and config folders to this identity's, never merging.

    Only install.sh and the server start call this. Returns why a folder that should move
    stays behind (it may be in use, or the system refused the move), else None: the caller
    must then stop before anything creates the new folder, or the move never happens.
    """
    home = Path(home if home is not None else Path.home())
    if not legacy_folders(home, product):
        return None
    path = (home / LEGACY_FOLDERS[0]).with_name("tail-harness.migration.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW, 0o600)) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # install.sh and a server start may race
        refused = move_legacy_folders(home, product)
        if not legacy_folders(home, product):
            # Safe while held: a process still waiting re-checks and finds nothing to move.
            path.unlink(missing_ok=True)
    return refused


def generate(root=None, product=PRODUCT):
    """Refresh the small set of static consumers; the standalone bridge stays standalone."""
    root = Path(root or Path(__file__).resolve().parents[1])
    bridge = root / "agent_service/mcp_bridge.py"
    source = bridge.read_text()
    match = re.search(r"^PRODUCT = (\{.*\})$", source, re.M)
    previous = ProductIdentity(**ast.literal_eval(match.group(1))) if match else ProductIdentity()
    # Only presentation/bootstrap files are generated. Persisted protocol identifiers
    # in Python business logic are never replaced.
    paths = ["pyproject.toml", "agent_service/index.html", "agent_service/ui.js", "agent_service/tour.js", "control/index.html", "control/admin.js", "harness_ui/assets/theme.js"]
    for relative in paths:
        path = root / relative
        text = path.read_text()
        if relative.endswith(".html"):
            text = text.replace("icons.svg#" + previous.icon, "icons.svg#__PRODUCT_ICON__")
        text = text.replace(previous.name, product.name).replace(previous.slug, product.slug)
        if relative == "harness_ui/assets/theme.js":
            text = re.sub(r"const defaultLight=.*?;", f"const defaultLight={json.dumps(product.theme_light)};", text)
            text = re.sub(r"const defaultDark=.*?;", f"const defaultDark={json.dumps(product.theme_dark)};", text)
        if relative.endswith(".html"):
            text = text.replace("icons.svg#__PRODUCT_ICON__", "icons.svg#" + product.icon)
        path.write_text(text)
    block = "PRODUCT = " + repr(asdict(product))
    source = re.sub(r"^PRODUCT = \{.*\}$", lambda _: block, source, flags=re.M)
    bridge.write_text(source)
    script = root / "agent_service/setup-mcp.sh"
    text = script.read_text()
    values = {"TH_PRODUCT_SLUG": product.slug, "TH_PRODUCT_ENV": product.env_prefix, "TH_PRODUCT_BRIDGE": product.state_dir + "-mcp", "TH_PRODUCT_MCP": product.mcp_name}
    for key, value in values.items():
        text = re.sub(r"^" + key + r"=.*$", lambda _, k=key, v=value: k + "=" + shlex.quote(v), text, flags=re.M)
    script.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, help="JSON identity for a synthetic build or fork")
    parser.add_argument("--field", choices=("venv", "slug"))
    parser.add_argument("--migrate-state", action="store_true", help="Move the folders of the product's former name (install.sh)")
    parser.add_argument("--check-migrated", action="store_true", help="Fail while those folders still wait to move (install.sh --check-only)")
    args = parser.parse_args()
    product = PRODUCT
    if args.migrate_state:
        raise SystemExit(migrate_legacy_state())  # None exits 0; a reason exits 1
    if args.check_migrated:
        raise SystemExit(legacy_waiting())  # None exits 0; a message exits 1
    if args.field:
        print(os.environ.get(product.env_prefix + "_VENV") or (os.environ.get("TH_VENV") if is_original(product) else None) or str(product.state_path() / "venv") if args.field == "venv" else product.slug)
        return
    if args.identity:
        product = ProductIdentity(**json.loads(args.identity.read_text()))
        path = Path(__file__)
        text = path.read_text()
        text = re.sub(r"(# identity-source:begin\n).*?(\n# identity-source:end)", lambda m: m[1] + "PRODUCT = ProductIdentity(**" + repr(asdict(product)) + ")" + m[2], text, flags=re.S)
        path.write_text(text)
    generate(product=product)


if __name__ == "__main__":
    main()
