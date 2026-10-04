"""Entry-point logging: redacted stderr and bounded private rotating files."""

import logging
import os
import re
import sys
import time
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

    def shouldRollover(self, record):
        if self.stream is None:
            self.stream = self._open()
        return (
            self.stream.tell() + len((self.format(record) + "\n").encode("utf-8")) > self.maxBytes
        )


def log_tail(state, lines=LOG_TAIL_LINES):
    """Read only the bounded suffix of the harness log; redact again for old files."""
    lines = min(LOG_TAIL_LINES, max(1, int(lines)))
    try:
        with (Path(state) / "logs" / "harness.log").open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            start = max(0, stream.tell() - LOG_TAIL_BYTES)
            stream.seek(start)
            content = stream.read(LOG_TAIL_BYTES)
        if start:
            content = content.partition(b"\n")[2]
        return redact(content.decode("utf-8", errors="replace")).splitlines()[-lines:]
    except FileNotFoundError:
        return []


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
