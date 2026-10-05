"""Entry-point logging: redacted stderr and bounded private rotating files."""

import logging
import os
import re
import sys
import time
from copy import copy
from logging.handlers import RotatingFileHandler
from pathlib import Path

from control import env

HANDLER_NAME = "keepharness"
FILE_HANDLER_NAME = "keepharness-file"
# One active file plus three backups: at most 4 MiB per process, including long records.
MAX_LOG_BYTES = 1024 * 1024
LOG_BACKUP_COUNT = 3
LOG_TAIL_LINES = 200
LOG_TAIL_BYTES = 64 * 1024
REDACTIONS = (
    (re.compile(r"(Bearer\s+)[^\s\"',;]+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"\b(harness_token|admin)=[^\s;,\"']+"), r"\1=[redacted]"),
    (re.compile(r"(\"token\"\s*:\s*\")[^\"]*(\")"), r"\1[redacted]\2"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]+"), "sk-[redacted]"),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,}"), "gh-[redacted]"),
    (re.compile(r"(://)[^\s/@:]+:[^\s/@]*@"), r"\1[redacted]@"),
)


def redact(text):
    from .secret_vault import redact_secrets

    text = redact_secrets(text)
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Mask bearer tokens, session cookies, JSON tokens and API keys in every record."""

    def filter(self, record):
        record.msg, record.args = redact(record.getMessage()), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class BoundedFormatter(logging.Formatter):
    converter = time.gmtime

    def format(self, record):
        encoded = super().format(record).encode("utf-8")
        if len(encoded) >= MAX_LOG_BYTES:
            return encoded[: MAX_LOG_BYTES - 16].decode("utf-8", errors="ignore") + " [truncated]"
        return encoded.decode("utf-8")


class PrivateRotatingHandler(RotatingFileHandler):
    def _open(self):
        stream = open(
            self.baseFilename,
            self.mode,
            encoding="utf-8",
            opener=lambda path, flags: os.open(path, flags, 0o600),
        )
        os.chmod(self.baseFilename, 0o600)
        return stream

    def format(self, record):
        cached = getattr(record, "_keepharness_formatted", None)
        return cached if cached is not None else super().format(record)

    def emit(self, record):
        # Cache on a private record so the formatter runs once, including tracebacks.
        try:
            prepared = copy(record)
            prepared._keepharness_formatted = self.format(record)
            super().emit(prepared)
        except Exception:
            self.handleError(record)

    def shouldRollover(self, record):
        rendered = self.format(record)
        probe = copy(record)
        # stdlib counts characters; padding makes its check count UTF-8 bytes.
        probe._keepharness_formatted = rendered + " " * (
            len(rendered.encode("utf-8")) - len(rendered)
        )
        return super().shouldRollover(probe)


def open_process_log(state):
    """Keep the bounded suffix of the previous run, then redirect a fresh run."""
    path = Path(state) / "harness.log"
    try:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            source.seek(max(0, source.tell() - MAX_LOG_BYTES))
            content = source.read(MAX_LOG_BYTES)
        backup = path.with_name(path.name + ".1")
        with open(backup, "wb", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            os.chmod(backup, 0o600)
            stream.write(content)
    except FileNotFoundError:
        pass
    stream = open(path, "wb", opener=lambda p, f: os.open(p, f, 0o600))
    os.chmod(path, 0o600)
    return stream


def _file_tail(path, lines, byte_limit):
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            start = max(0, stream.tell() - byte_limit)
            stream.seek(start)
            content = stream.read(byte_limit)
        if start:
            _, newline, content = content.partition(b"\n")
            if not newline or not content:
                return ["[line truncated]"]
        return redact(content.decode("utf-8", errors="replace")).splitlines()[-lines:]
    except FileNotFoundError:
        return []


def log_tail(state, lines=LOG_TAIL_LINES):
    """Bound and redact both structured logs and otherwise invisible crash output."""
    lines = min(LOG_TAIL_LINES, max(1, int(lines)))
    state = Path(state)
    process_path = state / "harness.log"
    structured_path = state / "logs" / "harness.log"
    byte_limit = (
        LOG_TAIL_BYTES // 2
        if (process_path.exists() or process_path.with_name(process_path.name + ".1").exists())
        and structured_path.exists()
        else LOG_TAIL_BYTES
    )
    structured = _file_tail(structured_path, lines, byte_limit)
    # A restart moves the crashed run's output to harness.log.1; it fills the byte budget the
    # active log leaves, so both files together never exceed it.
    backup_path = process_path.with_name(process_path.name + ".1")
    process = _file_tail(process_path, lines, byte_limit)
    try:
        spare = byte_limit - min(process_path.stat().st_size, byte_limit)
    except FileNotFoundError:
        spare = byte_limit
    if spare > 0:
        process = _file_tail(backup_path, lines, spare) + process
    if not process:
        return structured
    # Reserve room for both sources when present; every process line is labelled.
    process = process[-(max(1, lines // 2) if structured else lines) :]
    remaining = lines - len(process)
    return (structured[-remaining:] if remaining else []) + [
        "[process output] " + line for line in process
    ]


def configure_logging(state=None, filename="harness.log"):
    """Install redacting stderr/file handlers at ``KEEPHARNESS_LOG_LEVEL`` (WARNING)."""
    root = logging.getLogger()
    level = env.read("LOG_LEVEL", "WARNING").strip().upper()
    root.setLevel(logging.getLevelNamesMapping().get(level, logging.WARNING))
    formatter = BoundedFormatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
    )
    if not any(handler.name == HANDLER_NAME for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.name = HANDLER_NAME
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
    if not any(handler.name == FILE_HANDLER_NAME for handler in root.handlers):
        folder = Path(state if state is not None else env.PRODUCT.state_path()) / "logs"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        handler = PrivateRotatingHandler(
            folder / filename,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.name = FILE_HANDLER_NAME
        handler.setFormatter(formatter)
        handler.addFilter(RedactingFilter())
        root.addHandler(handler)
