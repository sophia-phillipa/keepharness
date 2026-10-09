"""Explicit UI-triggered CLI operations; no shell interpolation or automatic installs."""

import asyncio
import os
import re
import signal
import uuid
from urllib.parse import urlsplit

from agent_service.errors import UserMessageError

# CSI (colours, cursor), OSC (hyperlinks, titles), and ordinary terminal escape sequences.
# An OSC never spans a line: a newline ends a bogus one so the text after it is not held back.
ANSI_ESCAPE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b\n]*(?:\x07|\x1b\\)|\x1b[ -/]*[0-~]"
)
# An escape sequence cut by the end of a read: held back until the next read completes it.
ANSI_PARTIAL = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|\][^\x07\x1b\n]*\x1b?|[ -/]+)?$")


# An OSC title or link longer than one read is not a real one: release it as text.
MAX_HELD_ESCAPE = 2048


def released(held):
    """The text of an OSC sequence that never ended; other unfinished sequences are noise."""
    return held[2:] if held.startswith("\x1b]") else ""


def strip_ansi(text):
    """Remove terminal escape codes; the second value is an unfinished trailing sequence."""
    partial = ANSI_PARTIAL.search(text)
    held = partial.group(0) if partial else ""
    done = ANSI_ESCAPE.sub("", text[: len(text) - len(held)])
    if len(held) > MAX_HELD_ESCAPE:
        return done + released(held), ""
    return done, held


class Operations:
    def __init__(self):
        self.jobs = {}
        self.tasks = set()
        self.by_id = {}
        self.stdin = {}
        self.codes = {}  # the code last pasted into a job, kept in memory to redact an echo of it

    def append_output(self, jid, fresh):
        """Add text to a job's output, redacting an echo of the pasted code."""
        text = self.jobs[jid]["output"] + fresh
        if self.codes.get(jid):
            text = text.replace(self.codes[jid], "[redacted]")
        self.jobs[jid]["output"] = text[-12000:]

    def launch(self, args, timeout=300, *, env=None, on_success=None, interactive=False):
        """Run one CLI; ``interactive`` keeps stdin open for one pasted line (login codes)."""
        jid = uuid.uuid4().hex
        self.jobs[jid] = {"id": jid, "state": "running", "output": "", "accepts_input": False}

        async def run():
            proc = None
            held = ""
            try:
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdin=asyncio.subprocess.PIPE if interactive else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                    env=env,
                )
                if interactive:
                    self.stdin[jid] = proc.stdin
                    self.jobs[jid]["accepts_input"] = True
                async with asyncio.timeout(timeout):
                    while True:
                        chunk = await proc.stdout.read(2048)
                        if not chunk:
                            break
                        fresh, held = strip_ansi(held + chunk.decode(errors="replace"))
                        self.append_output(jid, fresh)
                    # An OSC that never ended must not swallow the output that followed it.
                    self.append_output(jid, released(held))
                    succeeded = await proc.wait() == 0
                    if succeeded and on_success:
                        await on_success()
                    self.jobs[jid].update(state="completed" if succeeded else "failed")
            except asyncio.CancelledError:
                self.jobs[jid]["state"] = "cancelled"
                raise
            except asyncio.TimeoutError:
                self.jobs[jid].update(state="failed", output="Timed out. Try again.")
            except OSError as exc:
                self.jobs[jid].update(state="failed", output=str(exc))
            finally:
                self.jobs[jid]["accepts_input"] = False
                self.stdin.pop(jid, None)
                self.codes.pop(jid, None)
                if proc and proc.returncode is None:
                    try:
                        if os.name == "posix":
                            os.killpg(proc.pid, signal.SIGTERM)
                        else:
                            proc.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError:
                        if os.name == "posix":
                            os.killpg(proc.pid, signal.SIGKILL)
                        else:
                            proc.kill()
                        await proc.wait()

        task = asyncio.create_task(run())
        self.by_id[jid] = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.jobs[jid]

    async def send_input(self, jid, text):
        """Write one line to a running interactive operation.

        The text is never persisted; it stays in memory until the job ends so that an echo of it
        can be redacted from the output.
        """
        stream = self.stdin.get(jid)
        if stream is None or not self.jobs.get(jid, {}).get("accepts_input"):
            raise UserMessageError("No login is waiting for a code.")
        self.jobs[jid]["accepts_input"] = False
        self.stdin.pop(jid, None)
        self.codes[jid] = text.strip()
        stream.write(text.encode() + b"\n")
        await stream.drain()
        stream.close()

    def cancel(self, jid):
        task = self.by_id.get(jid)
        if task:
            task.cancel()

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


def operation(binary, provider, data):
    action = data.get("action")
    name = data.get("name", "")
    if not re.fullmatch(r"[A-Za-z0-9_@./:-]{1,160}", name) or name.startswith("-"):
        raise UserMessageError("Invalid name.")
    if action == "login":
        return [binary, "mcp", "login", name]
    if action == "plugin_install":
        return [binary, "plugin", "add" if provider == "codex" else "install", name]
    if action == "plugin_remove":
        return [binary, "plugin", "remove" if provider == "codex" else "uninstall", name]
    if action == "connector_remove":
        return [
            binary,
            "mcp",
            "remove",
            *(["--scope", "user"] if provider == "claude" else []),
            name,
        ]
    if action == "connector_add":
        transport = data.get("transport", "http")
        if transport == "http":
            url = data.get("url", "")
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise UserMessageError(
                    "Use the MCP server's HTTPS URL, with no credentials in the URL."
                )
            return (
                [binary, "mcp", "add", name, "--url", url]
                if provider == "codex"
                else [binary, "mcp", "add", "--scope", "user", "--transport", "http", name, url]
            )
        args = data.get("command", [])
        if (
            not isinstance(args, list)
            or not args
            or any(not isinstance(x, str) or len(x) > 500 for x in args)
        ):
            raise UserMessageError("Command must be a JSON list, with no intermediate shell.")
        return [
            binary,
            "mcp",
            "add",
            *(["--scope", "user"] if provider == "claude" else []),
            name,
            "--",
            *args,
        ]
    raise UserMessageError("Unknown operation.")
