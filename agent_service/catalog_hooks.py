"""Granted native pre-run hooks, independent of provider/global hook discovery."""

import asyncio
import os
from contextlib import contextmanager
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


@contextmanager
def _verified_copy(hook, catalogs):
    """Yield the descriptor of a private in-memory copy of ``hook`` whose bytes were hashed.

    The trust check and the exec read the same bytes, so replacing or rewriting the file after
    the check changes nothing. Files a hook loads itself stay outside the digest.
    """
    verified = {}

    def read(path):
        verified[str(path)] = data = path.read_bytes()
        return data

    for catalog in catalogs:
        require_trusted_hooks(catalog, read)  # re-hash right before exec: fail closed
    data = verified[hook] if hook in verified else Path(hook).read_bytes()
    descriptor = os.memfd_create(Path(hook).name)
    try:
        with open(descriptor, "wb", closefd=False) as copy:
            copy.write(data)
        yield descriptor
    finally:
        os.close(descriptor)


async def run_hooks(runtime, granted, event, *, timeout=30, output_limit=32768):
    hooks = runtime.get("allowed_hooks", [])
    if not hooks:
        return
    if not granted:
        event("catalog_hook", {"outcome": "skipped", "reason": "hooks_not_granted"})
        return
    for hook in hooks:
        process = None
        readers = []
        try:
            async with asyncio.timeout(timeout):
                with _verified_copy(hook, runtime.get("hook_catalogs", [])) as descriptor:
                    process = await asyncio.create_subprocess_exec(
                        hook,
                        executable=f"/proc/self/fd/{descriptor}",
                        pass_fds=(descriptor,),
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
