# P6a terminal core. Tests run against agent_service/terminal.py (not written yet: red).
#
# Minimal public API pinned by these tests (the implementer follows it):
#
#   class TerminalError(HarnessError)            # agent_service/errors.py base; .code, .status
#       codes used here: "terminal_limit" (409), "terminal_not_found" (404), "terminal_exited" (409)
#
#   class TerminalManager
#       __init__(self, *, clock, shell=None, environ=None, max_sessions=4,
#                idle_timeout=1800.0, ring_lines=5000)
#           clock:   callable returning seconds (float). Every idle and cap decision reads only it.
#           shell:   default $SHELL, fallback "/bin/sh"; the tests pass "/bin/sh".
#           environ: host mapping filtered by child_environment(); default os.environ.
#       start(project_id: str, cwd: str) -> tuple[TerminalSession, bool]
#           (session, created). created False = reattach to the project's live session.
#           A reattach never counts against max_sessions.
#       get(session_id: str) -> TerminalSession      # TerminalError("terminal_not_found", 404)
#       reap_idle() -> list[str]                     # ends idle sessions, returns their ids
#       close_all() -> None                          # ends every session (teardown)
#
#   class TerminalSession
#       id: str
#       write(data: str) -> None                 # UTF-8 bytes to the pty, unchanged.
#                                                # TerminalError("terminal_exited", 409) after exit
#       lines() -> list[str]                     # last ring_lines completed lines, ANSI stripped
#       events_after(seq: int) -> list[TerminalEvent]
#       has_foreground_child() -> bool           # tcgetpgrp(master) != the shell's process group
#       attach_stream() -> None / detach_stream() -> None
#       is_alive() -> bool
#
#   TerminalEvent (frozen dataclass): seq: int, name: str ("output" | "exit"),
#       payload: dict  # output: {"seq", "text", "echo": bool}; exit: {"code": int}
#
# Rules pinned here:
#   - Ring buffer: lines() keeps at most ring_lines completed lines, oldest dropped first.
#   - Bare "\r" (not part of "\r\n") rewrites the current line. The pty's ONLCR turns each "\n"
#     into "\r\n", so a program's "x\r\n" arrives as "x\r\r\n": that must keep "x".
#   - Echo: the ECHO flag from tcgetattr on the master is read when each output event is made.
#   - Idle: last activity = start, last write() or last detach_stream(). A session with an attached
#     stream is never idle. reap_idle() ends sessions idle for >= idle_timeout seconds.
#   - Cap: a start that would create a session while max_sessions are live ends the live session
#     with no foreground child and the least recent last activity. If every live session has a
#     foreground child, it raises terminal_limit and starts nothing.
#   - Environment: child_environment(environ) plus TERM=dumb; no KEEPHARNESS_* names reach the shell.

import errno
import os
import sys
import threading
import time

import pytest

from agent_service import terminal
from agent_service.errors import HarnessError
from agent_service.terminal import TerminalError, TerminalManager, _Screen

# /bin/sh is dash on this host and has no `read -s`. The echo tests use `stty -echo` and `stty echo`,
# which are the calls `read -s` makes internally, so the ECHO flag is switched the same way.
SHELL = "/bin/sh"
# Real-time guard so a broken implementation fails instead of hanging. It is never a sync point:
# tests wait on observable output or state, and the fake clock drives every idle decision.
GUARD_SECONDS = 10.0
_POLL = threading.Event()
SECRET_PROMPT = (
    "stty -echo; printf 'Password: '; IFS= read -r pw; stty echo; printf 'len=%s\\n' \"${#pw}\"\n"
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def wait_for(predicate, timeout=GUARD_SECONDS):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not met before the guard timeout"
        _POLL.wait(0.01)


def output_events(session):
    return [event for event in session.events_after(0) if event.name == "output"]


def open_session(manager, project_id, cwd):
    session, _ = manager.start(project_id, str(cwd))
    return session


def busy_session(manager, project_id, cwd):
    session = open_session(manager, project_id, cwd)
    session.write("sleep 30\n")
    wait_for(session.has_foreground_child)
    return session


def start_secret_prompt(session):
    session.write(SECRET_PROMPT)
    # The echoed command line also contains "Password:" (echo on); the prompt itself is printed
    # after `stty -echo`, so only that event carries echo off.
    wait_for(
        lambda: any(
            "Password:" in event.payload["text"] and event.payload["echo"] is False
            for event in output_events(session)
        )
    )


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_manager(clock, tmp_path):
    managers = []

    def _make(**overrides):
        options = {
            "clock": clock,
            "shell": SHELL,
            "environ": {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        }
        options.update(overrides)
        manager = TerminalManager(**options)
        managers.append(manager)
        return manager

    yield _make
    for manager in managers:
        manager.close_all()


def test_terminal_error_is_a_harness_error():
    assert issubclass(TerminalError, HarnessError)


def test_events_after_reports_a_gap_when_the_cursor_is_older_than_the_ring(
    make_manager, monkeypatch, tmp_path
):
    monkeypatch.setattr("agent_service.terminal.EVENT_LIMIT", 1)
    session = open_session(make_manager(), "demo", tmp_path)
    session.write("for i in 1 2 3 4 5; do echo line$i; done; exit\n")
    wait_for(lambda: any(event.name == "exit" for event in session.events_after(0)))

    events = session.events_after(0)
    gap, retained = events[0], events[1:]
    assert gap.name == "gap", events
    assert gap.seq == retained[0].seq - 1
    assert gap.payload == {"seq": gap.seq}
    assert session.events_after(gap.seq) == retained
    assert session.events_after(retained[-1].seq) == []


def test_events_after_has_no_gap_and_the_exact_suffix_for_every_cursor(make_manager, tmp_path):
    session = open_session(make_manager(), "demo", tmp_path)
    session.write("echo one; echo two; exit\n")
    wait_for(lambda: any(event.name == "exit" for event in session.events_after(0)))

    events = session.events_after(0)
    assert events[0].seq == 1, events
    assert all(event.name != "gap" for event in events)
    for cursor in range(events[-1].seq + 2):
        expected = [event for event in events if event.seq > cursor]
        assert session.events_after(cursor) == expected, cursor


def test_start_creates_one_session_per_project(make_manager, tmp_path):
    manager = make_manager()
    first, first_created = manager.start("p1", str(tmp_path))
    second, second_created = manager.start("p2", str(tmp_path))
    assert (first_created, second_created) == (True, True)
    assert second.id != first.id


def test_start_reattaches_to_the_live_session_of_the_same_project(make_manager, tmp_path):
    manager = make_manager()
    first, _ = manager.start("p1", str(tmp_path))
    again, created = manager.start("p1", str(tmp_path))
    assert created is False
    assert again.id == first.id


def test_reattach_keeps_the_shell_state_of_the_earlier_start(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("MARK=$((6*7)); echo SET-$MARK\n")
    wait_for(lambda: "SET-42" in session.lines())
    again = open_session(manager, "p1", tmp_path)
    again.write("echo GOT-$MARK\n")
    wait_for(lambda: "GOT-42" in again.lines())


def test_shell_starts_in_the_given_project_folder(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("pwd\n")
    wait_for(lambda: os.path.realpath(tmp_path) in session.lines())


def test_ring_buffer_keeps_only_the_last_ring_lines_completed_lines(make_manager, tmp_path):
    manager = make_manager(ring_lines=3)
    session = open_session(manager, "p1", tmp_path)
    session.write("for n in 1 2 3 4 5; do echo $n; done\n")
    wait_for(lambda: "5" in session.lines())
    assert session.lines() == ["3", "4", "5"]


def test_ring_buffer_default_keeps_the_last_5000_lines(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("i=1; while [ $i -le 5010 ]; do echo $i; i=$((i+1)); done\n")
    wait_for(lambda: "5010" in session.lines())
    lines = session.lines()
    assert len(lines) == 5000
    assert lines[0] == "11"
    assert lines[-1] == "5010"


@pytest.mark.parametrize(
    ("printf_format", "expected"),
    [
        (r"\033[31mred\033[0m", "red"),
        (r"\033[2Kcleared", "cleared"),
        (r"\033]0;window title\007done", "done"),
    ],
    ids=["colour", "erase-line", "osc-title"],
)
def test_ansi_escape_sequences_are_stripped(make_manager, tmp_path, printf_format, expected):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write(f"printf '{printf_format}\\n'\n")
    wait_for(lambda: expected in session.lines())


def test_bare_carriage_return_rewrites_the_current_line(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("printf 'p 10%%\\rp 20%%\\n'\n")
    wait_for(lambda: "p 20%" in session.lines())
    assert "p 10%" not in session.lines()


def test_crlf_line_end_does_not_rewrite_the_line(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("printf 'x\\r\\ny\\n'\n")
    wait_for(lambda: "y" in session.lines())
    assert session.lines()[-2:] == ["x", "y"]


def test_echo_flag_is_off_while_a_secret_is_read(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    start_secret_prompt(session)
    prompts = [event for event in output_events(session) if "Password:" in event.payload["text"]]
    assert prompts[-1].payload["echo"] is False


def test_secret_typed_while_echo_is_off_never_reaches_the_output(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    start_secret_prompt(session)
    session.write("s3cr3t-pass\n")
    wait_for(lambda: "len=11" in session.lines())
    assert [e for e in output_events(session) if "s3cr3t-pass" in e.payload["text"]] == []
    assert [line for line in session.lines() if "s3cr3t-pass" in line] == []


def test_echo_flag_is_back_on_after_the_secret_is_read(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    start_secret_prompt(session)
    session.write("s3cr3t-pass\n")
    wait_for(lambda: "len=11" in session.lines())
    assert output_events(session)[-1].payload["echo"] is True


BASH = "/bin/bash"


@pytest.mark.skipif(not os.access(BASH, os.X_OK), reason="/bin/bash is not available")
def test_readline_prompt_is_not_hidden_input_and_a_secret_read_is(make_manager, tmp_path):
    manager = make_manager(shell=BASH)
    session = open_session(manager, "p1", tmp_path)
    wait_for(lambda: bool(output_events(session)))
    # bash/readline has ECHO and ICANON off at its idle prompt: the screen must not mask that input.
    wait_for(lambda: output_events(session)[-1].payload["echo"] is True)
    session.write(SECRET_PROMPT)
    wait_for(
        lambda: any(
            e.payload["echo"] is False and "Password:" in e.payload["text"]
            for e in output_events(session)
        )
    )
    session.write("s3cr3t-pass\n")
    wait_for(lambda: "len=11" in session.lines())
    wait_for(lambda: output_events(session)[-1].payload["echo"] is True)


def test_hidden_input_flips_the_echo_flag_without_any_output(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("stty -echo; read x\n")  # dash has no `read -s`
    started = time.monotonic()
    wait_for(lambda: any(e.payload["echo"] is False for e in output_events(session)), timeout=2)
    assert time.monotonic() - started < 2
    hidden = [e for e in output_events(session) if e.payload["echo"] is False]
    assert hidden[-1].payload["text"] == ""


@pytest.mark.parametrize(
    ("key", "hex_bytes"),
    [("\t", "09"), ("\x1b[A", "1b5b41")],
    ids=["tab", "up"],
)
def test_key_bytes_reach_the_pty_unchanged(make_manager, tmp_path, key, hex_bytes):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write(
        "IFS= read -r key; printf 'HEX=%s\\n' \"$(printf '%s' \"$key\" | od -An -tx1 | tr -d ' ')\"\n"
    )
    session.write(key + "\n")
    wait_for(lambda: f"HEX={hex_bytes}" in session.lines())


def test_ctrl_c_stops_the_foreground_child_and_the_shell_keeps_running(make_manager, tmp_path):
    manager = make_manager()
    session = busy_session(manager, "p1", tmp_path)
    session.write("\x03")
    wait_for(lambda: not session.has_foreground_child())
    session.write("echo AFTER-$((2*3))\n")
    wait_for(lambda: "AFTER-6" in session.lines())
    assert session.is_alive()


def test_ctrl_d_on_an_empty_line_ends_the_read_with_end_of_file(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("IFS= read -r line; printf 'RC=%s\\n' \"$?\"\n")
    session.write("\x04")
    wait_for(lambda: "RC=1" in session.lines())


def test_cap_ends_the_oldest_idle_session_when_the_fifth_starts(make_manager, tmp_path):
    manager = make_manager()
    idle = open_session(manager, "p1", tmp_path)
    for project in ("p2", "p3", "p4"):
        busy_session(manager, project, tmp_path)
    manager.start("p5", str(tmp_path))
    assert not idle.is_alive()


def test_cap_never_ends_a_busy_session_even_when_it_is_the_oldest(make_manager, tmp_path):
    manager = make_manager()
    oldest_busy = busy_session(manager, "p1", tmp_path)
    open_session(manager, "p2", tmp_path)
    for project in ("p3", "p4"):
        busy_session(manager, project, tmp_path)
    manager.start("p5", str(tmp_path))
    assert oldest_busy.is_alive()


def test_cap_ranks_idle_sessions_by_last_activity_not_by_start_time(make_manager, tmp_path, clock):
    manager = make_manager()
    first = open_session(manager, "p1", tmp_path)  # started at t=0
    clock.advance(10)
    second = open_session(manager, "p2", tmp_path)  # started at t=10
    clock.advance(10)
    for project in ("p3", "p4"):  # started at t=20, busy
        busy_session(manager, project, tmp_path)
    clock.advance(20)
    first.write("echo used\n")  # p1 is active again at t=40
    clock.advance(10)
    manager.start("p5", str(tmp_path))
    assert not second.is_alive()
    assert first.is_alive()


def test_fifth_start_with_four_busy_sessions_answers_terminal_limit(make_manager, tmp_path):
    manager = make_manager()
    for project in ("p1", "p2", "p3", "p4"):
        busy_session(manager, project, tmp_path)
    with pytest.raises(TerminalError) as caught:
        manager.start("p5", str(tmp_path))
    assert caught.value.code == "terminal_limit"
    assert caught.value.status == 409


def test_refused_start_ends_no_busy_session(make_manager, tmp_path):
    manager = make_manager()
    busy = [busy_session(manager, project, tmp_path) for project in ("p1", "p2", "p3", "p4")]
    with pytest.raises(TerminalError):
        manager.start("p5", str(tmp_path))
    assert all(session.is_alive() for session in busy)


def test_reattach_at_the_cap_is_not_refused(make_manager, tmp_path):
    manager = make_manager()
    first = busy_session(manager, "p1", tmp_path)
    for project in ("p2", "p3", "p4"):
        busy_session(manager, project, tmp_path)
    again, created = manager.start("p1", str(tmp_path))
    assert created is False
    assert again.id == first.id


def test_session_goes_idle_after_exactly_30_minutes_without_input_or_stream(
    make_manager, tmp_path, clock
):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    clock.advance(1799)
    assert manager.reap_idle() == []
    clock.advance(1)
    assert manager.reap_idle() == [session.id]
    assert not session.is_alive()


def test_input_restarts_the_30_minute_idle_clock(make_manager, tmp_path, clock):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    clock.advance(1500)
    session.write("\n")
    clock.advance(1500)
    assert manager.reap_idle() == []
    clock.advance(300)
    assert manager.reap_idle() == [session.id]


def test_attached_stream_keeps_a_session_from_going_idle(make_manager, tmp_path, clock):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.attach_stream()
    clock.advance(7200)
    assert manager.reap_idle() == []


def test_idle_clock_starts_when_the_last_stream_detaches(make_manager, tmp_path, clock):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.attach_stream()
    clock.advance(3600)
    session.detach_stream()
    clock.advance(1799)
    assert manager.reap_idle() == []
    clock.advance(1)
    assert manager.reap_idle() == [session.id]


def test_shell_exit_emits_an_exit_event_with_the_code(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("exit 3\n")
    wait_for(lambda: not session.is_alive())
    exits = [event.payload for event in session.events_after(0) if event.name == "exit"]
    assert exits == [{"code": 3}]


def test_input_after_exit_answers_terminal_exited(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("exit 0\n")
    wait_for(lambda: not session.is_alive())
    with pytest.raises(TerminalError) as caught:
        session.write("echo late\n")
    assert caught.value.code == "terminal_exited"
    assert caught.value.status == 409


def test_unknown_session_id_answers_terminal_not_found(make_manager):
    manager = make_manager()
    with pytest.raises(TerminalError) as caught:
        manager.get("no-such-session")
    assert caught.value.code == "terminal_not_found"
    assert caught.value.status == 404


def test_child_environment_sets_term_dumb(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("env\n")
    wait_for(lambda: "TERM=dumb" in session.lines())


def test_child_environment_has_no_keepharness_names(make_manager, tmp_path):
    manager = make_manager(
        environ={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "KEEPHARNESS_TOKEN": "tok-value-xyz",
        }
    )
    session = open_session(manager, "p1", tmp_path)
    session.write("env\n")
    wait_for(lambda: "TERM=dumb" in session.lines())
    assert [line for line in session.lines() if line.startswith("KEEPHARNESS_")] == []
    assert [line for line in session.lines() if "tok-value-xyz" in line] == []


HOST_SECRETS = {
    "ANTHROPIC_API_KEY": "sk-ant-canary-111",
    "OPENAI_API_KEY": "sk-openai-canary-222",
    "ADMIN_TOKEN": "admin-canary-333",
    "TAIL_HARNESS_X": "tail-canary-444",
    "HARNESS_X": "harness-canary-555",
}


def test_shell_environment_keeps_host_secrets_out_and_keeps_path_and_home(make_manager, tmp_path):
    manager = make_manager(environ={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), **HOST_SECRETS})
    session = open_session(manager, "p1", tmp_path)
    session.write("env; echo END-OF-ENV\n")
    wait_for(lambda: "END-OF-ENV" in session.lines())
    lines = session.lines()
    for name, value in HOST_SECRETS.items():
        assert not any(line.startswith(name + "=") for line in lines), name
        assert not any(value in line for line in lines), name
    assert "PATH=/usr/bin:/bin" in lines
    assert f"HOME={tmp_path}" in lines
    assert "TERM=dumb" in lines


def process_gone(pid):
    """True once the pid no longer runs: reaped, or a zombie that only waits for its parent."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as stat:
            return stat.read().rsplit(b")", 1)[1].split()[0] == b"Z"
    except FileNotFoundError:
        return True


def test_permission_error_while_signalling_still_ends_the_session(
    make_manager, tmp_path, monkeypatch
):
    manager = make_manager()
    session = busy_session(manager, "p1", tmp_path)
    master_fd = session._master_fd

    def refuse(group, sig):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "killpg", refuse)
    session.end("deleted")
    wait_for(lambda: not session.is_alive())
    monkeypatch.undo()
    with pytest.raises(OSError):
        os.fstat(master_fd)


def test_reap_idle_still_ends_the_other_stale_sessions_when_one_end_raises(
    make_manager, tmp_path, clock, monkeypatch
):
    manager = make_manager()
    failing = open_session(manager, "p1", tmp_path)
    other = open_session(manager, "p2", tmp_path)
    clock.advance(1800.0)

    def refuse(reason):
        raise RuntimeError("end failed")

    monkeypatch.setattr(failing, "end", refuse)
    assert manager.reap_idle() == [other.id]
    wait_for(lambda: not other.is_alive())
    monkeypatch.undo()
    failing.end("cleanup")


def test_close_all_still_ends_the_other_sessions_when_one_end_raises(
    make_manager, tmp_path, monkeypatch
):
    manager = make_manager()
    failing = open_session(manager, "p1", tmp_path)
    other = open_session(manager, "p2", tmp_path)

    def refuse(reason):
        raise RuntimeError("end failed")

    monkeypatch.setattr(failing, "end", refuse)
    manager.close_all()
    assert not other.is_alive()
    monkeypatch.undo()
    failing.end("cleanup")


@pytest.mark.parametrize("sweep", ["reap_idle", "close_all"])
def test_a_slow_session_end_does_not_block_the_manager_lookups(
    make_manager, tmp_path, clock, monkeypatch, sweep
):
    manager = make_manager()
    slow = open_session(manager, "p1", tmp_path)
    clock.advance(1800.0)
    other = open_session(manager, "p2", tmp_path)
    ending = threading.Event()
    original_end = slow.end

    def slow_end(reason):
        ending.set()
        time.sleep(1.0)
        original_end(reason)

    monkeypatch.setattr(slow, "end", slow_end)
    sweeper = threading.Thread(target=getattr(manager, sweep))
    sweeper.start()
    assert ending.wait(GUARD_SECONDS)
    started = time.monotonic()
    try:
        # reap_idle keeps the other session; close_all already dropped it, so either answer is fast.
        manager.get(other.id)
    except TerminalError:
        pass
    elapsed = time.monotonic() - started
    sweeper.join(GUARD_SECONDS)
    assert elapsed < 0.2
    assert not sweeper.is_alive()
    assert not slow.is_alive()


def test_ending_a_shell_that_exited_on_its_own_signals_no_pid(make_manager, tmp_path, monkeypatch):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    shell_pid = session._pid
    session.write("exit 0\n")
    wait_for(lambda: not session.is_alive())
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda group, sig: signalled.append(("killpg", group, sig)))
    monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append(("kill", pid, sig)))
    session.end("deleted")
    session._kill_groups()
    monkeypatch.undo()
    assert not [call for call in signalled if call[1] == shell_pid]


def test_ending_a_session_kills_background_jobs_of_the_shell(make_manager, tmp_path):
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write("sleep 300 & echo BG=$!\n")
    wait_for(lambda: any(line.startswith("BG=") for line in session.lines()))
    job_line = next(line for line in session.lines() if line.startswith("BG="))
    pid = int(job_line.removeprefix("BG="))
    assert not process_gone(pid)
    session.end("deleted")
    wait_for(lambda: process_gone(pid), timeout=2.0)


def test_event_log_drops_the_oldest_events_past_the_byte_limit(make_manager, tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "EVENT_BYTES_LIMIT", 20_000)
    manager = make_manager()
    session = open_session(manager, "p1", tmp_path)
    session.write(
        "yes 0123456789012345678901234567890123456789 | head -n 1000; echo END-OF-OUTPUT\n"
    )
    wait_for(lambda: "END-OF-OUTPUT" in session.lines())
    events = output_events(session)
    assert sum(len(event.payload["text"].encode()) for event in events) <= 20_000
    assert events[0].seq > 1
    assert "END-OF-OUTPUT" in "".join(event.payload["text"] for event in events)


def test_event_byte_limit_is_four_mebibytes():
    assert terminal.EVENT_BYTES_LIMIT == 4 * 1024 * 1024


def test_screen_line_limit_is_four_kibibytes():
    assert terminal.SCREEN_LINE_LIMIT == 4 * 1024


def test_screen_caps_the_current_line_at_the_line_limit_and_drops_the_excess():
    screen = _Screen(ring_lines=10)
    screen.feed("x" * (terminal.SCREEN_LINE_LIMIT + 1000))
    screen.feed("\n")
    assert screen.lines() == ["x" * terminal.SCREEN_LINE_LIMIT]


@pytest.mark.parametrize("configured", ["bash", "relative/sh", "/nonexistent/sh", "/"])
def test_default_shell_falls_back_to_bin_sh_unless_shell_is_an_absolute_executable(configured):
    assert terminal._default_shell({"SHELL": configured}) == "/bin/sh"


def test_default_shell_falls_back_for_a_non_executable_file(tmp_path):
    plain = tmp_path / "not-executable"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    assert terminal._default_shell({"SHELL": str(plain)}) == "/bin/sh"


def test_default_shell_uses_an_absolute_executable_from_shell_variable():
    assert terminal._default_shell({"SHELL": sys.executable}) == sys.executable
