"""Opt-in stderr logging with secret redaction, configured only by entry points."""

import logging
import re
import sys

from control import env

HANDLER_NAME = "keepharness"
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


def configure_logging():
    """Install one redacting stderr handler at ``KEEPHARNESS_LOG_LEVEL`` (default WARNING)."""
    root = logging.getLogger()
    level = env.read("LOG_LEVEL", "WARNING").strip().upper()
    root.setLevel(logging.getLevelNamesMapping().get(level, logging.WARNING))
    if any(handler.name == HANDLER_NAME for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.name = HANDLER_NAME
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
