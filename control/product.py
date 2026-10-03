"""Product identity and deterministic generation of standalone packaging assets.

Forks change PRODUCT (and their assets), then run ``python -m control.product``.
The build backend also refreshes generated assets. No services are started.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import shlex
from dataclasses import asdict, dataclass
from pathlib import Path


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
# original identity moves those folders once and adopts their markers (see migrate_legacy_state).
LEGACY_MARKER = {"slug": "tail-harness", "lineage": "tail-harness"}
LEGACY_FOLDERS = (".local/share/tail-harness", ".config/tail-harness")

logger = logging.getLogger(__name__)


def is_original(product):
    return (product.slug, product.lineage) == ("keepharness", "keepharness")


def write_marker(path, value):
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
    os.replace(temporary, path)


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
    if actual == LEGACY_MARKER and is_original(product):
        write_marker(path, expected)
    elif actual != expected:
        raise ValueError("State belongs to another product identity")


def migrate_legacy_state(home=None, product=PRODUCT):
    """Move the Tail Harness state and config folders to this identity's, never merging."""
    if not is_original(product):
        return
    home = Path(home if home is not None else Path.home())
    for legacy, current in zip(LEGACY_FOLDERS, (product.state_dir, product.config_dir)):
        old, new = home / legacy, home / current
        if not old.is_dir() or os.path.lexists(new):
            continue
        try:
            marker = read_marker(old)
        except (OSError, ValueError):
            continue
        if marker not in (None, LEGACY_MARKER, {"slug": product.slug, "lineage": product.lineage}):
            continue
        old.rename(new)
        logger.warning("Moved %s to %s: the product is now %s", old, new, product.name)
        runtime = new / "runtime.json"
        if runtime.is_file() and not runtime.is_symlink():
            # Rewritten on the next start too; until then the CLI reads these paths.
            text = re.sub(re.escape(json.dumps(str(old))[1:-1]) + r'(?=[/"])', lambda _: json.dumps(str(new))[1:-1], runtime.read_text())
            runtime.write_text(text)
        if current == product.state_dir:
            ensure_lineage(new, product)


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
    values = {"TH_PRODUCT_SLUG": product.slug, "TH_PRODUCT_ENV": product.env_prefix, "TH_PRODUCT_STATE": product.state_dir, "TH_PRODUCT_MCP": product.mcp_name}
    for key, value in values.items():
        text = re.sub(r"^" + key + r"=.*$", lambda _, k=key, v=value: k + "=" + shlex.quote(v), text, flags=re.M)
    script.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", type=Path, help="JSON identity for a synthetic build or fork")
    parser.add_argument("--field", choices=("venv", "slug"))
    parser.add_argument("--migrate-state", action="store_true", help="Move the folders of the product's former name (install.sh)")
    args = parser.parse_args()
    product = PRODUCT
    if args.migrate_state:
        migrate_legacy_state()
        return
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
