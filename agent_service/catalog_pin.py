"""Owner-managed immutable catalog checkouts; updates never move an active tree."""

import hashlib
import os
import re
import subprocess
from pathlib import Path


class CatalogPinError(ValueError):
    pass


def _git(root, *arguments, strip=True):
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(root),
            *arguments,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise CatalogPinError("catalog_git_failed")
    return result.stdout.strip() if strip else result.stdout


def _verify_pin(root, commit):
    if _git(root, "rev-parse", "HEAD") != commit or _git(
        root, "status", "--porcelain", "--ignored"
    ):
        raise CatalogPinError("catalog_pin_modified")
    # Read the immutable tree, not the mutable index (which can suppress status).
    algorithm = "sha256" if len(commit) == 64 else "sha1"
    for entry in _git(root, "ls-tree", "-rz", commit, strip=False).split("\0"):
        if not entry:
            continue
        metadata, relative = entry.split("\t", 1)
        mode, kind, expected = metadata.split()
        path = Path(root) / relative
        try:
            if kind != "blob" or any(
                parent.is_symlink()
                for parent in path.parents
                if parent != Path(root) and Path(root) in parent.parents
            ):
                raise CatalogPinError("catalog_pin_modified")
            if mode == "120000" and path.is_symlink():
                content = os.fsencode(os.readlink(path))
            elif mode in ("100644", "100755") and path.is_file() and not path.is_symlink():
                if bool(path.stat().st_mode & 0o111) != (mode == "100755"):
                    raise CatalogPinError("catalog_pin_modified")
                content = path.read_bytes()
            else:
                raise CatalogPinError("catalog_pin_modified")
        except OSError:
            raise CatalogPinError("catalog_pin_modified") from None
        actual = hashlib.new(
            algorithm, b"blob " + str(len(content)).encode() + b"\0" + content
        ).hexdigest()
        if actual != expected:
            raise CatalogPinError("catalog_pin_modified")


def _catalog_id(catalog):
    value = catalog.get("id", "")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise CatalogPinError("invalid_catalog_id")
    return value


def pin_catalog(catalog, state_dir, ref, *, owner=False):
    if owner is not True:
        raise CatalogPinError("catalog_owner_required")
    if catalog.get("trusted") is not True or catalog.get("kind") != "git":
        raise CatalogPinError("catalog_git_required")
    catalog_id = _catalog_id(catalog)
    if not isinstance(ref, str) or not ref or ref.startswith("-") or len(ref) > 200:
        raise CatalogPinError("invalid_catalog_ref")
    root = Path(catalog["root"]).resolve()
    commit = _git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    target = Path(state_dir).resolve() / "catalog_pins" / catalog_id / commit
    if target.is_relative_to(root):
        raise CatalogPinError("catalog_state_inside_source")
    state = Path(state_dir).resolve()
    if any(path.is_symlink() for path in (state / "catalog_pins", target.parent, target)):
        raise CatalogPinError("catalog_pin_symlink")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        _git(root, "worktree", "add", "--detach", str(target), commit)
        # Do not traverse links: tracked links must not change external permissions.
        for directory, directories, files in os.walk(target, followlinks=False):
            for name in files + directories:
                path = Path(directory) / name
                if not path.is_symlink():
                    path.chmod(path.stat().st_mode & ~0o222)
        target.chmod(target.stat().st_mode & ~0o222)
    _verify_pin(target, commit)
    return {"commit": commit, "root": str(target)}


def _revisions(root):
    result = {}
    root = Path(root)
    # Include all versioned dependencies and manifest-defined locations, not just
    # conventional resource directories. Links are hashed as links, never followed.
    for relative in _git(root, "ls-files", "-z", strip=False).split("\0"):
        if not relative:
            continue
        path = root / relative
        if path.is_symlink():
            content = os.readlink(path).encode()
        elif path.is_file() and path.resolve().is_relative_to(root.resolve()):
            content = path.read_bytes()
        else:
            continue
        result[relative] = hashlib.sha256(content).hexdigest()
    return result


def preview_update(catalog, pin, state_dir, ref, *, owner=False, fetch=True):
    if owner is not True:
        raise CatalogPinError("catalog_owner_required")
    if fetch:
        _git(catalog["root"], "fetch", "--all", "--prune")
    candidate = pin_catalog(catalog, state_dir, ref, owner=owner)
    before, after = _revisions(pin["root"]), _revisions(candidate["root"])
    diff = [
        {
            "resource_id": "catalog/" + _catalog_id(catalog) + "/" + path,
            "before": before.get(path),
            "after": after.get(path),
        }
        for path in sorted(before.keys() | after.keys())
        if before.get(path) != after.get(path)
    ]
    return {"pin": candidate, "diff": diff}


def effective_catalogs(config, project):
    result = []
    state = Path(config.get("control_state_dir", config.get("state_dir", "state"))).resolve()
    for catalog in config.get("catalogs", []):
        if catalog.get("trusted") is not True or catalog.get("id") not in project.get(
            "catalogs", []
        ):
            continue
        value = dict(catalog)
        pin = project.get("catalog_pins", {}).get(catalog["id"])
        if pin:
            commit = pin.get("commit", "")
            if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                raise CatalogPinError("invalid_catalog_pin")
            expected = state / "catalog_pins" / _catalog_id(catalog) / commit
            root = Path(pin.get("root", expected)).resolve()
            if root != expected or not root.is_dir():
                raise CatalogPinError("invalid_catalog_pin")
            _verify_pin(root, commit)
            value.update(root=str(root), pin={"commit": commit, "root": str(root)})
        result.append(value)
    return result


def snapshot_catalogs(config, project):
    result = []
    for catalog in effective_catalogs(config, project):
        commit, dirty = None, None
        if catalog.get("kind") == "git":
            commit = _git(catalog["root"], "rev-parse", "HEAD")
            dirty = bool(_git(catalog["root"], "status", "--porcelain", "--ignored"))
        result.append(
            {
                "catalog_id": catalog["id"],
                "commit": commit,
                "dirty": dirty,
                "pinned": bool(catalog.get("pin")),
            }
        )
    return result
