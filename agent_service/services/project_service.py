"""Project registration, access checks and primary-folder deletion."""

import asyncio
import hashlib
import os
import shutil
import unicodedata
import uuid
from pathlib import Path

from .. import workspaces
from ..config import REPOSITORY_ROOT
from ..errors import APIError
from ..persistence.db import encoded
from ..private_storage import private_roots


class ProjectService:
    def __init__(self, config, root, db, project_repository, conversation_repository):
        self.config, self.root, self.db = config, root, db
        self.project_repository = project_repository
        self.conversation_repository = conversation_repository
        # ConversationService aliases both sets: mutate them in place, never rebind.
        self.deleted_project_folders = project_repository.deleted_folders()
        self.deleting_project_folders = set()

    def project(self, identity, project):
        client = self.config.get("clients", {}).get(identity[0], {})
        if project not in client.get("projects", []) or project not in self.config["projects"]:
            raise APIError("project_denied", 403)
        return self.config["projects"][project]

    def share_projects(self, config=None):
        """Every project reaches every provider and every client (the owner). ``config`` is a
        runtime candidate not installed yet (default: the live one)."""
        config = self.config if config is None else config
        projects = list(config["projects"])
        for spec in config.get("services", {}).values():
            spec["projects"] = projects.copy()
        for client in config.get("clients", {}).values():
            client["projects"] = projects.copy()

    def add_project(self, data, project_id=None):
        if not self.config.get("project_registration"):
            raise APIError("project_registration_disabled", 403)
        if not isinstance(data, dict):
            raise APIError("invalid_project")
        # An empty "paths" list is a folder-less project: chats and Space pages only.
        raw_paths = data.get("paths", [data.get("root")])
        label = data.get("name", data.get("label", ""))
        if (
            not isinstance(raw_paths, list)
            or len(raw_paths) > 20
            or not all(
                isinstance(raw, str) and raw.strip() and not any(ord(c) < 32 for c in raw)
                for raw in raw_paths
            )
        ):
            raise APIError("invalid_project")
        roots = []
        home = Path.home().resolve()
        protected = [
            home / ".ssh",
            home / ".codex",
            home / ".claude",
            home / ".gemini",
            home / ".config",
            *(path.resolve() for path in private_roots(self.config, self.root)),
        ]
        for raw in raw_paths:
            root = Path(raw.strip()).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise APIError("project_directory_required")
            root = root.resolve()
            if root in (Path("/"), home) or any(
                root.is_relative_to(p) or p.is_relative_to(root) for p in protected
            ):
                raise APIError("project_directory_forbidden", 403)
            if str(root) not in roots:
                roots.append(str(root))
        # Preserve idempotent registration for older single-root clients without a name.
        if project_id is None and "paths" not in data and not label:
            for pid, spec in self.config["projects"].items():
                if spec.get("root") and str(Path(spec["root"]).resolve()) == roots[0]:
                    return pid
            label = Path(roots[0]).name
        if not isinstance(label, str):
            raise APIError("invalid_project_name")
        label = unicodedata.normalize("NFKC", " ".join(label.split()))
        if (
            len(label) > 100
            or sum(c.isalpha() for c in label) < 3
            or any(ord(c) < 32 for c in label)
        ):
            raise APIError("invalid_project_name")
        if any(
            unicodedata.normalize("NFKC", " ".join(spec.get("label", pid).split())).casefold()
            == label.casefold()
            for pid, spec in self.config["projects"].items()
            if pid != project_id
        ):
            raise APIError("project_name_exists", 409)
        pid = project_id or "project-" + uuid.uuid4().hex[:12]
        spec = (
            dict(self.config["projects"][pid])
            if project_id
            else {"test_commands": {}, "node_binary": shutil.which("node") or "node"}
        )
        spec.update(label=label, root=roots[0] if roots else None, additional_roots=roots[1:])
        with self.db:
            self.project_repository.register(pid, encoded(spec))
        self.config["projects"][pid] = spec
        self.share_projects()
        return pid

    def project_folder_deletion(self, identity, project):
        spec = self.project(identity, project)
        if project in self.deleting_project_folders:
            raise APIError("project_folder_busy", 409)
        if not spec.get("root"):
            raise APIError("project_directory_required")
        if project in self.deleted_project_folders:
            raise APIError("project_folder_deleted", 410)
        root = Path(spec["root"])
        if not root.is_absolute() or any(part.is_symlink() for part in (root, *root.parents)):
            raise APIError("project_root_unavailable", 422)
        if root.exists() and not root.is_dir():
            raise APIError("project_root_unavailable", 422)
        root = root.resolve()
        home = Path.home().resolve()
        protected = [
            *(path.resolve() for path in private_roots(self.config, self.root)),
            REPOSITORY_ROOT,
            *(home / name for name in (".ssh", ".aws", ".codex", ".claude", ".gemini", ".config")),
            *(
                Path(name).resolve()
                for name in (
                    "/etc",
                    "/usr",
                    "/bin",
                    "/sbin",
                    "/System",
                    "/Library",
                    "/Applications",
                    "/dev",
                    "/proc",
                    "/sys",
                    "/boot",
                    "/ostree",
                )
            ),
        ]
        if (
            root == Path(root.anchor)
            or root == home
            or home.is_relative_to(root)
            or root.is_mount()
            or any(root.is_relative_to(p) or p.is_relative_to(root) for p in protected)
        ):
            raise APIError("project_directory_forbidden", 403)
        aliases = {project}
        for pid, other in self.config["projects"].items():
            if pid == project:
                continue
            other_primary = Path(other["root"]) if other.get("root") else None
            same_primary = bool(
                other_primary
                and (
                    (root.exists() and other_primary.exists() and root.samefile(other_primary))
                    or (
                        root.name == other_primary.name
                        and root.parent.exists()
                        and other_primary.parent.exists()
                        and root.parent.samefile(other_primary.parent)
                    )
                )
            )
            if same_primary and spec.get("label", project) == other.get("label", pid):
                if pid not in identity[1]["projects"]:
                    raise APIError("project_directory_shared", 409)
                aliases.add(pid)
                continue
            for _, raw in workspaces.project_roots(other):
                other_root = Path(raw).resolve()
                if (
                    root.is_relative_to(other_root)
                    or other_root.is_relative_to(root)
                    or (
                        other_root.exists()
                        and any(
                            parent.exists() and parent.samefile(other_root)
                            for parent in (root, *root.parents)
                        )
                    )
                    or (
                        root.exists()
                        and any(
                            parent.exists() and parent.samefile(root)
                            for parent in (other_root, *other_root.parents)
                        )
                    )
                ):
                    raise APIError("project_directory_shared", 409)
        if aliases & self.deleting_project_folders or any(
            self.conversation_repository.project_busy(pid) for pid in aliases
        ):
            raise APIError("project_folder_busy", 409)
        info = root.stat() if root.exists() else None
        revision = hashlib.sha256(
            encoded(
                [
                    project,
                    str(root),
                    *([info.st_dev, info.st_ino, info.st_ctime_ns] if info else [None]),
                ]
            ).encode()
        ).hexdigest()
        return {
            "paths": [str(root)],
            "revision": revision,
            "project_ids": sorted(aliases),
            "missing": info is None,
        }

    async def delete_project_folder(self, identity, project, data):
        if not isinstance(data, dict) or data.get("confirmed") is not True:
            raise APIError("project_folder_confirmation_required")
        preview = self.project_folder_deletion(identity, project)
        if (
            data.get("paths") != preview["paths"]
            or data.get("revision") != preview["revision"]
            or data.get("project_ids") != preview["project_ids"]
        ):
            raise APIError("project_folder_changed", 409)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise APIError("project_folder_deletion_unsupported", 503)
        root = Path(preview["paths"][0])

        def remove():
            # Anchor deletion to opened directories, never follow a replaced parent symlink.
            fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for part in root.parts[1:-1]:
                    child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = child
                current = os.stat(root.name, dir_fd=fd, follow_symlinks=False)
                revision = hashlib.sha256(
                    encoded(
                        [project, str(root), current.st_dev, current.st_ino, current.st_ctime_ns]
                    ).encode()
                ).hexdigest()
                if revision != preview["revision"]:
                    raise APIError("project_folder_changed", 409)
                shutil.rmtree(root.name, dir_fd=fd)
            finally:
                os.close(fd)

        self.deleting_project_folders.update(preview["project_ids"])
        try:
            if not preview["missing"]:
                await asyncio.to_thread(remove)
            with self.db:
                self.project_repository.mark_folders_deleted(preview["project_ids"])
            self.deleted_project_folders.update(preview["project_ids"])
        except OSError:
            raise APIError("project_folder_delete_failed", 409)
        finally:
            self.deleting_project_folders.difference_update(preview["project_ids"])
        return {"deleted_paths": preview["paths"], "deleted_projects": preview["project_ids"]}
