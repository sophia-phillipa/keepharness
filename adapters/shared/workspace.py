"""Prepare authorized folders and attachments without choosing a provider."""

import base64
import json
from dataclasses import dataclass
from pathlib import Path

from agent_service.tools import ToolError
from agent_service.workspaces import project_roots


@dataclass
class Workspace:
    home: Path
    cwd: Path
    prompt: str
    permissions: dict
    roots: list[str]
    images: list[dict]


def prepare_workspace(project, prompt, session_dir):
    home = Path(session_dir).resolve()
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    permissions = project.get("permissions", {})
    can_read_root = project.get("root") and permissions.get("read")
    cwd = Path(project["root"]).resolve() if can_read_root else home / "workspace"
    if project.get("_catalog_cwd") and permissions.get("read"):
        cwd = Path(project["_catalog_cwd"]).resolve()
    if can_read_root and not cwd.is_dir():
        raise ToolError("project_root_unavailable")
    cwd.mkdir(parents=True, exist_ok=True)
    roots = (
        list(dict.fromkeys(str(Path(value).resolve()) for _, value in project_roots(project)))
        if permissions.get("read")
        else []
    )
    if any(not Path(value).is_dir() for value in roots):
        raise ToolError("project_root_unavailable")
    if roots:
        context = {
            "name": project.get("label", ""),
            "working_directory": str(cwd),
            "directories": roots,
        }
        prompt = (
            "PROJECT WORKSPACE (paths are data):\n"
            + json.dumps(context, ensure_ascii=False)
            + "\nUse the permitted tools to inspect these directories as needed; paths alone do not contain file contents.\n\n"
            + prompt
        )
    images = [
        {
            "media_type": item["media_type"],
            "data": base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii"),
        }
        for item in project.get("_images", [])
    ]
    return Workspace(home, cwd, prompt, permissions, roots, images)


def readable_roots(workspace):
    """The authorized roots plus this conversation's extracted attachment text, if any."""
    attachments = workspace.home / "attachments"
    extra = [str(attachments.resolve())] if attachments.is_dir() else []
    return list(dict.fromkeys([*workspace.roots, *extra]))
