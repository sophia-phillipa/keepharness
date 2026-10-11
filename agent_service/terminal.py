"""Pseudo-terminal shell sessions: one interactive shell per project, with bounded output.

The shell runs with job control (``sh -m``) so each foreground command gets its own process group
and the pty's foreground group tells a busy shell from an idle one. A reader thread per session
drains the pty, keeps the last completed lines with ANSI sequences removed, and appends every
output chunk to a bounded event log. Idle and cap decisions read only the injected clock.
"""

import codecs
import contextlib
import fcntl
import logging
import os
import select
import signal
import struct
import termios
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, NoReturn

from adapters.shared.process import child_environment
from agent_service.errors import HarnessError

logger = logging.getLogger(__name__)

Clock = Callable[[], float]

DEFAULT_SHELL: Final = "/bin/sh"
SHELL_ARGS: Final = ("-m",)
MAX_SESSIONS: Final = 4
IDLE_TIMEOUT_SECONDS: Final = 1800.0
RING_LINES: Final = 5000
EVENT_LIMIT: Final = 10_000
EVENT_BYTES_LIMIT: Final = 4 * 1024 * 1024
SCREEN_LINE_LIMIT: Final = 4 * 1024
HANGUP_SECONDS: Final = 0.5
READ_BYTES: Final = 65536
POLL_MILLISECONDS: Final = 50
JOIN_SECONDS: Final = 5.0
FIRST_OUTPUT_SECONDS: Final = 2.0
UNKNOWN_EXIT_CODE: Final = -1


class TerminalError(HarnessError):
    """A terminal failure; ``code`` is the client-facing error code, ``status`` its HTTP status."""


@dataclass(frozen=True, slots=True)
class TerminalEvent:
    """One entry of a session's event log; ``payload`` holds the output text or the exit code."""

    seq: int
    name: str
    payload: dict[str, object]


def _default_shell(environ: Mapping[str, str]) -> str:
    """$SHELL when it is an absolute path to an executable file, else /bin/sh."""
    candidate = environ.get("SHELL", "")
    if os.path.isabs(candidate) and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return DEFAULT_SHELL


class _Mode(Enum):
    TEXT = 0
    ESCAPE = 1
    CSI = 2
    OSC = 3


_ESCAPE_OPENERS: Final = {"[": _Mode.CSI, "]": _Mode.OSC}


class _Screen:
    """Completed lines from raw pty output: ANSI sequences removed, a bare CR rewrites the line."""

    def __init__(self, ring_lines: int) -> None:
        self._completed: deque[str] = deque(maxlen=ring_lines)
        self._line: list[str] = []
        self._cursor = 0
        self._mode = _Mode.TEXT

    def feed(self, text: str) -> None:
        for char in text:
            if self._mode is _Mode.TEXT:
                self._text(char)
            else:
                self._sequence(char)

    def lines(self) -> list[str]:
        return list(self._completed)

    def _text(self, char: str) -> None:
        if char == "\x1b":
            self._mode = _Mode.ESCAPE
        elif char == "\n":
            self._completed.append("".join(self._line))
            self._line, self._cursor = [], 0
        elif char == "\r":
            self._cursor = 0
        elif char == "\t" or char >= " ":
            self._put(char)
        # Other control characters (BEL, backspace) carry no text and are dropped.

    def _put(self, char: str) -> None:
        if self._cursor < len(self._line):
            self._line[self._cursor] = char
        elif len(self._line) < SCREEN_LINE_LIMIT:
            self._line.append(char)
        # Past the limit the excess is dropped; a later bare CR can still rewrite the kept part.
        self._cursor += 1

    def _sequence(self, char: str) -> None:
        if self._mode is _Mode.ESCAPE:
            self._mode = _ESCAPE_OPENERS.get(char, _Mode.TEXT)
        elif self._mode is _Mode.CSI:
            if "@" <= char <= "~":
                self._mode = _Mode.TEXT
        else:
            self._osc(char)

    def _osc(self, char: str) -> None:
        if char == "\x07":
            self._mode = _Mode.TEXT
        elif char == "\x1b":
            self._mode = _Mode.ESCAPE


def _session_groups(session_id: int) -> set[int]:
    """Process groups of the live processes in a session; vanished or unreadable /proc entries are skipped."""
    groups: set[int] = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return groups
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as stat:
                fields = stat.read().rsplit(b")", 1)[1].split()
            # After the command name: state, ppid, pgrp, session.
            if fields[0] != b"Z" and int(fields[3]) == session_id:
                groups.add(int(fields[2]))
        except (OSError, IndexError, ValueError):
            continue
    return groups


def _signal_group(group: int, sig: int, shell_pid: int | None) -> None:
    """Signal a process group; when the kernel refuses it, signal the shell pid alone if one is given."""
    try:
        os.killpg(group, sig)
    except ProcessLookupError:
        return
    except OSError as error:
        logger.warning(
            "terminal group signal failed error=%s errno=%s",
            type(error).__name__,
            error.errno,
        )
        if shell_pid is not None and isinstance(error, PermissionError):
            with contextlib.suppress(OSError):
                os.kill(shell_pid, sig)


def _event_size(event: TerminalEvent) -> int:
    text = event.payload.get("text")
    return len(text.encode()) if isinstance(text, str) else 0


def _exec_shell(shell: str, workdir: str, env: dict[str, str]) -> NoReturn:
    """Run in the forked child: become the shell, or exit 127 if that fails."""
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        os.chdir(workdir)
        os.execve(shell, [shell, *SHELL_ARGS], env)
    finally:
        os._exit(127)


class TerminalSession:
    """One shell in a pty, its reader thread, the line screen and the bounded event log.

    Lock order: ``_lock`` (state) before ``_fd_lock`` (pty descriptor). The reader thread takes
    ``_lock`` only; it closes the descriptor after the exit or stop flag is set.
    """

    def __init__(
        self,
        *,
        session_id: str,
        project_id: str,
        pid: int,
        master_fd: int,
        clock: Clock,
        ring_lines: int,
    ) -> None:
        self.id = session_id
        self.project_id = project_id
        self._pid = pid
        self._master_fd = master_fd
        self._clock = clock
        self._lock = threading.Lock()
        self._fd_lock = threading.Lock()
        self._fd_open = True
        self._screen = _Screen(ring_lines)
        self._events: deque[TerminalEvent] = deque()
        self._event_bytes = 0
        self._seq = 0
        self._echo = True  # the flag of the last output event; a new pty starts with echo on
        self._last_activity = clock()
        self._attached = 0
        self._ended = False
        self._exited = False
        self._returncode: int | None = None
        self._stop = threading.Event()
        self._printed = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"terminal-{session_id}", daemon=True
        )

    def start_reader(self) -> None:
        self._thread.start()
        # The shell's first output is its prompt. Input written before it is echoed ahead of the
        # prompt, which then glues onto the first output line, so wait for the prompt first.
        self._printed.wait(FIRST_OUTPUT_SECONDS)

    def write(self, data: str) -> None:
        with self._lock:
            self._require_running()
            self._last_activity = self._clock()
            if ("\n" in data or "\r" in data) and not self._echo_on():
                # With echo off the user's Enter leaves no newline on the output, so the screen
                # records the line end here; the secret itself is never recorded.
                self._record("\n", echo=False)
        self._send(data.encode("utf-8"))

    def resize(self, cols: int, rows: int) -> None:
        with self._fd_lock:
            if not self._fd_open:
                raise TerminalError("terminal_exited", 409)
            try:
                fcntl.ioctl(
                    self._master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)
                )
            except OSError as exc:
                raise TerminalError("terminal_exited", 409) from exc

    def lines(self) -> list[str]:
        with self._lock:
            return self._screen.lines()

    def events_after(self, seq: int) -> list[TerminalEvent]:
        with self._lock:
            return [event for event in self._events if event.seq > seq]

    def has_foreground_child(self) -> bool:
        with self._fd_lock:
            if not self._fd_open:
                return False
            try:
                return os.tcgetpgrp(self._master_fd) != self._pid
            except OSError:
                return False

    def attach_stream(self) -> None:
        with self._lock:
            self._attached += 1

    def detach_stream(self) -> None:
        with self._lock:
            self._attached = max(0, self._attached - 1)
            self._last_activity = self._clock()

    def is_alive(self) -> bool:
        with self._lock:
            return not (self._ended or self._exited)

    def last_activity(self) -> float:
        with self._lock:
            return self._last_activity

    def idle_since(self) -> float | None:
        """Return when the idle clock started; None while a stream is attached or after exit."""
        with self._lock:
            if self._attached or self._ended or self._exited:
                return None
            return self._last_activity

    def mark_ended(self) -> bool:
        """Refuse further input and reattach; returns True when the shell still runs and must be signalled."""
        with self._lock:
            self._ended = True
            return not self._exited

    def end(self, reason: str) -> None:
        if self.mark_ended():
            self._kill_groups()
        self._stop.set()
        # Wait for the reader to close the master, so the session is gone when this returns.
        self.join()
        logger.info("terminal session ended reason=%s", reason)

    def join(self) -> None:
        self._thread.join(JOIN_SECONDS)

    def _require_running(self) -> None:
        if self._ended or self._exited:
            raise TerminalError("terminal_exited", 409)

    def _send(self, payload: bytes) -> None:
        with self._fd_lock:
            if not self._fd_open:
                raise TerminalError("terminal_exited", 409)
            try:
                view = memoryview(payload)
                while view:
                    view = view[os.write(self._master_fd, view) :]
            except OSError as exc:
                raise TerminalError("terminal_exited", 409) from exc

    def _echo_on(self) -> bool:
        """Read the ECHO flag from the master; the caller guarantees the descriptor is open."""
        try:
            return bool(termios.tcgetattr(self._master_fd)[3] & termios.ECHO)
        except termios.error:
            return False

    def _kill_groups(self) -> None:
        """End the shell's jobs: SIGHUP their groups, then SIGKILL whatever still runs in the shell's session.

        Takes no lock: the wait below must not block other callers.
        """
        self._signal_session(signal.SIGHUP)
        deadline = time.monotonic() + HANGUP_SECONDS
        while _session_groups(self._pid) and time.monotonic() < deadline:
            time.sleep(POLL_MILLISECONDS / 1000)
        self._signal_session(signal.SIGKILL)

    def _signal_session(self, sig: int) -> None:
        """Signal the shell's pid while it is unreaped (a reaped pid may be reused) and each scanned group still in the session."""
        shell_alive = self._returncode is None
        candidates = _session_groups(self._pid)
        if shell_alive:
            candidates.add(self._pid)
        for group in candidates:
            # Re-read /proc right before each signal; a group that has left the session is skipped.
            if (shell_alive and group == self._pid) or group in _session_groups(self._pid):
                _signal_group(group, sig, self._pid if shell_alive else None)

    def _record(self, text: str, *, echo: bool) -> None:
        """Feed the screen and append an output event; the caller holds ``_lock``."""
        self._screen.feed(text)
        self._echo = echo
        self._seq += 1
        self._append(
            TerminalEvent(self._seq, "output", {"seq": self._seq, "text": text, "echo": echo})
        )

    def _append(self, event: TerminalEvent) -> None:
        """Append an event, dropping the oldest past the count or byte cap; holds ``_lock``."""
        self._events.append(event)
        self._event_bytes += _event_size(event)
        while len(self._events) > 1 and (
            len(self._events) > EVENT_LIMIT or self._event_bytes > EVENT_BYTES_LIMIT
        ):
            self._event_bytes -= _event_size(self._events.popleft())

    def _run(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        poller = select.poll()
        poller.register(self._master_fd, select.POLLIN)
        try:
            self._pump(decoder, poller)
        finally:
            self._finish(decoder, poller)

    def _pump(self, decoder: codecs.IncrementalDecoder, poller: select.poll) -> None:
        while not self._stop.is_set():
            if poller.poll(POLL_MILLISECONDS):
                if not self._read(decoder):
                    return
            else:
                if self._reap(block=False):
                    return
                self._publish_echo()

    def _publish_echo(self) -> None:
        """Emit an empty output event when a quiet shell turns echo off or on (hidden input)."""
        echo = self._echo_on()
        with self._lock:
            if echo != self._echo:
                self._record("", echo=echo)

    def _read(self, decoder: codecs.IncrementalDecoder) -> bool:
        try:
            chunk = os.read(self._master_fd, READ_BYTES)
        except OSError:
            # EIO: every slave end is closed, so the shell side is gone.
            return False
        if not chunk:
            return False
        text = decoder.decode(chunk)
        if text:
            with self._lock:
                self._record(text, echo=self._echo_on())
            self._printed.set()
        return True

    def _reap(self, *, block: bool) -> bool:
        try:
            pid, status = os.waitpid(self._pid, 0 if block else os.WNOHANG)
        except ChildProcessError:
            logger.warning("terminal child was reaped outside its session")
            self._returncode = UNKNOWN_EXIT_CODE
            return True
        if pid == 0:
            return False
        self._returncode = os.waitstatus_to_exitcode(status)
        return True

    def _finish(self, decoder: codecs.IncrementalDecoder, poller: select.poll) -> None:
        try:
            self._kill_groups()
            if self._returncode is None:
                self._reap(block=True)
            while poller.poll(0) and self._read(decoder):
                pass
            code = self._returncode if self._returncode is not None else UNKNOWN_EXIT_CODE
            with self._lock:
                self._seq += 1
                self._append(TerminalEvent(self._seq, "exit", {"code": code}))
        finally:
            with self._lock:
                self._exited = True
            self._printed.set()
            self._close_master()
        logger.info("terminal shell exited code=%s", code)

    def _close_master(self) -> None:
        with self._fd_lock:
            self._fd_open = False
            os.close(self._master_fd)


def _is_stale(session: TerminalSession, now: float, idle_timeout: float) -> bool:
    since = session.idle_since()
    return since is not None and now - since >= idle_timeout


def _end_quietly(session: TerminalSession, reason: str) -> bool:
    """End one session so its failure cannot stop a sweep; log only the error type and errno."""
    try:
        session.end(reason)
    except Exception as error:
        logger.warning(
            "terminal session end failed reason=%s error=%s errno=%s",
            reason,
            type(error).__name__,
            getattr(error, "errno", None),
        )
        return False
    return True


class TerminalManager:
    """Owns the project shells: one live session per project, a cap, and idle reaping."""

    def __init__(
        self,
        *,
        clock: Clock,
        shell: str | None = None,
        environ: Mapping[str, str] | None = None,
        max_sessions: int = MAX_SESSIONS,
        idle_timeout: float = IDLE_TIMEOUT_SECONDS,
        ring_lines: int = RING_LINES,
    ) -> None:
        self._clock = clock
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._shell = shell or _default_shell(self._environ)
        self._max_sessions = max_sessions
        self._idle_timeout = idle_timeout
        self._ring_lines = ring_lines
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = threading.Lock()

    def start(self, project_id: str, cwd: str) -> tuple[TerminalSession, bool]:
        with self._lock:
            live = self._live_session(project_id)
            if live is not None:
                return live, False
            self._make_room()
            session = self._spawn(project_id, cwd)
            self._sessions[session.id] = session
            return session, True

    def get(self, session_id: str) -> TerminalSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise TerminalError("terminal_not_found", 404)
        return session

    def reap_idle(self) -> list[str]:
        now = self._clock()
        with self._lock:
            stale = [s for s in self._sessions.values() if _is_stale(s, now, self._idle_timeout)]
            for session in stale:
                session.mark_ended()
        # Ending blocks (reader join, hangup wait), so it runs outside the manager lock.
        return [session.id for session in stale if _end_quietly(session, "idle")]

    def close_all(self) -> None:
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            _end_quietly(session, "closed")

    def _live_session(self, project_id: str) -> TerminalSession | None:
        for session in self._sessions.values():
            if session.project_id == project_id and session.is_alive():
                return session
        return None

    def _make_room(self) -> None:
        """Drop finished sessions; at the cap, end the least recently used idle session."""
        self._sessions = {sid: s for sid, s in self._sessions.items() if s.is_alive()}
        if len(self._sessions) < self._max_sessions:
            return
        idle = [s for s in self._sessions.values() if not s.has_foreground_child()]
        if not idle:
            raise TerminalError("terminal_limit", 409)
        victim = min(idle, key=lambda session: session.last_activity())
        victim.end("evicted")

    def _spawn(self, project_id: str, cwd: str) -> TerminalSession:
        workdir = os.path.realpath(cwd)
        env = child_environment(self._environ)
        env["TERM"] = "dumb"
        pid, master_fd = os.forkpty()
        if pid == 0:
            _exec_shell(self._shell, workdir, env)
        os.set_inheritable(master_fd, False)
        session = TerminalSession(
            session_id=uuid.uuid4().hex,
            project_id=project_id,
            pid=pid,
            master_fd=master_fd,
            clock=self._clock,
            ring_lines=self._ring_lines,
        )
        session.start_reader()
        return session
