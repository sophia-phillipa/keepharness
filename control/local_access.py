"""Who counts as the local owner: the per-install secret and the Host allow-list.

Every account on this computer can reach 127.0.0.1, so a loopback address proves nothing about
the person behind it (decision D09). The owner's launcher, desktop app and CLI read a private
(0600) secret from the state folder. The secret never leaves the disk: it only signs the one-time
link that ``keepharness open`` prints, and redeeming that link gives the browser a random session
token of its own. The state folder keeps only the token's SHA-256 and its expiry; the admin and
the harness both check the cookie against that list, which ``keepharness open --revoke`` empties.
Cookies ignore the port, so one cookie reaches both services.
"""

import fcntl
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from pathlib import Path
from urllib.parse import urlsplit

from agent_service.errors import UserMessageError

from .product import PRODUCT

KEY_FILE = "local.key"
SESSIONS_FILE = "local-sessions.json"
SESSIONS_LOCK = ".local-sessions.lock"
MAX_SESSIONS = 64
COOKIE = PRODUCT.slug + "-local"
OPEN_PATH = "/open"
OPEN_SECONDS = 300
COOKIE_SECONDS = 30 * 24 * 60 * 60
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost"})
MAX_VALUE = 256
PROC_NET_TCP = Path("/proc/net/tcp")
UID_MAP = Path("/proc/self/uid_map")
TAILSCALED_UID = 0
ESTABLISHED = "01"
# Inside a user namespace host uid 0 is unmapped (shown as 65534, like every other unmapped
# account), so Serve cannot be proven there and 65534 must never be accepted instead.
USER_NAMESPACE_NOTICE = (
    "Tailnet sign-in is off: KeepHarness runs inside a user namespace; "
    "run it on the host"
)


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
        raise UserMessageError(f"{path} is empty; delete it and start {PRODUCT.name} again.")
    return secret


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def read_sessions(state: Path) -> dict[str, int]:
    """The live list: token SHA-256 to expiry (Unix seconds); missing or malformed is empty."""
    try:
        with open(
            os.open(Path(state) / SESSIONS_FILE, os.O_RDONLY | os.O_NOFOLLOW), encoding="utf-8"
        ) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value for key, value in data.items() if isinstance(key, str) and type(value) is int
    }


def _write_sessions(state: Path, sessions: Mapping[str, int]) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=state, prefix=".local-sessions-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(dict(sessions), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, Path(state) / SESSIONS_FILE)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _update_sessions(state: Path, change: Callable[[dict[str, int]], dict[str, int]]) -> None:
    """Read, ``change`` and rewrite the list under an exclusive lock, so writers never interleave."""
    state = Path(state)
    lock = os.open(state / SESSIONS_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _write_sessions(state, change(read_sessions(state)))
    finally:
        os.close(lock)


def issue_session(state: Path, now: float | None = None) -> str:
    """A new browser session: the token goes to the browser, only its SHA-256 stays here."""
    now = time.time() if now is None else now
    token = secrets.token_urlsafe(32)

    def add(sessions: dict[str, int]) -> dict[str, int]:
        live = {key: expires for key, expires in sessions.items() if expires > now}
        live[digest(token)] = int(now + COOKIE_SECONDS)
        # The oldest sessions go first when the list is full.
        return dict(sorted(live.items(), key=lambda item: item[1])[-MAX_SESSIONS:])

    _update_sessions(state, add)
    return token


def revoke_sessions(state: Path) -> int:
    """Sign every browser out; returns how many sessions were still listed."""
    revoked = []

    def clear(sessions: dict[str, int]) -> dict[str, int]:
        revoked.append(len(sessions))
        return {}

    _update_sessions(state, clear)
    return revoked[0]


def has_session(cookies: Mapping[str, str], state: Path, now: float | None = None) -> bool:
    """True when the request carries a listed, unexpired session token.

    A cookie holding the install secret itself (issued before 0.15.0) is not listed, so it is
    refused and the browser must be signed in again through ``keepharness open``.
    """
    value = cookies.get(COOKIE, "")
    if not value or len(value) > MAX_VALUE:
        return False
    expires = read_sessions(state).get(digest(value))
    return expires is not None and expires > (time.time() if now is None else now)


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


def _proc_address(host: str, port: int) -> str:
    """An IPv4 endpoint as /proc/net/tcp prints it: the network-order address as a host word."""
    return f"{int.from_bytes(socket.inet_aton(host), sys.byteorder):08X}:{port:04X}"


def in_user_namespace(uid_map: Path = UID_MAP) -> bool:
    """True when this process runs in a user namespace (uid_map is not the identity map)."""
    try:
        ranges = [int(field) for field in uid_map.read_text(encoding="ascii").split()]
    except (OSError, ValueError):
        return False
    return ranges not in ([], [0, 0, 4294967295])


def served_by_tailscaled(
    client: tuple[str, int] | None, server_port: int, *, proc_net: Path = PROC_NET_TCP
) -> bool:
    """True when the loopback connection from ``client`` was opened by tailscaled (uid 0).

    Tailscale Serve proxies to 127.0.0.1 from tailscaled, which runs as root; any other account
    on this computer owns its own socket, so its uid shows in /proc/net/tcp. Every row for the
    connection (client port to ``server_port``) must be ESTABLISHED, owned by uid 0 and backed
    by a socket inode: once the client closes, the kernel re-labels the row (FIN_WAIT2 or
    TIME_WAIT) as uid 0 with inode 0 whoever opened it, while the harness can still read the
    request. Never cached: another account can rebind a freed source port between two requests.
    Fails closed on anything unreadable, malformed or absent (another platform, no row), and
    never raises.
    """
    if client is None or client[0] != "127.0.0.1":
        return False
    local = _proc_address(client[0], client[1])
    remote = _proc_address("127.0.0.1", server_port)
    proven: list[bool] = []
    try:
        with proc_net.open(encoding="ascii") as rows:
            for row in rows:
                fields = row.split()
                if len(fields) > 9 and fields[1] == local and fields[2] == remote:
                    proven.append(
                        fields[3] == ESTABLISHED
                        and int(fields[7]) == TAILSCALED_UID
                        and int(fields[9]) != 0
                    )
    except (OSError, ValueError):
        return False
    return bool(proven) and all(proven)


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
