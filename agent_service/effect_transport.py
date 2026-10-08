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
from .private_storage import private_paths

MAX_REQUEST_BYTES = 256 * 1024


def transport_support(backend, mode):
    supported = backend in ("codex", "claude") and mode == "native"
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
    # Native processes share the owner's filesystem.
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
        config_file = Path(directory) / "capability.json"
        with open(
            config_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)
        ) as stream:
            json.dump({"socket": socket, "token": token}, stream)
        capability["config_file"] = str(config_file)
        try:
            yield capability
        finally:
            server.close()
            for task in list(clients):
                task.cancel()
            await asyncio.gather(*clients, return_exceptions=True)
            await server.wait_closed()


def server_spec(capability):
    """Only the short-lived prepare capability enters the provider configuration."""
    import sys

    return {
        "command": sys.executable,
        "args": [
            str(Path(__file__).with_name("effect_mcp.py")),
            capability["config_file"],
        ],
    }


def validate_scoped_private_files(service):
    """Reject pre-existing aliases before exposing any worker-controlled mounts."""

    try:
        for path in private_paths(service.config, service.root, skip_catalogs=True):
            if path.is_symlink():
                raise APIError("scoped_private_file_linked")
            try:
                if path.is_file() and path.stat().st_nlink != 1:
                    raise APIError("scoped_private_file_linked")
            except FileNotFoundError:
                continue
    except (OSError, RuntimeError):
        raise APIError("scoped_private_files_unavailable") from None
