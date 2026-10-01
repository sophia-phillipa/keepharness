"""Admin panel HTTP layer: the local-only guard and the ``/api`` dispatch tables."""

import asyncio
import json
import os
import secrets
import shutil
import socket
import sys
import uuid
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from adapters.claude.auth import cli_login_environment
from adapters.deepseek import account as deepseek
from tail_ui import asset_response, static_response

from agent_service.errors import APIError

from . import env
from .product import PRODUCT
from .dashboard import execution as dashboard_execution
from .integration_catalog import catalog as integration_catalog
from .integrations import inventory
from .local_models import (
    launch_command,
    load_profile,
    load_profiles,
    runtime_details,
    save_profile,
    validate_profile,
)
from .manager import PERMISSIONS
from .operations import operation
from .vault_admin import change_vault, read_vault

ADMIN_BODY_LIMIT = 64000
ADMIN_BODY_TIMEOUT = 10
ADMIN_OPERATION_LIMIT = 4
PANEL_DIR = env.REPOSITORY_ROOT / "control"
# POST paths that start a background operation and count against ADMIN_OPERATION_LIMIT.
LIMITED_OPERATIONS = frozenset(
    {"/api/provider-login", "/api/integration", "/api/model-install", "/api/local-start"}
)


def admin_guard(request, manager, port):
    """Answer requests that must not reach the API, or return ``None`` to continue.

    Order: host/client/Tailscale identity, then origin and fetch metadata, then the
    static panel files (which set the admin cookie), then the admin cookie itself.
    """
    host = request.headers.get("host", "")
    allowed = (f"127.0.0.1:{port}", f"localhost:{port}")
    if (
        host not in allowed
        or (request.client is None or request.client.host not in ("127.0.0.1", "::1", "testclient"))
        or request.headers.get("tailscale-user-login")
    ):
        return JSONResponse({"error": "Management is only available on this machine."}, 403)
    origin = request.headers.get("origin")
    path = request.url.path
    # A clicked harness link may cross sites; only allow the initial document.
    navigation = (
        request.method == "GET"
        and path == "/"
        and request.headers.get("sec-fetch-mode") == "navigate"
        and request.headers.get("sec-fetch-dest") == "document"
        and request.headers.get("sec-fetch-user") == "?1"
    )
    if (origin and origin not in ["http://" + h for h in allowed]) or (
        request.headers.get("sec-fetch-site") == "cross-site" and not navigation
    ):
        return JSONResponse({"error": "Unauthorized origin."}, 403)
    if path == "/":
        r = FileResponse(PANEL_DIR / "index.html")
        r.set_cookie("admin", manager.cookie, httponly=True, samesite="strict")
        return r
    if path.startswith("/assets/"):
        return asset_response(path, request.headers)
    if path in ("/admin.js", "/admin.css"):
        return static_response(PANEL_DIR / path[1:], request.headers)
    if not secrets.compare_digest(
        request.cookies.get("admin", "").encode("utf-8"), manager.cookie.encode("utf-8")
    ):
        return JSONResponse({"error": "Open the management panel on this machine first."}, 401)
    return None


async def list_folders(request, manager):
    def folders():
        folder = Path(request.query_params.get("path") or Path.home()).expanduser()
        if not folder.is_absolute():
            raise ValueError("Provide a folder with an absolute path on this server.")
        try:
            folder = folder.resolve(strict=True)
            directories = []
            with os.scandir(folder) as entries:
                for entry in entries:
                    try:
                        is_directory = entry.is_dir()
                    except OSError:
                        continue
                    if is_directory:
                        directories.append({"name": entry.name, "path": str(folder / entry.name)})
                        if len(directories) > 200:
                            break
        except PermissionError:
            raise ValueError("No permission to open this folder. Choose another folder.") from None
        except (FileNotFoundError, NotADirectoryError):
            raise ValueError(
                "The folder was not found on this server. Choose another folder."
            ) from None
        return {
            "path": str(folder),
            "parent": str(folder.parent) if folder.parent != folder else None,
            "directories": sorted(directories[:200], key=lambda d: d["name"].casefold()),
            "truncated": len(directories) > 200,
        }

    return await asyncio.to_thread(folders)


async def read_dashboard(request, manager):
    job = request.query_params.get("job")
    return (
        await asyncio.to_thread(dashboard_execution, manager.state, job)
        if job
        else await manager.dashboard.read()
    )


async def read_state(request, manager):
    return {
        "settings": manager.settings,
        "inventory": manager.inventory,
        "status": manager.status(),
        "authentication": manager.auth,
        "models": manager.provider_models,
        "integrations": manager.integrations(),
        "operations": list(manager.operations.jobs.values()),
        "local_profile": load_profile(manager.state),
        "local_profiles": load_profiles(manager.state),
        "credentials": {"deepseek": deepseek.key_file(manager.state).exists()},
    }


async def create_folder(request, manager, data):
    name = data.get("name", "")
    parent = data.get("parent", "")
    if (
        not isinstance(name, str)
        or not name.strip()
        or name != name.strip()
        or name in (".", "..")
        or any(c in name for c in ("/", "\\", "\x00"))
        or len(name) > 120
    ):
        raise ValueError("Use a simple folder name, with no slashes, up to 120 characters.")
    if not isinstance(parent, str) or not Path(parent).is_absolute():
        raise ValueError("Select the folder to create the project in.")
    folder = Path(parent).resolve(strict=True) / name
    try:
        await asyncio.to_thread(folder.mkdir)
    except FileExistsError:
        raise ValueError("An item with that name already exists. Choose another name.") from None
    except PermissionError:
        raise ValueError("No permission to create a folder in this location.") from None
    result = {"path": str(folder), "created": True}
    return result


async def scan_inventory(request, manager, data):
    result = await manager.refresh()
    return result


async def check_provider(request, manager, data):
    result = await manager.check(data.get("provider"))
    return result


async def save_provider_token(request, manager, data):
    if data.get("provider") != "deepseek":
        raise ValueError("Unknown API provider.")
    deepseek.store_key(manager.state, data.get("token"))
    manager.provider_revisions["deepseek"] = str(uuid.uuid4())
    manager.auth["deepseek"] = False
    manager.provider_models.pop("deepseek", None)
    if manager.running():
        await manager.apply_settings(manager.settings)
    manager.audit("deepseek_key_saved")
    result = {"saved": True}
    return result


async def delete_provider(request, manager, data):
    provider = data.get("provider")
    if provider not in manager.settings["services"]:
        raise ValueError("Unknown provider.")
    import copy

    draft = copy.deepcopy(manager.settings)
    draft["services"][provider].update(
        added=False,
        enabled=False,
        models=[],
        integrations=[],
        projects=["sem-projeto"],
        permissions={k: False for k in PERMISSIONS},
    )
    if draft.get("mcp_defaults", {}).get("backend") == provider:
        draft["mcp_defaults"] = {}
    await manager.apply_settings(draft)
    if provider == "deepseek":
        deepseek.key_file(manager.state).unlink(missing_ok=True)
    manager.audit("provider_removed:" + provider)
    result = {"removed": True}
    return result


async def export_settings(request, manager, data):
    result = {
        "format": PRODUCT.slug + "-settings",
        "version": 1,
        "settings": manager.settings,
        "local_profile": load_profile(manager.state),
        "local_profiles": load_profiles(manager.state),
    }
    return result


async def import_settings(request, manager, data):
    bundle = data.get("bundle", {})
    if (
        not isinstance(bundle, dict)
        or bundle.get("format") != PRODUCT.slug + "-settings"
        or bundle.get("version") != 1
    ):
        raise ValueError("Incompatible configuration format.")
    imported = manager.validate(bundle.get("settings"))
    profile = validate_profile(bundle.get("local_profile", {}), state_dir=manager.state)
    profiles = bundle.get("local_profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Invalid local profile catalog.")
    validated = {}
    for key, value in profiles.items():
        item = validate_profile(value, state_dir=manager.state)
        if not item or str(Path(key).expanduser().resolve()) != item["model_file"]:
            raise ValueError("The profile does not match the given weights file.")
        validated[item["model_file"]] = item
    if profile:
        validated.setdefault(profile["model_file"], profile)
    if data.get("apply") is True:
        with manager.configuration_change():
            for item in validated.values():
                save_profile(manager.state, item)
            if profile:
                save_profile(manager.state, validated[profile["model_file"]])
            await manager.apply_settings(imported)
        manager.audit("settings_imported")
    result = {
        "valid": True,
        "applied": data.get("apply") is True,
        "services": [p for p, s in imported["services"].items() if s["enabled"]],
        "projects": len(imported["projects"]),
        "local_profile": bool(validated),
        "local_profiles": len(validated),
    }
    return result


async def save_settings(request, manager, data):
    await manager.apply_settings(data)
    result = {"saved": True}
    return result


async def start_harness(request, manager, data):
    await manager.start()
    result = manager.status()
    return result


async def stop_harness(request, manager, data):
    await manager.stop()
    result = manager.status()
    return result


async def cancel_operation(request, manager, data):
    manager.operations.cancel(data.get("id"))
    result = {"cancelled": True}
    return result


async def login_provider(request, manager, data):
    provider = data.get("provider")
    binary = (
        manager.inventory["binaries"].get(provider)
        if provider in ("codex", "claude", "gemini")
        else None
    )
    if not binary:
        raise ValueError("CLI not found.")
    command = (
        [sys.executable, "-m", "adapters.gemini.account", "--binary", binary]
        if provider == "gemini"
        else (
            [binary, "login", "--device-auth"] if provider == "codex" else [binary, "auth", "login"]
        )
    )
    existing = next(
        (
            j
            for j in manager.operations.jobs.values()
            if j.get("provider") == provider
            and j.get("kind") == "provider-login"
            and j["state"] == "running"
        ),
        None,
    )
    if existing:
        result = existing
    else:
        options = (
            {
                "env": cli_login_environment(),
                "on_success": manager.claude_login_completed,
            }
            if provider == "claude"
            else {}
        )
        result = manager.operations.launch(command, **options)
        result.update(provider=provider, kind="provider-login")
    return result


async def read_integration_catalog(request, manager, data):
    provider = data.get("provider")
    if provider not in ("codex", "claude"):
        raise ValueError("Invalid provider.")
    binary = manager.inventory["binaries"].get(provider)
    if not binary:
        raise ValueError("CLI not installed.")
    result = await integration_catalog(provider, binary, inventory())
    return result


async def change_integration(request, manager, data):
    provider = data.get("provider")
    if provider not in ("codex", "claude"):
        raise ValueError("Invalid provider.")
    binary = manager.inventory["binaries"][provider]
    if not binary:
        raise ValueError("CLI not installed.")
    result = manager.operations.launch(operation(binary, provider, data))
    manager.audit("integration:" + data.get("action", ""))
    return result


async def install_model(request, manager, data):
    catalog = {
        "gemma4": "hf.co/unsloth/gemma-4-E4B-it-GGUF:Q4_K_M",
        "qwen36": "hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q3_K_M",
    }
    model = catalog.get(data.get("model"))
    binary = manager.inventory["binaries"].get("ollama")
    if not model:
        raise ValueError("Unknown model.")
    if not data.get("accepted"):
        raise ValueError("Confirm the license and download size.")
    required = {"gemma4": 6 * 1024**3, "qwen36": 18 * 1024**3}[data["model"]]
    env.LOCAL_AI.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(env.LOCAL_AI).free < required:
        raise ValueError("Not enough free space for the model.")
    args = (
        [binary, "pull", model]
        if data.get("runtime") == "ollama" and binary
        else [
            sys.executable,
            "-m",
            "control.download_model",
            data["model"],
            str(env.LOCAL_AI / "models"),
        ]
    )
    result = manager.operations.launch(args, timeout=7200)
    manager.audit("model_install:" + data["model"])
    return result


async def import_local_profile(request, manager, data):
    from .local_models import processes

    active = processes()
    if data.get("file"):
        selected = str(Path(data["file"]).expanduser().resolve())
        active = [
            server
            for server in active
            if server.get("model_file")
            and str(Path(server["model_file"]).expanduser().resolve()) == selected
        ]
    if len(active) != 1:
        raise ValueError("A single active local server is required to import the profile.")
    selected = {
        k: active[0][k]
        for k in ("binary", "model_file", "mmproj_file", "flags", "performance")
        if k in active[0] and active[0][k]
    }
    with manager.configuration_change():
        result = save_profile(manager.state, validate_profile(selected, state_dir=manager.state))
        if manager.running():
            await manager.apply_settings(manager.settings)
    manager.audit("local_profile_imported")
    return result


async def save_local_profile(request, manager, data):
    profile = validate_profile(data, state_dir=manager.state)
    if not profile:
        raise ValueError("Provide the model and executable for this profile.")
    with manager.configuration_change():
        result = save_profile(manager.state, profile)
        if manager.running():
            await manager.apply_settings(manager.settings)
    manager.audit("local_profile_saved")
    return result


async def read_local_devices(request, manager, data):
    result = await runtime_details(data.get("binary", ""))
    return result


async def list_local_files(request, manager, data):
    from .local_models import processes

    roots = {Path(m["model_file"]).parent for m in processes() if m.get("model_file")}
    roots.add(env.LOCAL_AI / "models")
    if data.get("folder"):
        root = Path(data["folder"]).expanduser().resolve()
        if not root.is_dir() or root == Path("/"):
            raise ValueError("Choose an existing models folder.")
        roots.add(root)
    files = []
    for root in roots:
        for file in list(root.glob("*.gguf"))[:200]:
            if file.is_file() and not file.is_symlink():
                files.append(
                    {
                        "path": str(file.resolve()),
                        "name": file.name,
                        "bytes": file.stat().st_size,
                    }
                )
    result = {"files": files, "servers": processes()}
    return result


async def start_local_model(request, manager, data):
    from .local_models import processes

    active = processes()
    model = Path(data.get("file", "")).expanduser().resolve()
    profile = load_profile(manager.state, model) if data.get("use_profile") else {}
    if data.get("use_profile") and not profile:
        raise ValueError(
            "This model does not have a saved profile yet. Set one up before starting."
        )
    if profile:
        profile = validate_profile(profile, state_dir=manager.state)
    cpu_only = bool(profile) and profile.get("performance", {}).get("n-gpu-layers") == "0"
    if active and not cpu_only:
        raise ValueError(
            "Another local server is active. To preserve it, another model can only be started with an explicit CPU profile (0 GPU layers)."
        )
    binary = (
        data.get("binary")
        or profile.get("binary")
        or (
            str(env.LOCAL_AI / "runtime/llama-b11003/llama-server")
            if (env.LOCAL_AI / "runtime/llama-b11003/llama-server").is_file()
            else shutil.which("llama-server")
        )
    )
    if not binary or not Path(binary).is_file() or Path(binary).name != "llama-server":
        raise ValueError("Provide the installed llama-server executable.")
    if not model.is_file() or model.suffix != ".gguf":
        raise ValueError("Select an existing GGUF file.")
    multigpu = set(profile.get("performance", {})) & {
        "main-gpu",
        "split-mode",
        "tensor-split",
    }
    if multigpu:
        details = await runtime_details(binary)
        unsupported = multigpu - set(details["supported_flags"])
        if unsupported:
            raise ValueError(
                "This runtime did not confirm support for: " + ", ".join(sorted(unsupported))
            )
    layers = int(data.get("gpu_layers", 0))
    if not 0 <= layers <= 999:
        raise ValueError("Invalid GPU layers.")
    from .start_local import ensure_key

    key = ensure_key(env.LOCAL_AI / "config/api-key")
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", 8096))
        except OSError:
            raise ValueError("Port 8096 is busy; the existing process was preserved.")
    launch_profile = (
        {**profile, "binary": str(binary)}
        if profile
        else {
            "binary": str(binary),
            "model_file": str(model),
            "performance": {
                "ctx-size": "65536",
                "parallel": "1",
                "n-gpu-layers": str(layers),
            },
        }
    )
    result = manager.operations.launch(launch_command(launch_profile, key_file=key), timeout=None)
    manager.audit("local_model_started")
    return result


async def reveal_vpn_key(request, manager, data):
    key = manager.state / "vpn.key"
    if not key.exists():
        raise ValueError("Start the harness first to generate the key.")
    result = {"token": key.read_text()}
    manager.audit("vpn_key_revealed")
    return result


async def set_tailnet(request, manager, data):
    await manager.tailnet(data.get("enabled") is True)
    result = manager.status()
    return result


GET_ROUTES = {
    "/api/vault": read_vault,
    "/api/folders": list_folders,
    "/api/dashboard": read_dashboard,
    "/api/state": read_state,
}
POST_ROUTES = {
    "/api/vault": change_vault,
    "/api/folders/create": create_folder,
    "/api/scan": scan_inventory,
    "/api/check": check_provider,
    "/api/provider-token": save_provider_token,
    "/api/provider-delete": delete_provider,
    "/api/settings-export": export_settings,
    "/api/settings-import": import_settings,
    "/api/settings": save_settings,
    "/api/start": start_harness,
    "/api/stop": stop_harness,
    "/api/cancel-operation": cancel_operation,
    "/api/provider-login": login_provider,
    "/api/integration-catalog": read_integration_catalog,
    "/api/integration": change_integration,
    "/api/model-install": install_model,
    "/api/local-import": import_local_profile,
    "/api/local-profile": save_local_profile,
    "/api/local-devices": read_local_devices,
    "/api/local-files": list_local_files,
    "/api/local-start": start_local_model,
    "/api/vpn-key": reveal_vpn_key,
    "/api/tailnet": set_tailnet,
}


async def endpoint(request: Request):
    manager = request.app.state.manager
    denied = admin_guard(request, manager, request.app.state.admin_port)
    if denied is not None:
        return denied
    path = request.url.path
    try:
        if request.method == "GET":
            handler = GET_ROUTES.get(path)
            if handler is None:
                return JSONResponse({"error": "Not found"}, 404)
            return JSONResponse(await handler(request, manager))
        if request.headers.get("x-harness-admin") != "1":
            raise ValueError("Administrative header required.")
        if manager.lock.locked():
            return JSONResponse(
                {"error": "Another administrative operation is in progress. Wait and try again."},
                429,
                headers={"Retry-After": "1"},
            )
        async with manager.lock:
            length = request.headers.get("content-length")
            if length is not None:
                if not length.isdecimal():
                    raise ValueError("Invalid request size.")
                if int(length) > ADMIN_BODY_LIMIT:
                    return JSONResponse({"error": "Request too large."}, 413)
            raw = bytearray()
            async with asyncio.timeout(ADMIN_BODY_TIMEOUT):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > ADMIN_BODY_LIMIT:
                        return JSONResponse({"error": "Request too large."}, 413)
                    raw.extend(chunk)
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                raise ValueError("The request must be a JSON object.")
            if (
                path in LIMITED_OPERATIONS
                and sum(j.get("state") == "running" for j in manager.operations.jobs.values())
                >= ADMIN_OPERATION_LIMIT
            ):
                return JSONResponse(
                    {
                        "error": "There are operations in progress. Wait for one to finish before starting another."
                    },
                    429,
                    headers={"Retry-After": "5"},
                )
            handler = POST_ROUTES.get(path)
            if handler is None:
                return JSONResponse({"error": "Not found"}, 404)
            result = await handler(request, manager, data)
        return JSONResponse(result)
    except APIError as exc:
        return JSONResponse({"error": exc.code}, exc.status)
    except TimeoutError:
        return JSONResponse({"error": "Sending the request took too long. Try again."}, 408)
    except (TypeError, AttributeError, RecursionError):
        return JSONResponse(
            {"error": "Invalid request structure. Check the submitted fields."}, 400
        )
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, 400)


ROUTES = [
    Route("/", endpoint),
    Route("/admin.js", endpoint),
    Route("/admin.css", endpoint),
    Route("/assets/{path:path}", endpoint),
    Route("/api/{path:path}", endpoint, methods=["GET", "POST"]),
]
