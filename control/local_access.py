"""Who counts as the local owner: the per-install secret and the Host allow-list.

Every account on this computer can reach 127.0.0.1, so a loopback address proves nothing about
the person behind it (decision D09). The owner's launcher, desktop app and CLI read a private
(0600) secret from the state folder; a browser receives it as a cookie through a one-time link
that ``keepharness open`` prints. The admin and the harness share the cookie because cookies
ignore the port.
"""

import hashlib
import hmac
import os
import secrets
import tempfile
import time
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path
from urllib.parse import urlsplit

from .product import PRODUCT

KEY_FILE = "local.key"
COOKIE = PRODUCT.slug + "-local"
OPEN_PATH = "/open"
OPEN_SECONDS = 300
COOKIE_SECONDS = 30 * 24 * 60 * 60
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost"})
MAX_VALUE = 256


def ensure_secret(state: Path) -> str:
    """The install's secret, created once with mode 0600 if it does not exist yet."""
    path = Path(state) / KEY_FILE
    try:
        return read_secret(path)
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".local-key-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(secrets.token_urlsafe(32))
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes the complete file or fails when another process won the race.
        os.link(temporary, path)
    except FileExistsError:
        pass
    finally:
        Path(temporary).unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return read_secret(path)


def read_secret(path: Path) -> str:
    with open(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), encoding="utf-8") as stream:
        secret = stream.read().strip()
    if not secret:
        raise ValueError(f"{path} is empty; delete it and start {PRODUCT.name} again.")
    return secret


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def has_secret(cookies: Mapping[str, str], expected: str) -> bool:
    """True when the request carries the cookie whose SHA-256 is ``expected``."""
    value = cookies.get(COOKIE, "")
    return bool(value) and len(value) <= MAX_VALUE and hmac.compare_digest(digest(value), expected)


def host_allowed(host: str, names: Iterable[str]) -> bool:
    """The one Host allow-list of the admin and the harness: it stops DNS rebinding.

    Compares the lower-case name of the ``Host`` header, without its port; malformed is refused.
    """
    if not host or "@" in host or "/" in host:
        return False
    try:
        name = urlsplit("//" + host).hostname
    except ValueError:
        return False
    return bool(name) and name in names


def origin_names(origins: Iterable[str]) -> frozenset[str]:
    """The host names of configured origins, plus the loopback names."""
    return LOOPBACK_NAMES | {urlsplit(origin).hostname or "" for origin in origins} - {""}


def _signature(secret: str, expires: int, nonce: str) -> str:
    return hmac.new(secret.encode(), f"open:{expires}.{nonce}".encode(), "sha256").hexdigest()


def open_ticket(secret: str, now: float | None = None) -> str:
    """A ticket for one browser; any process that can read the secret may issue it."""
    expires = int((time.time() if now is None else now) + OPEN_SECONDS)
    nonce = secrets.token_urlsafe(16)
    return f"{expires}.{nonce}.{_signature(secret, expires, nonce)}"


def consume_ticket(
    secret: str, ticket: str, used: MutableMapping[str, int], now: float | None = None
) -> bool:
    """Accept a ticket once, before it expires; ``used`` remembers the nonces already spent."""
    now = time.time() if now is None else now
    for nonce, expires in list(used.items()):
        if expires < now:
            del used[nonce]
    try:
        expires_text, nonce, signature = ticket.split(".")
        expires = int(expires_text)
    except ValueError:
        return False
    if len(ticket) > MAX_VALUE or not now <= expires <= now + OPEN_SECONDS or nonce in used:
        return False
    if not hmac.compare_digest(signature, _signature(secret, expires, nonce)):
        return False
    used[nonce] = expires
    return True
