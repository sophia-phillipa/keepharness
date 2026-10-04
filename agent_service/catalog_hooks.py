"""Granted native pre-run hooks, independent of provider/global hook discovery."""

import asyncio
from pathlib import Path

from adapters.shared.process import child_environment, stop_process

from .catalog_manifest import require_trusted_hooks
from .errors import APIError
from .secret_vault import redact_secrets


async def _read_output(reader, limit):
    chunks = []
    size = 0
    while chunk := await reader.read(8192):
        size += len(chunk)
        if size > limit:
            raise APIError("catalog_hook_output_limit")
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


async def run_hooks(runtime, granted, event, *, timeout=30, output_limit=32768):
    hooks = runtime.get("allowed_hooks", [])
    if not hooks:
        return
    if not granted:
        event("catalog_hook", {"outcome": "skipped", "reason": "hooks_not_granted"})
        return
    for hook in hooks:
        for catalog in runtime.get("hook_catalogs", []):
            require_trusted_hooks(catalog)  # re-hash right before exec: fail closed
        process = None
        readers = []
        try:
            async with asyncio.timeout(timeout):
                process = await asyncio.create_subprocess_exec(
                    hook,
                    cwd=runtime.get("cwd") or str(Path(hook).parent),
                    env=child_environment(),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
                readers = [
                    asyncio.create_task(_read_output(stream, output_limit))
                    for stream in (process.stdout, process.stderr)
                ]
                stdout, stderr, code = await asyncio.gather(*readers, process.wait())
                event(
                    "catalog_hook",
                    {
                        "hook": hook,
                        "outcome": "done" if code == 0 else "failed",
                        "stdout": redact_secrets(stdout),
                        "stderr": redact_secrets(stderr),
                    },
                )
                if code:
                    raise APIError("catalog_hook_failed")
        except TimeoutError:
            raise APIError("catalog_hook_timeout") from None
        except OSError:
            raise APIError("catalog_hook_unavailable") from None
        finally:
            for reader in readers:
                if not reader.done():
                    reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if process is not None:
                await stop_process(process)
