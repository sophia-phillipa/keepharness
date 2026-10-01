"""Private local bindings and execution-local, explicitly advisory injection."""

import fcntl
import json
import os
import re
import stat
import tempfile
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .errors import APIError

_known_secrets = weakref.WeakKeyDictionary()
_environment = ContextVar("integration_environment", default=None)
_blocked_environment = ContextVar("mediated_environment", default=())
_authority_fields = re.compile(
    r"(\b(?:harness_session|nonce)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,;}]+",
    re.IGNORECASE,
)


def redact_secrets(value):
    if isinstance(value, str):
        value = _authority_fields.sub(r"\1[redacted]", value)
        for secret in sorted(
            {item for values in list(_known_secrets.values()) for item in values},
            key=len,
            reverse=True,
        ):
            value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, dict):
        return {
            redact_secrets(key): "[redacted]"
            if isinstance(key, str) and key.lower() in ("harness_session", "nonce")
            else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


class SecretStream:
    """Bounded authority syntax state and known-vault prefixes across provider deltas."""

    def __init__(self):
        self.pending = {}
        self.authority = {}
        self.vault_pending = {}

    def feed(self, channel, value):
        # Parse authority syntax before vault literals can replace its field name.
        # Buffer the resulting text separately so authority names inside a vault
        # credential cannot be emitted ahead of its remaining bytes.
        value = self.vault_pending.pop(channel, "") + self._authority_feed(channel, value)
        secrets = {item for values in list(_known_secrets.values()) for item in values}
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        suffix = max(
            (
                size
                for secret in secrets
                for size in range(1, len(secret))
                if value.endswith(secret[:size])
            ),
            default=0,
        )
        if suffix:
            self.vault_pending[channel] = value[-suffix:]
            value = value[:-suffix]
        return value

    def _authority_feed(self, channel, value):
        value = self.pending.pop(channel, "") + value
        output = []
        while value:
            mode = self.authority.get(channel)
            if mode == "value":
                end = re.search(r"[\s\"'&,;}]", value)
                if end is None:
                    return "".join(output)
                value = value[end.start() :]
                self.authority.pop(channel, None)
            elif mode in ("separator", "leading"):
                prefix = re.match(r"[\s\"']*", value).group()
                output.append(prefix)
                value = value[len(prefix) :]
                if not value:
                    break
                if mode == "separator":
                    if value[0] in ":=":
                        output.append(value[0])
                        value = value[1:]
                        self.authority[channel] = "leading"
                        continue
                    self.authority.pop(channel, None)
                elif value[0] not in "&,;}":
                    output.append("[redacted]")
                    self.authority[channel] = "value"
                    continue
                else:
                    self.authority.pop(channel, None)
            match = re.search(r"\b(?:harness_session|nonce)\b", value, re.IGNORECASE)
            if match:
                output.append(value[: match.end()])
                value = value[match.end() :]
                self.authority[channel] = "separator"
                continue
            names = {"harness_session", "nonce"}
            suffix = max(
                (
                    size
                    for secret in names
                    for size in range(1, len(secret))
                    if (value.lower() if secret in names else value).endswith(secret[:size])
                ),
                default=0,
            )
            if suffix:
                self.pending[channel] = value[-suffix:]
                value = value[:-suffix]
            output.append(value)
            break
        return "".join(output)


@contextmanager
def execution_environment(environment, blocked=()):
    token = _environment.set(dict(environment))
    blocked_token = _blocked_environment.set(tuple(blocked))
    try:
        yield
    finally:
        _environment.reset(token)
        _blocked_environment.reset(blocked_token)


def blocked_environment():
    return _blocked_environment.get()


def injected_environment():
    return dict(_environment.get() or {})


class SecretVault:
    """0600 atomic store. Same-user unrestricted shells remain advisory."""

    def __init__(self, path):
        self.path = Path(path)
        _known_secrets[self] = set()

    def _read(self):
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {}
        except OSError:
            raise APIError("effect_credentials_unavailable") from None
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
                raise APIError("effect_credentials_not_private")
            try:
                values = json.load(stream)
            except (ValueError, UnicodeError):
                raise APIError("effect_credentials_unavailable") from None
        if not isinstance(values, dict):
            raise APIError("effect_credentials_unavailable")
        for binding, value in values.items():
            self._validate(binding, value)
            _known_secrets[self].update(value.values())
        return values

    @staticmethod
    def _validate(binding, value):
        if not isinstance(binding, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", binding):
            raise APIError("secret_binding_invalid")
        if (
            not isinstance(value, dict)
            or not value
            or not all(
                isinstance(key, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
                and isinstance(item, str)
                and item
                and not any(c in item for c in "\r\n\x00")
                for key, item in value.items()
            )
        ):
            raise APIError("secret_value_invalid")

    def get(self, binding):
        value = self._read().get(binding)
        if value is None:
            raise APIError("effect_credentials_unavailable")
        return value

    def remember(self, values):
        _known_secrets[self].update(values)

    def status(self):
        return [
            {"binding": key, "fields": sorted(value)} for key, value in sorted(self._read().items())
        ]

    def set(self, binding, value):
        self._validate(binding, value)
        self._update(binding, value)
        _known_secrets[self].update(value.values())

    def delete(self, binding):
        self._update(binding, None)

    def _update(self, binding, value):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            str(self.path) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = self._read()
            if value is None:
                values.pop(binding, None)
            else:
                values[binding] = value
            descriptor, temporary = tempfile.mkstemp(
                prefix=".harness-secrets-", dir=self.path.parent
            )
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(values, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
