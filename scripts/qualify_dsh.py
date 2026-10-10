"""Qualify the dsh ACP profile (KeepHarness #51) and write a markdown report.

Run from the repository root: ``python -m scripts.qualify_dsh --dsh <binary> --out <report.md>``.
Without ``--dsh`` every row stays UNVERIFIED. Each probe runs in its own dsh process inside a
disposable sandbox (HOME, DSH_HOME, DSH_AGENTS_HOME and a workspace) that is removed afterwards.
The child environment is the sandbox plus the ``--child-env K=V ...`` pairs. The ``FAKE_DSH_*``
variables are switches of the offline fixture ``tests/fixtures/fake-dsh-acp/dsh``; a real dsh
ignores them, and they exist only to rehearse failure paths. Exit 0 when no row FAILs, 1 when one
does, 2 on bad arguments.
"""

import argparse
import asyncio
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from adapters.gemini.native import AcpConnection, AcpStream, session_missing
from agent_service.secret_vault import redact_secrets
from agent_service.tools import ToolError

PASS: Final = "PASS"
FAIL: Final = "FAIL"
UNVERIFIED: Final = "UNVERIFIED"
PINNED_VERSION: Final = "0.2.1-alpha.1"
PROTOCOL_VERSION: Final = 1
PROBE_SECONDS: Final = 30
CLOSE_SECONDS: Final = 10
VERSION_SECONDS: Final = 10
READ_LIMIT: Final = 1024 * 1024
DEFAULT_MODE: Final = 0o644
UNKNOWN_SESSION: Final = "qualify-no-such-session"
KEY_PATTERN: Final = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}")
U_REASON: Final = "No offline probe proves this item; it stays open under D-043."
USAGE_ROW: Final = "U05"
USAGE_ABSENT_REASON: Final = (
    "usage not emitted by the probe target; permission and sandbox mapping stay open"
)
NO_DSH_REASON: Final = "No dsh binary given; nothing was run."
INITIALIZE: Final[dict[str, Any]] = {
    "protocolVersion": PROTOCOL_VERSION,
    "clientInfo": {"name": "keepharness-qualify", "version": "0"},
    "clientCapabilities": {"fs": {}, "terminal": False},
}
PROMPT: Final[list[dict[str, str]]] = [{"type": "text", "text": "hello"}]
PERMISSION_ENV: Final[dict[str, str]] = {"FAKE_DSH_PERMISSION": "1"}
CANCEL_ENV: Final[dict[str, str]] = {"FAKE_DSH_CHUNKS": "50", "FAKE_DSH_CHUNK_DELAY": "0.02"}

TITLES: Final[dict[str, str]] = {
    "U01": "Minimum supported Node.js version",
    "U02": "Long-term protocol and configuration compatibility",
    "U03": "Unattended API-key write and credentials file permissions",
    "U04": "Account login, headless login and token renewal",
    "U05": "Released ACP behavior, usage accounting, permission and sandbox mapping",
    "U06": "Exact headless argument and event schema",
    "U07": "Plugin enable/disable writer and conflict handling",
    "U08": "Automatic MCP settings import and MCP resources through ACP",
    "U09": "Session storage root and file generation",
    "P01": "ACP v1 handshake; session/load not advertised",
    "P02": "Turn: prompt streams chunks, then end_turn",
    "P03": "Create and list a session",
    "P04": "Owner allow relayed as allow_once",
    "P05": "Owner deny relayed as reject_once",
    "P06": "Cancel stops the stream as cancelled",
    "P07": "Resume a session from another process",
    "P08": "Resuming an unknown session reports session-not-found",
    "P09": f"Pinned version {PINNED_VERSION} with P01-P08 passing",
}
PROBE_IDS: Final = tuple(row_id for row_id in TITLES if row_id.startswith("P"))


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str
    reason: str = ""


@dataclass(slots=True)
class Run:
    """One sandbox shared by the probes of a qualification run."""

    dsh: Path
    env: dict[str, str]
    workspace: Path
    transcript: list[dict[str, Any]]
    usage: list[dict[str, Any]]


Approve = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
Probe = Callable[[Run], Awaitable[Verdict]]


class Recorder(AcpConnection):
    """ACP client that keeps every message it sends, so probes can check the protocol."""

    def __init__(
        self,
        proc: asyncio.subprocess.Process,
        run: Run,
        approve: Approve | None = None,
        permissions: dict[str, bool] | None = None,
    ) -> None:
        self.transcript = run.transcript
        self.usage = run.usage
        self.first_answer = asyncio.Event()
        self.stream = AcpStream(self._on_event)
        super().__init__(proc, self.stream, approve, permissions or {}, "ask")

    def _on_event(self, kind: str, _payload: dict[str, Any]) -> None:
        if kind == "answer_delta":
            self.first_answer.set()
        elif kind == "context_usage":
            self.usage.append(dict(_payload))

    @property
    def answer(self) -> str:
        return self.stream.answer

    async def _send(self, item: dict[str, Any]) -> None:
        self.transcript.append(item)
        await super()._send(item)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})


@contextlib.asynccontextmanager
async def connect(
    run: Run,
    extra_env: dict[str, str],
    approve: Approve | None = None,
    permissions: dict[str, bool] | None = None,
) -> AsyncIterator[Recorder]:
    proc = await asyncio.create_subprocess_exec(
        str(run.dsh),
        "--profile",
        "acp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=READ_LIMIT,
        env={**run.env, **extra_env},
    )
    try:
        yield Recorder(proc, run, approve, permissions)
    finally:
        await close(proc)


async def close(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None:
        proc.stdin.close()
    try:
        async with asyncio.timeout(CLOSE_SECONDS):
            await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def open_session(rpc: Recorder, workspace: Path) -> str:
    await rpc.call("initialize", INITIALIZE)
    created = await rpc.call("session/new", {"cwd": str(workspace), "mcpServers": []})
    return str(created["sessionId"])


async def prompt(rpc: Recorder, session_id: str) -> dict[str, Any]:
    return await rpc.call("session/prompt", {"sessionId": session_id, "prompt": PROMPT})


def resume_params(run: Run, session_id: str) -> dict[str, Any]:
    return {"sessionId": session_id, "cwd": str(run.workspace), "mcpServers": []}


def stop_verdict(result: dict[str, Any], expected: str) -> Verdict:
    stop = result.get("stopReason")
    if stop == expected:
        return Verdict(PASS)
    return Verdict(FAIL, f"stopReason is {stop!r}, expected {expected!r}")


async def cancel_after_first_chunk(rpc: Recorder, session_id: str) -> None:
    await rpc.first_answer.wait()
    # The child may already be gone; the pending prompt call reports that failure.
    with contextlib.suppress(OSError):
        await rpc.notify("session/cancel", {"sessionId": session_id})


async def probe_handshake(run: Run) -> Verdict:
    async with connect(run, {}) as rpc:
        result = await rpc.call("initialize", INITIALIZE)
    capabilities = result.get("agentCapabilities", {})
    if result.get("protocolVersion") != PROTOCOL_VERSION:
        return Verdict(FAIL, "protocolVersion is not 1")
    if capabilities.get("loadSession") is not False:
        return Verdict(FAIL, "loadSession is not advertised as false")
    return Verdict(PASS)


async def probe_turn(run: Run) -> Verdict:
    async with connect(run, {}) as rpc:
        session_id = await open_session(rpc, run.workspace)
        result = await prompt(rpc, session_id)
    if not rpc.answer:
        return Verdict(FAIL, "no answer text was streamed")
    return stop_verdict(result, "end_turn")


async def probe_create_list(run: Run) -> Verdict:
    async with connect(run, {}) as rpc:
        session_id = await open_session(rpc, run.workspace)
        listed = await rpc.call("session/list", {})
        await rpc.call("session/close", {"sessionId": session_id})
    listed_ids = {item.get("sessionId") for item in listed.get("sessions", [])}
    if session_id in listed_ids:
        return Verdict(PASS)
    return Verdict(FAIL, "created session is missing from session/list")


def relayed(asked: list[dict[str, Any]], sent: list[dict[str, Any]], wanted: str) -> bool:
    """True when the client answered with an option of the wanted kind that the agent offered."""
    offered = {
        option.get("optionId")
        for params in asked
        for option in params.get("options", [])
        if option.get("kind") == wanted
    }
    chosen = {
        message["result"].get("outcome", {}).get("optionId")
        for message in sent
        if isinstance(message.get("result"), dict)
    }
    return bool(offered & chosen)


async def permission_probe(run: Run, *, approved: bool) -> Verdict:
    asked: list[dict[str, Any]] = []

    async def approve(_title: str, params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"approved": approved}

    start = len(run.transcript)
    async with connect(run, PERMISSION_ENV, approve, {"shell": True}) as rpc:
        session_id = await open_session(rpc, run.workspace)
        result = await prompt(rpc, session_id)
    wanted = "allow_once" if approved else "reject_once"
    if not asked:
        return Verdict(FAIL, "the agent never asked the owner")
    if not relayed(asked, run.transcript[start:], wanted):
        return Verdict(FAIL, f"the owner decision was not relayed as {wanted}")
    if f"tool:{wanted}" not in rpc.answer:
        return Verdict(FAIL, f"the stream did not carry tool:{wanted} after the answer")
    return stop_verdict(result, "end_turn")


async def probe_allow(run: Run) -> Verdict:
    return await permission_probe(run, approved=True)


async def probe_deny(run: Run) -> Verdict:
    return await permission_probe(run, approved=False)


async def probe_cancel(run: Run) -> Verdict:
    async with connect(run, CANCEL_ENV) as rpc:
        session_id = await open_session(rpc, run.workspace)
        watcher = asyncio.create_task(cancel_after_first_chunk(rpc, session_id))
        try:
            result = await prompt(rpc, session_id)
        finally:
            watcher.cancel()
    return stop_verdict(result, "cancelled")


async def probe_resume(run: Run) -> Verdict:
    async with connect(run, {}) as first:
        session_id = await open_session(first, run.workspace)
        before = await prompt(first, session_id)
    async with connect(run, {}) as second:
        await second.call("initialize", INITIALIZE)
        await second.call("session/resume", resume_params(run, session_id))
        after = await prompt(second, session_id)
    for result in (before, after):
        verdict = stop_verdict(result, "end_turn")
        if verdict.status != PASS:
            return verdict
    return Verdict(PASS)


async def probe_unknown_session(run: Run) -> Verdict:
    async with connect(run, {}) as rpc:
        await rpc.call("initialize", INITIALIZE)
        try:
            await rpc.call("session/resume", resume_params(run, UNKNOWN_SESSION))
        except ToolError as failure:
            if session_missing(failure):
                return Verdict(PASS)
            return Verdict(FAIL, "the error is not session-not-found")
    return Verdict(FAIL, "an unknown session was accepted")


PROBES: Final[dict[str, Probe]] = {
    "P01": probe_handshake,
    "P02": probe_turn,
    "P03": probe_create_list,
    "P04": probe_allow,
    "P05": probe_deny,
    "P06": probe_cancel,
    "P07": probe_resume,
    "P08": probe_unknown_session,
}


async def bounded(probe: Probe, run: Run) -> Verdict:
    try:
        async with asyncio.timeout(PROBE_SECONDS):
            return await probe(run)
    except TimeoutError:
        return Verdict(FAIL, "the probe timed out")
    except ToolError as failure:
        return Verdict(FAIL, str(failure) or "tool error")
    except Exception as failure:  # a failed probe is a row, not a crash of the run
        return Verdict(FAIL, type(failure).__name__)


def read_version(dsh: Path, env: dict[str, str]) -> str | None:
    try:
        done = subprocess.run(
            [str(dsh), "--version"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=VERSION_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def version_verdict(version: str | None, protocol: dict[str, Verdict]) -> Verdict:
    statuses = {verdict.status for verdict in protocol.values()}
    if FAIL in statuses:
        return Verdict(FAIL, "a protocol probe failed")
    if version == PINNED_VERSION and statuses == {PASS}:
        return Verdict(PASS)
    if version is None:
        return Verdict(UNVERIFIED, "dsh did not report a version")
    return Verdict(UNVERIFIED, "the reported version is not the pinned one")


def make_run(dsh: Path, root: Path, child_env: dict[str, str]) -> Run:
    dirs = {name: root / name for name in ("home", "dsh-home", "agents-home", "workspace")}
    for path in dirs.values():
        path.mkdir()
    env = {
        **child_env,
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(dirs["home"]),
        "DSH_HOME": str(dirs["dsh-home"]),
        "DSH_AGENTS_HOME": str(dirs["agents-home"]),
    }
    return Run(dsh=dsh, env=env, workspace=dirs["workspace"], transcript=[], usage=[])


def build_rows(verdicts: dict[str, Verdict]) -> list[dict[str, str]]:
    rows = []
    for row_id, title in TITLES.items():
        default = Verdict(UNVERIFIED, U_REASON)
        verdict = verdicts.get(row_id, default)
        rows.append(
            {"id": row_id, "title": title, "status": verdict.status, "reason": verdict.reason}
        )
    return rows


def sanitize(text: str, sandbox: Path) -> str:
    cleaned = text.replace(str(sandbox), "<sandbox>").replace(str(Path.home()), "<HOME>")
    return redact_secrets(KEY_PATTERN.sub("[redacted-key]", cleaned))


def usage_reason(usage: list[dict[str, Any]]) -> str:
    if not usage:
        return USAGE_ABSENT_REASON
    observed = json.dumps(usage[-1])
    return (
        f"usage observed, last event verbatim: {observed}; permission and sandbox mapping stay open"
    )


def run_sandboxed(dsh: Path, root: Path, child_env: dict[str, str]) -> dict[str, Any]:
    run = make_run(dsh, root, child_env)
    verdicts: dict[str, Verdict] = {}
    for probe_id, probe in PROBES.items():
        verdicts[probe_id] = asyncio.run(bounded(probe, run))
    verdicts["P09"] = version_verdict(read_version(dsh, run.env), verdicts)
    verdicts[USAGE_ROW] = Verdict(UNVERIFIED, usage_reason(run.usage))
    rows = build_rows(verdicts)
    for row in rows:
        row["reason"] = sanitize(row["reason"], root)
    return {"rows": rows, "transcript": run.transcript}


def qualify(
    dsh: str | None, scratch: Path, child_env: dict[str, str] | None = None
) -> dict[str, Any]:
    if dsh is None:
        verdicts = {row_id: Verdict(UNVERIFIED, NO_DSH_REASON) for row_id in PROBE_IDS}
        return {"rows": build_rows(verdicts), "transcript": []}
    root = Path(tempfile.mkdtemp(prefix="qualify-dsh-", dir=scratch))
    try:
        return run_sandboxed(Path(dsh), root, child_env or {})
    finally:
        shutil.rmtree(root)


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def render_report(rows: list[dict[str, str]]) -> str:
    lines = [
        "# dsh ACP qualification (KeepHarness #51)",
        "",
        f"Pinned version: {PINNED_VERSION}. A PASS row proves only the dsh binary that was run.",
        "",
        "| ID | Item | Status | Reason |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {row['id']} | {_cell(row['title'])} | {row['status']} | {_cell(row['reason'])} |"
        for row in rows
    ]
    return "\n".join(lines) + "\n"


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(target: Path, text: str) -> None:
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else DEFAULT_MODE
    fd, tmp = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    fsync_dir(target.parent)


def parse_child_env(pairs: Sequence[str]) -> dict[str, str] | None:
    env: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            return None
        env[key] = value
    return env


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qualify the dsh ACP profile (KeepHarness #51).")
    parser.add_argument("--dsh", help="dsh binary to probe; omit to leave every row UNVERIFIED")
    parser.add_argument("--out", required=True, type=Path, help="markdown report to write")
    parser.add_argument(
        "--child-env",
        nargs="+",
        metavar="K=V",
        default=[],
        help="extra environment for the child, e.g. FAKE_DSH_CRASH_MID_STREAM=1",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    child_env = parse_child_env(args.child_env)
    if child_env is None:
        parser.error("--child-env expects K=V pairs")
    if args.dsh is not None and not Path(args.dsh).is_file():
        parser.error("--dsh must name an existing file")
    rows = qualify(args.dsh, Path(tempfile.gettempdir()), child_env)["rows"]
    write_atomic(args.out, render_report(rows))
    counts = Counter(row["status"] for row in rows)
    print(f"PASS {counts[PASS]} · FAIL {counts[FAIL]} · UNVERIFIED {counts[UNVERIFIED]}")
    return 1 if counts[FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
