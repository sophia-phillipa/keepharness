"""Uploaded files, uploaded workspaces and project file browsing."""

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from starlette.responses import FileResponse, JSONResponse

from .. import maestro, tools, workspaces
from ..errors import APIError
from ..persistence.db import encoded
from ..services.conversation_service import preview_metadata
from . import api_route, body

# Bidirectional embedding, override and isolate controls: they can reorder how a name
# is displayed and hide its real extension ("invoice\u202etxt.exe").
BIDI_CONTROLS = frozenset("\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def require_current_read(request, service, project):
    identity = service.identity(request, revalidate=True)
    service.project(identity, project)
    if not service.can_read_project(project):
        raise APIError("read_denied", 403)
    return identity


def require_current_upload(request, service, project, *, selected_media=False):
    identity = service.identity(request, revalidate=True)
    service.project(identity, project)
    if not service.uploads_enabled(project) or not maestro.candidates(
        service.config, project, uploads=True
    ):
        raise APIError("uploads_denied", 403)
    if selected_media and not any(
        candidate["backend"] == request.query_params.get("backend")
        and candidate["model"] == request.query_params.get("model")
        for candidate in maestro.candidates(service.config, project, uploads=True)
    ):
        raise APIError("uploads_denied", 403)
    return identity


async def workspaces_collection(request, service, identity):
    if request.method == "POST":
        return await upload_workspace(request, service, identity)
    return await list_workspaces(request, service, identity)


async def list_workspaces(request, service, identity):
    rows = service.project_repository.workspaces(identity[0])
    return JSONResponse(
        {"workspaces": [dict(r) for r in rows if r["project"] in identity[1]["projects"]]}
    )


async def upload_workspace(request, service, identity):
    project = request.query_params.get("project_id")
    identity = require_current_upload(request, service, project)
    from urllib.parse import unquote

    name = unquote(request.headers.get("x-filename", "project.zip"))
    if not re.fullmatch(r"[\w .-]{1,160}", name):
        raise APIError("invalid_filename")
    async with service.upload_lock:
        identity = require_current_upload(request, service, project)
        base = service.root / "workspaces"
        base.mkdir(exist_ok=True, mode=0o700)
        used = sum(p.stat().st_size for p in base.rglob("*") if p.is_file() and not p.is_symlink())
        if used > 2 * 1024**3 - workspaces.MAX_BYTES * 2:
            raise APIError("workspace_storage_limit", 413)
        wid = uuid.uuid4().hex
        folder = base / wid
        folder.mkdir(mode=0o700)
        archive = folder / "original.zip"
        size = 0
        try:
            with archive.open("xb") as output:
                async with asyncio.timeout(120):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > workspaces.MAX_BYTES:
                            raise APIError("workspace_size_limit", 413)
                        output.write(chunk)
            identity = require_current_upload(request, service, project)
            manifest = await asyncio.to_thread(workspaces.unpack, archive, folder / "work")
            if not manifest:
                raise APIError("empty_workspace")
            warnings = await workspaces.prepare_documents(folder / "work", manifest)
            identity = require_current_upload(request, service, project)
            with service.db:
                service.project_repository.add_workspace(
                    wid, project, identity[0], name, time.time(), encoded(manifest)
                )
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    return JSONResponse(
        {
            "workspace_id": wid,
            "project_id": project,
            "files": len(manifest),
            "bytes": sum(f["bytes"] for f in manifest),
            "source_preserved": True,
            "extraction_warnings": warnings,
        },
        status_code=201,
    )


async def workspace(request, service, identity):
    path = request.url.path
    wid = request.path_params["workspace"]
    record = service.workspace(identity, wid)
    if not service.can_read_project(record["project"]):
        raise APIError("read_denied", 403)
    root = service.workspace_root(wid)
    if path.endswith("/download"):
        import tempfile

        from starlette.background import BackgroundTask

        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            output = Path(tmp.name)
        try:
            await asyncio.to_thread(workspaces.pack, root, output)
            require_current_read(request, service, record["project"])
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return FileResponse(
            output,
            filename="harness-" + wid + ".zip",
            background=BackgroundTask(output.unlink),
        )
    params = request.query_params
    try:
        start = int(params.get("start", 1))
        limit = int(params.get("limit", 100))
    except ValueError:
        raise APIError("invalid_range")
    result = await asyncio.to_thread(
        workspaces.inspect,
        root,
        params.get("query", ""),
        params.get("path", ""),
        start,
        limit,
    )
    require_current_read(request, service, record["project"])
    return JSONResponse({"workspace_id": wid, **result})


def project_file_status(root, entries):
    """Read Git state for this page only; do not refresh the index or run fsmonitor."""
    paths = [entry["path"] for entry in entries if entry["type"] == "file"]
    if not paths:
        return
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "--literal-pathspecs",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "-z",
                "--untracked-files=normal",
                "--",
                *paths,
            ],
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if result.returncode:
        return
    states = {}
    records = iter(result.stdout.decode("utf-8", errors="replace").split("\0"))
    for record in records:
        if len(record) < 4:
            continue
        code, path = record[:2], record[3:]
        states[path] = next((value for value in "UADRM?" if value in code), None)
        if "R" in code or "C" in code:
            next(records, None)
    for entry in entries:
        if states.get(entry["path"]):
            entry["status"] = states[entry["path"]]


async def authorized_project_files(request, service, identity):
    params = request.query_params
    project = params.get("project_id")
    spec = service.project(identity, project)
    roots = [
        {"id": key, "path": path, "label": path} for key, path in workspaces.project_roots(spec)
    ]
    result = {
        "roots": roots,
        "entries": [],
        "path": "",
        "limited": False,
        "can_authorize": bool(service.config.get("shared_projects")) and project != "sem-projeto",
    }
    if not roots:
        return JSONResponse(result)
    if not service.can_read_project(project):
        raise APIError("read_denied", 403)
    root_id = params.get("root_id", "root")
    root = workspaces.project_root(spec, root_id)
    try:
        start, limit = int(params.get("start", 1)), int(params.get("limit", 100))
    except ValueError:
        raise APIError("invalid_range")
    listing = await asyncio.to_thread(
        workspaces.browse_project, root, params.get("path", ""), start, limit
    )
    await asyncio.to_thread(project_file_status, root, listing["entries"])
    require_current_read(request, service, project)
    return JSONResponse(
        {**result, **listing, "root_id": root_id}, headers={"Cache-Control": "no-store"}
    )


async def project_files(request, service, identity):
    if request.query_params.get("view") == "authorized":
        return await authorized_project_files(request, service, identity)
    if request.query_params.get("view") == "tree":
        roots = workspaces.visible_system_roots()
        root_id = request.query_params.get("root_id", "home")
        try:
            start = int(request.query_params.get("start", 1))
            limit = int(request.query_params.get("limit", 100))
        except ValueError:
            raise APIError("invalid_range")
        folder = request.query_params.get("path", "")
        if request.query_params.get("navigate_project") == "1":
            spec = service.project(identity, request.query_params.get("project_id"))
            if not spec.get("root"):
                raise APIError("project_has_no_directory")
            target = Path(spec["root"]).resolve()
            root_id, root = next(
                ((rid, base) for rid, base in roots if target.is_relative_to(base.resolve())),
                ("system", Path("/")),
            )
            folder = target.relative_to(root.resolve()).as_posix()
        root = workspaces.system_root(root_id)
        result = await asyncio.to_thread(workspaces.browse_system, root, folder, start, limit)
        current = service.identity(request, revalidate=True)
        if current[0] != identity[0]:
            raise APIError("project_denied", 403)
        if request.query_params.get("navigate_project") == "1":
            service.project(current, request.query_params.get("project_id"))
        return JSONResponse(
            {
                "state": "ready",
                "roots": [
                    {
                        "id": rid,
                        "label": {
                            "home": "Personal folder",
                            "media-user": "External media",
                        }[rid],
                    }
                    for rid, _ in roots
                ],
                "root_id": root_id,
                **result,
            }
        )
    project = request.query_params.get("project_id")
    spec = service.project(identity, project)
    if not service.can_read_project(project):
        raise APIError("read_denied", 403)
    if not spec.get("root"):
        raise APIError("project_has_no_directory")
    params = request.query_params
    try:
        start = int(params.get("start", 1))
        limit = int(params.get("limit", 100))
    except ValueError:
        raise APIError("invalid_range")
    result = await asyncio.to_thread(
        workspaces.inspect,
        spec["root"],
        params.get("query", ""),
        params.get("path", ""),
        start,
        limit,
    )
    require_current_read(request, service, project)
    return JSONResponse(result)


async def attach_project_files(request, service, identity):
    config = service.config
    project = request.query_params.get("project_id")
    service.project(identity, project)
    if not service.can_read_project(project):
        raise APIError("read_denied", 403)
    if not service.uploads_enabled(project) or not maestro.candidates(
        config, project, uploads=True
    ):
        raise APIError("uploads_denied", 403)
    data = await body(request)
    identity = require_current_read(request, service, project)
    try:
        maximum = int(request.query_params.get("max_files", workspaces.MAX_ATTACHMENTS))
    except ValueError:
        raise APIError("invalid_selection")
    if data.get("project_root_id") is not None:
        root = workspaces.project_root(service.project(identity, project), data["project_root_id"])
        selected, skipped = await asyncio.to_thread(
            workspaces.selected_project_files, root, data.get("paths"), maximum
        )
    else:
        root = workspaces.system_root(data.get("root_id", "system"))
        selected, skipped = await asyncio.to_thread(
            workspaces.selected_system_files, root, data.get("paths"), maximum
        )
    backend = request.query_params.get("backend", data.get("backend"))
    identity = require_current_read(request, service, project)
    model = request.query_params.get("model", data.get("model"))
    execution_mode = data.get("execution_mode") or request.query_params.get("execution_mode")
    if backend:
        execution_mode = execution_mode or service.default_execution_mode(backend)
        service.validate_execution_mode(backend, execution_mode)
    return JSONResponse(
        await service.attach_project_files(
            identity,
            project,
            selected,
            skipped,
            backend,
            model,
            execution_mode,
            revalidate=lambda: require_current_read(request, service, project),
        )
    )


async def upload_file(request, service, identity):
    config = service.config
    # Raw streaming upload avoids multipart temporary-file allocation before checking quota.
    project = request.query_params.get("project_id")
    identity = require_current_upload(request, service, project)
    from urllib.parse import unquote

    filename = unquote(request.headers.get("x-filename", ""))
    if (
        not 1 <= len(filename) <= 160
        or not filename.strip()
        or filename in (".", "..")
        or any(
            char in "/\\" or ord(char) < 32 or 127 <= ord(char) < 160 or char in BIDI_CONTROLS
            for char in filename
        )
    ):
        raise APIError("invalid_filename")
    async with service.upload_lock:
        identity = require_current_upload(request, service, project)
        used = service.message_repository.project_bytes(project)
        fid = uuid.uuid4().hex
        folder = service.root / "files" / project / fid
        folder.mkdir(parents=True, mode=0o700)
        dest = folder / "source"
        size = 0
        digest = hashlib.sha256()
        file_limit = tools.MAX_ATTACHMENT_BYTES
        try:
            with dest.open("xb") as out:
                async with asyncio.timeout(600):
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > file_limit or used + size > 2 * 1024**3:
                            raise APIError("upload_limit", 413)
                        out.write(chunk)
                        digest.update(chunk)
            identity = require_current_upload(request, service, project)
            if Path(filename).suffix.lower() == ".mp4":
                backend = request.query_params.get("backend")
                model = request.query_params.get("model")
                choices = maestro.candidates(config, project, uploads=True)
                if not any(c["backend"] == backend and c["model"] == model for c in choices):
                    raise APIError("model_video_unavailable")
                execution_mode = request.query_params.get(
                    "execution_mode"
                ) or service.default_execution_mode(backend)
                service.validate_execution_mode(backend, execution_mode)
                await service.validate_video(backend, model, execution_mode)
            require_current_upload(
                request, service, project, selected_media=Path(filename).suffix.lower() == ".mp4"
            )
            pages = await tools.extract(dest, filename)
            if any(page.get("media_type") for page in pages):
                backend = request.query_params.get("backend")
                model = request.query_params.get("model")
                choices = maestro.candidates(config, project, uploads=True)
                if not any(c["backend"] == backend and c["model"] == model for c in choices):
                    raise APIError("select_model_for_image")
                execution_mode = request.query_params.get(
                    "execution_mode"
                ) or service.default_execution_mode(backend)
                service.validate_execution_mode(backend, execution_mode)
                await service.validate_images(backend, model, execution_mode)
            identity = require_current_upload(
                request,
                service,
                project,
                selected_media=Path(filename).suffix.lower() == ".mp4"
                or any(page.get("media_type") for page in pages),
            )
            with service.db:
                service.message_repository.add_file(
                    fid,
                    project,
                    filename,
                    size,
                    digest.hexdigest(),
                    encoded(pages),
                    identity[0],
                )
        except BaseException:
            shutil.rmtree(folder)
            raise
    return JSONResponse(
        {
            "file_id": fid,
            "sha256": digest.hexdigest(),
            "bytes": size,
            "pages": len(pages),
            **preview_metadata(fid, pages),
        },
        status_code=201,
    )


async def file_preview(request, service, identity):
    file_id = request.path_params["file"]
    row = service.message_repository.owned_file(file_id, identity[0])
    if not row:
        raise APIError("file_not_found", 404)
    record = dict(row)
    service.project(identity, record["project"])
    pages = json.loads(record["pages"])
    metadata = preview_metadata(file_id, pages)
    source = service.root / "files" / record["project"] / file_id / "source"
    if not metadata or source.is_symlink() or not source.is_file():
        raise APIError("preview_not_available", 404)
    return FileResponse(
        source,
        media_type=metadata["media_type"],
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


ROUTES = [
    api_route("/v1/workspaces", workspaces_collection, methods=["GET", "POST"]),
    api_route("/v1/workspaces/{workspace}", workspace),
    api_route("/v1/workspaces/{workspace}/download", workspace),
    api_route("/v1/project-files", project_files),
    api_route("/v1/project-files/attach", attach_project_files, methods=["POST"]),
    api_route("/v1/files", upload_file, methods=["POST"]),
    api_route("/v1/files/{file}/preview", file_preview, methods=["GET"]),
]
