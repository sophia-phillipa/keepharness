"""Bound provider stalls and diagnostics; keep harness authority out of children."""

import asyncio
import math
import os
import re
import signal
from contextlib import asynccontextmanager

from agent_service.log_config import redact
from agent_service.tools import ToolError
from control.product import PRODUCT

STDERR_LIMIT = 64 * 1024
_SECRET_NAME = re.compile(
    r"token|secret|password|passwd|cookie|api.?key|authorization|harness.?session|enrollment.?nonce",
    re.I,
)
_SECRET_FIELD = re.compile(
    r"""(?i)(?<![\w.-])([\w.-]*(?:token|secret|password|passwd|cookie|api[_-]?key|authorization|harness[_-]?session|nonce)[\w.-]*["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;]+)"""
)
_CREDENTIAL_HEADER = re.compile(
    r"(?im)((?:(?:set-)?cookie|(?:proxy-)?authorization)\s*:\s*)[^\r\n]+"
)


def child_environment(environment=None, *, provider=None):
    """Provider authentication is retained; harness/session authority never is."""
    source = os.environ if environment is None else environment
    clean = {
        key: value
        for key, value in source.items()
        if not (
            key.upper().startswith(
                ("TAIL_HARNESS_", "LOCAL_AGENT_", "HARNESS_", PRODUCT.env_prefix + "_")
            )
            or "COOKIE" in key.upper()
            or key.upper() in {"ADMIN_TOKEN", "ADMIN_SECRET", "ADMIN_PASSWORD"}
        )
    }
    # DeepSeek supplies this inference credential explicitly from its key file.
    # A host variable with the same name is never enough to grant this exception.
    provider_key = PRODUCT.env_prefix + "_API_KEY"
    if provider == "deepseek" and environment is not None and provider_key in environment:
        clean[provider_key] = environment[provider_key]
    from agent_service.secret_vault import blocked_environment, injected_environment

    for name in blocked_environment():
        clean.pop(name, None)

    clean.update(injected_environment())
    return clean


def _timeout(value, default):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return default
    return value


class IdleWatchdog:
    """Each blocking operation gets fresh idle time; human callbacks run outside it."""

    def __init__(self, config=None):
        config = config or {}
        self.seconds = _timeout(config.get("idle_timeout_seconds"), 300)
        self.tool_seconds = _timeout(config.get("tool_idle_timeout_seconds"), 3600)
        self.tools = set()

    def observe(self, kind, data):
        tool_id = data.get("tool_id") or data.get("tool") or "tool"
        if kind == "tool_start":
            self.tools.add(tool_id)
        elif kind == "tool_end":
            self.tools.discard(tool_id)

    async def wait(self, operation):
        try:
            async with asyncio.timeout(self.tool_seconds if self.tools else self.seconds):
                return await operation
        except TimeoutError:
            raise ToolError("provider_idle_timeout") from None


class StderrCapture:
    """Continuously drain stderr into a bounded tail, sanitized before it escapes."""

    def __init__(self, environment=None):
        self.buffer = bytearray()
        self.truncated = False
        values = {**os.environ, **(environment or {})}
        self.secrets = sorted(
            {
                value
                for key, value in values.items()
                if len(value) >= 8 and _SECRET_NAME.search(key)
            },
            key=len,
            reverse=True,
        )

    async def drain(self, reader):
        if reader is None:
            return
        while chunk := await reader.read(8192):
            self.buffer.extend(chunk)
            if len(self.buffer) > STDERR_LIMIT:
                del self.buffer[:-STDERR_LIMIT]
                self.truncated = True

    def text(self):
        value = bytes(self.buffer)
        if self.truncated:
            # A chopped credential field would lose its identifying prefix.
            value = value.partition(b"\n")[2]
        text = value.decode("utf-8", errors="replace")
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        text = _CREDENTIAL_HEADER.sub(r"\1[redacted]", text)
        text = _SECRET_FIELD.sub(r"\1[redacted]", redact(text))
        return text.encode()[-STDERR_LIMIT:].decode("utf-8", errors="ignore")


async def stop_process(process):
    """Reap the whole provider session, including shells that inherited its pipes."""

    def send(sig):
        try:
            if getattr(process, "pid", None) is not None:
                os.killpg(process.pid, sig)
            elif process.returncode is None:
                (process.terminate if sig == signal.SIGTERM else process.kill)()
        except ProcessLookupError:
            pass

    async def discard_stdout():
        reader = getattr(process, "stdout", None)
        if reader is not None:
            while await reader.read(8192):
                pass

    # No protocol reader remains at this point. Unread stdout can otherwise
    # leave asyncio's Process.wait waiting for a paused pipe after SIGTERM.
    output = asyncio.create_task(discard_stdout())
    send(signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), 3)
    except asyncio.TimeoutError:
        send(signal.SIGKILL)
        await process.wait()
    finally:
        # The CLI may exit before a descendant that ignores SIGTERM.
        send(signal.SIGKILL)
        output.cancel()
        await asyncio.gather(output, return_exceptions=True)


@asynccontextmanager
async def process_diagnostics(process, provider, event=None, environment=None):
    capture = StderrCapture(environment)
    reader = asyncio.create_task(capture.drain(getattr(process, "stderr", None)))
    failure = None
    try:
        yield
    except BaseException as exc:
        failure = exc
        raise
    finally:
        try:
            await stop_process(process)
        finally:
            try:
                await asyncio.wait_for(reader, 1)
            except (asyncio.TimeoutError, OSError):
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            text = capture.text()
            if text:
                if failure is not None:
                    failure.error_detail = text
                if event is not None:
                    event("provider_stderr", {"provider": provider, "text": text})
