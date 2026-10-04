"""Model servers on the user's network (a tailnet machine running llama-server, Ollama, LM Studio...).

A saved server is only an address in ``settings["remote_models"]``. Its optional API key lives in a
private file of its own and never appears in settings, the runtime config (except as a ``key_file``
path), logs, audit entries or API responses.
"""

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import tempfile
from collections.abc import Collection
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from urllib.parse import SplitResult, urlsplit

import httpx
from starlette.requests import Request

if TYPE_CHECKING:
    from .manager import Manager

# Total seconds per probe; httpx timeouts apply per read, so a server that trickles bytes
# could otherwise hold discovery forever.
PROBE_SECONDS = 3
MAX_SERVERS = 8
MAX_MODELS = 100
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CONTEXT = 10_000_000  # tokens; a server's claim, bounded before it reaches the UI
MAX_URL_LENGTH = 300
MAX_KEY_LENGTH = 512
DEFAULT_PORTS = MappingProxyType({"http": 80, "https": 443})
HOST_LABEL = re.compile(r"[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?")
# The shape Manager.validate accepts for a model id, so a remote id can always be selected.
MODEL_ID = re.compile(r"[a-zA-Z0-9_./:-]{1,160}")
PORT_ERROR = "The port must be a number from 1 to 65535."
TAILSCALE_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)
UNENCRYPTED_KEY = (
    "An API key cannot be saved for an http:// address outside this computer and your tailnet: "
    "the connection is unencrypted, so the key would travel in plain text. "
    "Use an https:// address or a Tailscale address."
)
UNENCRYPTED_WARNING = (
    "This address uses unencrypted http outside this computer and your tailnet: "
    "your prompts and the model's answers travel in plain text. "
    "Prefer an https:// address or a Tailscale address."
)


def is_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return len(host) <= 253 and all(HOST_LABEL.fullmatch(label) for label in host.split("."))


def clean_address(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Enter the address of the model server.")
    text = value.strip()
    if len(text) > MAX_URL_LENGTH:
        raise ValueError("That address is too long.")
    if "?" in text or "#" in text:
        raise ValueError("The address cannot have a query or fragment.")
    return text


def split_address(text: str) -> SplitResult:
    """Parse ``text`` and check the scheme, the absence of user info and the host name."""
    try:
        parts = urlsplit(text)
    except ValueError:
        raise ValueError("That is not a valid address.") from None
    if parts.scheme not in DEFAULT_PORTS:
        raise ValueError("Use an address that starts with http:// or https://.")
    if "@" in parts.netloc:
        raise ValueError(
            "Do not put a user name or password in the address; use the API key field."
        )
    if not parts.hostname:
        raise ValueError("The address needs a host name.")
    if not is_host(parts.hostname):
        raise ValueError("The host name is not valid.")
    return parts


def parse_port(parts: SplitResult) -> int | None:
    """The port when it is not the scheme's default."""
    try:
        port = parts.port
    except ValueError:
        raise ValueError(PORT_ERROR) from None
    if port == 0:
        raise ValueError(PORT_ERROR)
    return None if port == DEFAULT_PORTS[parts.scheme] else port


def normalize_url(value: object) -> str:
    """The canonical base address (``scheme://host[:port]``) or a ``ValueError`` saying why not."""
    parts = split_address(clean_address(value))
    port = parse_port(parts)
    if parts.path.rstrip("/") not in ("", "/v1"):
        raise ValueError("Use the base address of the server: no path, or only /v1.")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def travels_unencrypted(url: str) -> bool:
    """Plain http to a host that is neither this computer nor on the tailnet (WireGuard encrypts it)."""
    parts = urlsplit(url)
    host = parts.hostname
    if parts.scheme != "http" or host == "localhost" or host.endswith(".ts.net"):
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return True
    address = getattr(address, "ipv4_mapped", None) or address
    return not (address.is_loopback or any(address in network for network in TAILSCALE_NETWORKS))


def key_file(state: Path, url: str) -> Path:
    return Path(state) / f"remote-model-{hashlib.sha256(url.encode()).hexdigest()[:24]}.key"


def validate_key(token: object) -> str:
    token = token.strip() if isinstance(token, str) else token
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= MAX_KEY_LENGTH
        or not token.isascii()
        or not token.isprintable()
        or " " in token
    ):
        raise ValueError("The API key must be 1 to 512 visible characters, with no spaces.")
    return token


def store_key(state: Path, url: str, token: str) -> None:
    """Owner-only from creation: temp file in the same folder, fsync, atomic rename."""
    target = key_file(state, url)
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=".remote-model-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(token)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    directory = os.open(target.parent, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def load_servers(state: Path, settings: dict) -> list[dict]:
    """The saved addresses with the path of their key file (``""`` when there is none)."""
    servers = []
    for item in settings.get("remote_models", []):
        try:
            url = normalize_url(item["url"])
        except (KeyError, TypeError, ValueError):
            continue  # a hand-edited entry must not break the scan
        path = key_file(state, url)
        servers.append({"url": url, "key_file": str(path) if path.is_file() else ""})
    return servers


def check_status(status_code: int, key: str) -> None:
    if status_code in (401, 403):
        raise ValueError(
            "The server rejected the API key." if key else "The server requires an API key."
        )
    if status_code != 200:
        raise ValueError(f"The server answered HTTP {status_code} instead of a model list.")


async def fetch_body(url: str, key: str, path: str = "/v1/models") -> bytearray:
    """GET ``url + path`` within one total deadline and size cap, or raise ``ValueError``."""
    headers = {"Authorization": "Bearer " + key} if key else {}
    body = bytearray()
    try:
        async with (
            asyncio.timeout(PROBE_SECONDS),
            httpx.AsyncClient(
                timeout=PROBE_SECONDS, trust_env=False, follow_redirects=False
            ) as client,
            client.stream("GET", url + path, headers=headers) as response,
        ):
            check_status(response.status_code, key)
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("The model list is too large.")
    except (TimeoutError, httpx.TimeoutException):
        raise ValueError("The server did not answer in time.") from None
    except httpx.HTTPError:
        raise ValueError("Could not connect to the server.") from None
    return body


def parse_model(entry: object) -> dict | None:
    """One well-formed model, or ``None``: the list comes from a machine we do not control."""
    if not (
        isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and MODEL_ID.fullmatch(entry["id"])
    ):
        return None
    meta = entry.get("meta")
    context = meta.get("n_ctx") if isinstance(meta, dict) else None
    valid = type(context) is int and 1 <= context <= MAX_CONTEXT
    return {"id": entry["id"], "context": context if valid else None}


async def probe(url: str, key: str = "") -> list[dict]:
    """List the models at ``<url>/v1/models``, or raise ``ValueError`` with a reason safe to show."""
    body = await fetch_body(url, key)
    try:
        entries = json.loads(body)["data"]
    except (ValueError, KeyError, TypeError, RecursionError):  # deeply nested JSON
        entries = None
    if not isinstance(entries, list):
        raise ValueError("The server did not return an OpenAI-style model list.")
    await require_responses_api(url, key)
    return [model for model in map(parse_model, entries) if model][:MAX_MODELS]


async def require_responses_api(url: str, key: str = "") -> None:
    """An invalid, model-free request tests the route without starting inference."""
    reason = (
        "responses_api_unavailable: This server must support the Responses API (/v1/responses)."
    )
    headers = {"Authorization": "Bearer " + key} if key else {}
    try:
        async with (
            asyncio.timeout(PROBE_SECONDS),
            httpx.AsyncClient(
                timeout=PROBE_SECONDS, trust_env=False, follow_redirects=False
            ) as client,
        ):
            async with client.stream(
                "POST", url + "/v1/responses", headers=headers, json={}
            ) as response:
                if response.status_code in (404, 405, 501) or 300 <= response.status_code < 400:
                    raise ValueError(reason)
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise ValueError(reason)
        json.loads(body)  # Any JSON validation/error dialect proves the route exists.
    except (TimeoutError, httpx.HTTPError, ValueError, RecursionError):
        raise ValueError(reason) from None


async def check_server(server: dict) -> tuple[dict, list[dict]]:
    """Probe one saved server. An unreachable one is a status, never an exception."""
    path = server["key_file"]
    status = {
        "url": server["url"],
        "host": urlsplit(server["url"]).hostname,
        "reachable": False,
        "models": [],
        "has_key": bool(path),
        "error": "",
    }
    try:
        try:
            key = Path(path).read_text(encoding="utf-8").strip() if path else ""
        except (OSError, ValueError):
            raise ValueError("The saved API key could not be read.") from None
        models = await probe(server["url"], key)
    except ValueError as reason:
        return {**status, "error": str(reason)}, []
    return {**status, "reachable": True, "models": [model["id"] for model in models]}, models


async def discover(
    servers: list[dict], taken: Collection[str] = ()
) -> tuple[list[dict], list[dict]]:
    """Runtime entries for the reachable servers, plus one status per server.

    A model id already in ``taken`` (served locally) or by an earlier server is skipped: a
    network machine must not be able to take over a model the user runs elsewhere.
    """
    async with asyncio.TaskGroup() as group:
        checks = [group.create_task(check_server(server)) for server in servers[:MAX_SERVERS]]
    taken = set(taken)
    runtimes = []
    for server, check in zip(servers, checks):
        status, models = check.result()
        for model in models:
            if model["id"] in taken:
                continue
            taken.add(model["id"])
            runtimes.append(
                {
                    "id": model["id"],
                    "context": model["context"],
                    "runtime": "remote",
                    "url": server["url"],
                    "key_file": server["key_file"],
                    "remote": True,
                    "host": status["host"],
                }
            )
    return runtimes, [check.result()[0] for check in checks]


def save_servers(manager: "Manager", servers: list[dict]) -> None:
    settings = {key: value for key, value in manager.settings.items() if key != "remote_models"}
    if servers:
        settings["remote_models"] = servers
    manager.state_repository.save_settings(settings)
    manager.settings = settings


async def add_remote_model(request: Request, manager: "Manager", data: dict) -> dict:
    """Probe the server first; only a reachable one is saved (with its key, if given)."""
    url = normalize_url(data.get("url"))
    token = validate_key(data["key"]) if data.get("key") not in (None, "") else ""
    unencrypted = travels_unencrypted(url)
    if token and unencrypted:
        raise ValueError(UNENCRYPTED_KEY)
    saved = list(manager.settings.get("remote_models", []))
    entry = {"url": url}
    if entry not in saved and len(saved) >= MAX_SERVERS:
        raise ValueError(f"At most {MAX_SERVERS} network servers can be saved. Remove one first.")
    models = await probe(url, token)
    with manager.configuration_change():
        save_servers(manager, saved if entry in saved else [*saved, entry])
        if token:
            store_key(manager.state, url, token)
        else:
            key_file(manager.state, url).unlink(missing_ok=True)
    manager.audit("remote_model_added:" + url)
    result = {"url": url, "models": [model["id"] for model in models], "has_key": bool(token)}
    return {**result, "warning": UNENCRYPTED_WARNING} if unencrypted else result


async def remove_remote_model(request: Request, manager: "Manager", data: dict) -> dict:
    url = normalize_url(data.get("url"))
    saved = list(manager.settings.get("remote_models", []))
    if {"url": url} not in saved:
        raise ValueError("That network server is not saved.")
    with manager.configuration_change():
        save_servers(manager, [item for item in saved if item != {"url": url}])
        key_file(manager.state, url).unlink(missing_ok=True)
    manager.audit("remote_model_removed:" + url)
    return {"removed": True}
