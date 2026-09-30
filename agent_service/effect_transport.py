"""Short-lived prepare-only capability, owned by one harness execution."""

import asyncio
import hmac
import json
import os
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from .errors import APIError

MAX_REQUEST_BYTES = 256 * 1024


def transport_support(backend, mode):
    supported = backend in ("codex", "claude") and mode in ("native", "scoped")
    return {
        "supported": supported,
        "enforcement": "unenforced",
        "reason": "worker_isolation_required" if supported else "effect_transport_unsupported",
    }


@asynccontextmanager
async def effect_transport(service, job_id, backend, mode, *, execution_id=None):
    if (
        not service.config.get("effect_integrations")
        or not transport_support(backend, mode)["supported"]
    ):
        yield None
        return
    token = secrets.token_urlsafe(32)
    clients = set()
    # Native processes share the owner's filesystem. The scoped adapter may upgrade
    # this only after checking every bind against the harness's private stores.
    capability = {
        "token": token,
        "enforcement": "unenforced",
        "server_name": "harness_effects_" + secrets.token_hex(16),
    }

    async def handle(reader, writer):
        task = asyncio.current_task()
        clients.add(task)
        try:
            async with asyncio.timeout(10):
                message = json.loads(await reader.readline())
                if (
                    not isinstance(message, dict)
                    or not isinstance(message.get("token"), str)
                    or not hmac.compare_digest(message["token"], token)
                ):
                    result = {"error": "effect_capability_denied"}
                elif message.get("method") != "prepare":
                    result = {"error": "effect_method_denied"}
                elif not isinstance(message.get("request"), dict):
                    result = {"error": "effect_request_invalid"}
                else:
                    result = await service.effects.prepare(
                        job_id,
                        message["request"],
                        execution_id=execution_id,
                        enforcement=capability["enforcement"],
                    )
        except asyncio.CancelledError:
            writer.close()
            await writer.wait_closed()
            clients.discard(task)
            raise
        except APIError as exc:
            result = {"error": exc.code}
        except (ValueError, TimeoutError, OSError):
            result = {"error": "effect_request_invalid"}
        except Exception:
            # Never forward provider/driver exceptions or request bodies to stderr.
            result = {"error": "effect_prepare_failed"}
        try:
            writer.write(json.dumps(result).encode() + b"\n")
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            finally:
                clients.discard(task)

    with tempfile.TemporaryDirectory(prefix="harness-effect-") as directory:
        socket = str(Path(directory) / "prepare.sock")
        server = await asyncio.start_unix_server(handle, socket, limit=MAX_REQUEST_BYTES)
        os.chmod(socket, 0o600)
        capability["socket"] = socket
        try:
            yield capability
        finally:
            server.close()
            for task in list(clients):
                task.cancel()
            await asyncio.gather(*clients, return_exceptions=True)
            await server.wait_closed()


def server_spec(capability, *, scoped=False):
    """Only the short-lived prepare capability enters the provider configuration."""
    import sys

    public = {
        "socket": "/bridge/effect.sock" if scoped else capability["socket"],
        "token": capability["token"],
    }
    return {
        "command": "/venv/bin/python" if scoped else sys.executable,
        "args": [
            "/bridge/effect_mcp.py" if scoped else str(Path(__file__).with_name("effect_mcp.py")),
            json.dumps(public),
        ],
    }


def scoped_enforcement(service, project, config, session_dir):
    """Fail closed when a sandbox bind could expose an approval/credential store."""
    private = [
        Path(service.root).resolve(),
        Path(
            service.config.get(
                "effect_credentials_path", service.root / "harness.effect_credentials.json"
            )
        ).resolve(),
    ]
    exposed = [
        Path("/usr"),
        Path(config.get("python", "/usr/bin/python")).parent.parent.resolve(),
        Path(config.get("binary", "/usr/bin/false")).resolve(),
    ]
    exposed += [
        Path(p).resolve() for p in [project.get("root"), *project.get("additional_roots", [])] if p
    ]
    exposed += [
        Path(path).resolve()
        for path in ("/etc/ssl", "/etc/pki", "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf")
        if Path(path).exists()
    ]
    code_host = (
        Path(config.get("binary", "/usr/bin/false")).resolve().with_name("codex-code-mode-host")
    )
    if code_host.exists():
        exposed.append(code_host.resolve())
    if config.get("auth_file"):
        auth = Path(config["auth_file"]).resolve()
        if auth.is_relative_to(Path(service.root).resolve()):
            return "unenforced"
        exposed.append(auth)
    private.append(Path(service.root).resolve() / "approval_sessions.sqlite3")
    # A dedicated session subdirectory is safe; binding the entire state is not.
    if session_dir:
        exposed.append(Path(session_dir).resolve())
        private += [
            Path(service.root).resolve() / "approval_sessions.sqlite3",
            Path(service.root).resolve() / "harness.effect_credentials.json",
        ]
    return (
        "unenforced"
        if any(
            secret == root or secret.is_relative_to(root) for root in exposed for secret in private
        )
        else "mediated"
    )
