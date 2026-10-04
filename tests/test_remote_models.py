"""Model servers on the user's network: address rules, add/remove routes, key hygiene, discovery.

No real network: every ``httpx.AsyncClient`` is routed to a mock transport.
"""

import asyncio
import inspect
import json
import os
import stat
import sys
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from starlette.testclient import TestClient

from control import discovery, local_models, remote_models, runtime_config
from control.server import Manager, create_app
from tests.owner_session import sign_in

REAL_CLIENT = httpx.AsyncClient
KEY = "sk-remote-" + "k" * 24
URL = "http://machine.tailnet.ts.net:8080"
NETLOC = "machine.tailnet.ts.net:8080"
MODELS = {"data": [{"id": "qwen3:8b", "meta": {"n_ctx": 32768}}, {"id": "gemma-4"}]}
ADD, REMOVE = "/api/remote-model-add", "/api/remote-model-remove"


def models_answer(request):
    return httpx.Response(200, json=MODELS)


class FakeNetwork:
    """Answers by ``host:port``; an unknown host refuses the connection."""

    def __init__(self):
        self.requests = []
        self.options = []
        self.routes = {}

    async def __call__(self, request):
        self.requests.append(request)
        answer = self.routes.get(request.url.netloc.decode())
        if answer is None:
            raise httpx.ConnectError("refused", request=request)
        if isinstance(answer, Exception):
            raise answer
        result = answer(request)
        return await result if inspect.isawaitable(result) else result


@pytest.fixture
def network(monkeypatch):
    fake = FakeNetwork()

    def client(**options):
        fake.options.append(options)
        return REAL_CLIENT(transport=httpx.MockTransport(fake), **options)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    return fake


@pytest.fixture
def open_umask():
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@pytest.fixture
def admin(tmp_path, network, open_umask):
    state = tmp_path / "state"
    with (
        patch("control.manager.Manager.refresh", AsyncMock()),
        TestClient(create_app(state), base_url="http://127.0.0.1:8094") as client,
    ):
        sign_in(client).get("/")
        client.headers["X-Harness-Admin"] = "1"
        yield client, state


def add(client, url=URL, key=None):
    return client.post(ADD, json={"url": url} | ({"key": key} if key else {}))


# --- address rules -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("http://machine.tailnet.ts.net:8080", URL),
        ("http://Machine.TailNet.ts.net:8080/", URL),
        ("http://box:8080/v1", "http://box:8080"),
        ("http://box:8080/v1/", "http://box:8080"),
        ("  http://100.64.0.7:11434  ", "http://100.64.0.7:11434"),
        ("https://box", "https://box"),
        ("https://box:443/", "https://box"),
        ("http://box:80", "http://box"),
        ("http://lan_box.local:1234", "http://lan_box.local:1234"),
        ("http://[fd7a:115c:a1e0::1]:8080/v1", "http://[fd7a:115c:a1e0::1]:8080"),
    ],
)
def test_normalize_url_accepts_and_canonicalizes(given, expected):
    assert remote_models.normalize_url(given) == expected


@pytest.mark.parametrize(
    "given, reason",
    [
        (None, "Enter the address"),
        (8080, "Enter the address"),
        ("", "Enter the address"),
        ("   ", "Enter the address"),
        ("box:8080", "http:// or https://"),
        ("ftp://box", "http:// or https://"),
        ("file:///etc/passwd", "http:// or https://"),
        ("ws://box:8080", "http:// or https://"),
        ("http://", "host name"),
        ("http:///v1", "host name"),
        ("http://user@box:8080", "user name or password"),
        ("http://user:secret@box:8080", "user name or password"),
        ("http://@box:8080", "user name or password"),
        ("http://box:8080/v1/models", "/v1"),
        ("http://box:8080/api", "/v1"),
        ("http://box:8080/v2", "/v1"),
        ("http://box:8080?x=1", "query or fragment"),
        ("http://box:8080/v1?key=abc", "query or fragment"),
        ("http://box:8080/#top", "query or fragment"),
        ("http://box:8080/?", "query or fragment"),
        ("http://box:0", "port"),
        ("http://box:99999", "port"),
        ("http://box:port", "port"),
        ("http://bo x:8080", "host name"),
        ("http://bo$x:8080", "host name"),
        ("http://bóx:8080", "host name"),
        ("http://" + "a" * 300, "too long"),
    ],
)
def test_normalize_url_rejects_with_a_clear_reason(given, reason):
    with pytest.raises(ValueError, match=reason):
        remote_models.normalize_url(given)


def test_key_file_name_is_derived_from_a_hash_of_the_address(tmp_path):
    first = remote_models.key_file(tmp_path, URL)
    assert first.parent == tmp_path and first.suffix == ".key"
    assert "machine" not in first.name
    assert first == remote_models.key_file(tmp_path, URL)
    assert first != remote_models.key_file(tmp_path, "http://other:8080")


# --- add route -----------------------------------------------------------------------------


def test_add_probes_persists_and_returns_the_models(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    response = add(client, URL + "/v1/", KEY)
    assert response.status_code == 200, response.text
    assert response.json() == {"url": URL, "models": ["qwen3:8b", "gemma-4"], "has_key": True}
    (request,) = network.requests
    assert str(request.url) == URL + "/v1/models"
    assert request.headers["authorization"] == "Bearer " + KEY
    probe_options = network.options[0]
    assert probe_options["trust_env"] is False and probe_options["follow_redirects"] is False
    assert json.loads((state / "settings.json").read_text())["remote_models"] == [{"url": URL}]
    assert client.app.state.manager.settings["remote_models"] == [{"url": URL}]


def test_add_without_a_key_sends_no_authorization(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    response = add(client)
    assert response.json()["has_key"] is False
    assert "authorization" not in network.requests[0].headers
    assert not remote_models.key_file(state, URL).exists()


def test_key_is_stored_privately_and_appears_nowhere_else(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    added = add(client, URL, KEY)
    path = remote_models.key_file(state, URL)
    assert path.read_text() == KEY
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manager = client.app.state.manager
    saved = client.post("/api/settings", json=manager.settings)
    assert saved.status_code == 200, saved.text
    responses = [
        added.text,
        saved.text,
        client.get("/api/state").text,
        client.post("/api/settings-export", json={}).text,
        client.post(REMOVE, json={"url": "http://nothing:1"}).text,
    ]
    assert not any(KEY in text for text in responses)
    for file in state.rglob("*"):
        if file.is_file() and file != path:
            assert KEY.encode() not in file.read_bytes(), file
    assert not list(state.glob("*.tmp")) and not list(state.glob(".remote-model-*"))
    assert [p.name for p in [state, *state.rglob("*")] if p.stat().st_mode & 0o077] == []


@pytest.mark.parametrize(
    "answer, reason",
    [
        (httpx.ConnectError("refused"), "Could not connect"),
        (httpx.ReadTimeout("slow"), "did not answer in time"),
        (lambda request: httpx.Response(401), "rejected the API key"),
        (lambda request: httpx.Response(403), "rejected the API key"),
        (lambda request: httpx.Response(404), "HTTP 404"),
        (lambda request: httpx.Response(500), "HTTP 500"),
        (
            lambda request: httpx.Response(302, headers={"location": "http://elsewhere/"}),
            "HTTP 302",
        ),
        (lambda request: httpx.Response(200, text="<html>"), "model list"),
        (lambda request: httpx.Response(200, json={"data": "nope"}), "model list"),
        (lambda request: httpx.Response(200, json=[1, 2]), "model list"),
        (lambda request: httpx.Response(200, content=b"[" * 100_000), "model list"),
        (
            lambda request: httpx.Response(
                200, content=b"x" * (remote_models.MAX_RESPONSE_BYTES + 1)
            ),
            "too large",
        ),
    ],
)
def test_failed_probe_is_a_400_and_persists_nothing(admin, network, answer, reason):
    client, state = admin
    network.routes[NETLOC] = answer
    before = sorted(p.name for p in state.iterdir())
    response = add(client, URL, KEY)
    assert response.status_code == 400, response.text
    assert reason in response.json()["error"]
    assert KEY not in response.text
    assert sorted(p.name for p in state.iterdir()) == before
    assert "remote_models" not in client.app.state.manager.settings


def test_probe_without_a_key_says_one_is_required(admin, network):
    client, _ = admin
    network.routes[NETLOC] = lambda request: httpx.Response(401)
    response = add(client)
    assert response.status_code == 400
    assert "requires an API key" in response.json()["error"]


def test_probe_is_bounded_by_a_total_timeout(admin, network, monkeypatch):
    client, _ = admin

    async def hang(request):
        await asyncio.sleep(30)

    network.routes[NETLOC] = hang
    monkeypatch.setattr(remote_models, "PROBE_SECONDS", 0.2)
    response = add(client)
    assert response.status_code == 400
    assert "did not answer in time" in response.json()["error"]


def test_probe_keeps_only_well_formed_model_ids(network):
    network.routes[NETLOC] = lambda request: httpx.Response(
        200,
        json={
            "data": [
                {"id": "ok/model:q4"},
                {"id": "bad id"},
                {"id": 5},
                "text",
                {"meta": {}},
                {"id": "n" * 200},
                {"id": "ctx", "meta": "not-a-dict"},
            ]
        },
    )
    found = asyncio.run(remote_models.probe(URL))
    assert [m["id"] for m in found] == ["ok/model:q4", "ctx"]
    assert found[1]["context"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"url": "http://user:pw@box:8080"},
        {},
        {"url": URL, "key": "has space"},
        {"url": URL, "key": 12},
    ],
)
def test_add_rejects_bad_input_before_any_request(admin, network, body):
    client, _ = admin
    response = client.post(ADD, json=body)
    assert response.status_code == 400
    assert network.requests == []
    assert "remote_models" not in client.app.state.manager.settings


def test_routes_require_the_admin_cookie_and_header(tmp_path, network):
    with (
        patch("control.manager.Manager.refresh", AsyncMock()),
        TestClient(create_app(tmp_path / "state"), base_url="http://127.0.0.1:8094") as client,
    ):
        manager = client.app.state.manager
        for path in (ADD, REMOVE):
            no_cookie = client.post(path, json={"url": URL}, headers={"X-Harness-Admin": "1"})
            assert no_cookie.status_code == 401
        sign_in(client).get("/")
        for path in (ADD, REMOVE):
            no_header = client.post(path, json={"url": URL})
            assert no_header.status_code == 400
            assert "Administrative header" in no_header.json()["error"]
        assert network.requests == [] and "remote_models" not in manager.settings


PLAIN_LAN = [
    "http://192.168.1.20:8080",
    "http://10.0.0.5:8080",
    "http://gpu-box.local:8080",
    "http://box:8080",
    "http://example.com",
    "http://100.63.255.255:8080",  # just below the Tailscale range
    "http://100.128.0.1:8080",  # just above it
    "http://[fd7a:115c:a1e1::1]:8080",
    "http://machine.ts.net.example.com:8080",
    "http://evil-ts.net:8080",
]
PRIVATE_OR_ENCRYPTED = [
    "http://127.0.0.1:8080",
    "http://127.9.9.9:8080",
    "http://localhost:8080",
    "http://[::1]:8080",
    "http://[::ffff:127.0.0.1]:8080",
    "http://100.64.0.1:8080",
    "http://100.127.255.254:8080",
    "http://[fd7a:115c:a1e0::7]:8080",
    "http://machine.tailnet.ts.net:8080",
    "https://192.168.1.20:8443",
    "https://box",
]


def netloc_of(address):
    return remote_models.urlsplit(remote_models.normalize_url(address)).netloc


@pytest.mark.parametrize("address", PLAIN_LAN)
def test_a_key_is_refused_for_plain_http_outside_this_computer_and_the_tailnet(
    admin, network, address
):
    client, state = admin
    network.routes[netloc_of(address)] = models_answer
    before = sorted(p.name for p in state.iterdir())
    response = add(client, address, KEY)
    assert response.status_code == 400, response.text
    assert "unencrypted" in response.json()["error"]
    assert KEY not in response.text
    assert network.requests == []
    assert sorted(p.name for p in state.iterdir()) == before
    assert "remote_models" not in client.app.state.manager.settings


@pytest.mark.parametrize("address", PRIVATE_OR_ENCRYPTED)
def test_a_key_is_accepted_where_the_connection_is_private_or_encrypted(admin, network, address):
    client, state = admin
    network.routes[netloc_of(address)] = models_answer
    response = add(client, address, KEY)
    assert response.status_code == 200, response.text
    assert response.json()["has_key"] is True and "warning" not in response.json()
    assert remote_models.key_file(state, remote_models.normalize_url(address)).read_text() == KEY


@pytest.mark.parametrize("address", PLAIN_LAN)
def test_keyless_plain_http_is_saved_with_a_warning_that_prompts_travel_unencrypted(
    admin, network, address
):
    client, _ = admin
    network.routes[netloc_of(address)] = models_answer
    response = add(client, address)
    assert response.status_code == 200, response.text
    assert "unencrypted" in response.json()["warning"]
    assert "prompts" in response.json()["warning"]
    assert client.app.state.manager.settings["remote_models"] == [
        {"url": remote_models.normalize_url(address)}
    ]


@pytest.mark.parametrize("address", PRIVATE_OR_ENCRYPTED)
def test_no_warning_where_the_connection_is_private_or_encrypted(admin, network, address):
    client, _ = admin
    network.routes[netloc_of(address)] = models_answer
    assert "warning" not in add(client, address).json()


def test_adding_a_saved_address_again_keeps_one_entry_and_replaces_the_key(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    add(client, URL, KEY)
    assert add(client, URL + "/v1", "another-key-value").status_code == 200
    assert client.app.state.manager.settings["remote_models"] == [{"url": URL}]
    assert remote_models.key_file(state, URL).read_text() == "another-key-value"


def test_adding_a_saved_address_again_without_a_key_drops_the_old_key(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    add(client, URL, KEY)
    assert add(client).json()["has_key"] is False
    assert not remote_models.key_file(state, URL).exists()


def test_a_failed_settings_write_leaves_no_server_and_no_key(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    manager = client.app.state.manager
    with patch.object(manager.state_repository, "save_settings", side_effect=OSError("disk full")):
        response = add(client, URL, KEY)
    assert response.status_code == 400
    assert "remote_models" not in manager.settings
    assert not remote_models.key_file(state, URL).exists()


def test_a_failed_key_write_rolls_the_saved_server_back(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    manager = client.app.state.manager
    with patch.object(remote_models, "store_key", side_effect=OSError("disk full")):
        response = add(client, URL, KEY)
    assert response.status_code == 400
    assert "remote_models" not in manager.settings
    assert not (state / "settings.json").exists()


def test_saved_servers_are_limited(admin, network):
    client, _ = admin
    for index in range(remote_models.MAX_SERVERS + 1):
        network.routes[f"box{index}:8080"] = models_answer
    for index in range(remote_models.MAX_SERVERS):
        assert add(client, f"http://box{index}:8080").status_code == 200
    response = add(client, f"http://box{remote_models.MAX_SERVERS}:8080")
    assert response.status_code == 400 and "At most" in response.json()["error"]


# --- remove route and settings -------------------------------------------------------------


def test_remove_deletes_the_entry_the_key_file_and_audits_the_address_only(admin, network):
    client, state = admin
    network.routes[NETLOC] = models_answer
    add(client, URL, KEY)
    path = remote_models.key_file(state, URL)
    removed = client.post(REMOVE, json={"url": URL + "/v1"})
    assert removed.status_code == 200 and removed.json() == {"removed": True}
    assert not path.exists()
    assert "remote_models" not in client.app.state.manager.settings
    assert "remote_models" not in json.loads((state / "settings.json").read_text())
    audit = (state / "audit.jsonl").read_text()
    assert [json.loads(line)["action"] for line in audit.splitlines()] == [
        "remote_model_added:" + URL,
        "remote_model_removed:" + URL,
    ]
    assert KEY not in audit


def test_removing_an_unknown_address_is_a_400(admin):
    client, _ = admin
    response = client.post(REMOVE, json={"url": URL})
    assert response.status_code == 400 and "not saved" in response.json()["error"]


def test_settings_saves_and_imports_cannot_change_the_saved_servers(admin, network):
    client, _ = admin
    network.routes[NETLOC] = models_answer
    add(client)
    manager = client.app.state.manager
    hostile = {**manager.settings, "remote_models": [{"url": "http://evil.example:1"}]}
    assert manager.validate(hostile)["remote_models"] == [{"url": URL}]
    assert client.post("/api/settings", json=hostile).status_code == 200
    without = {k: v for k, v in manager.settings.items() if k != "remote_models"}
    assert client.post("/api/settings", json=without).status_code == 200
    assert manager.settings["remote_models"] == [{"url": URL}]


def test_settings_without_servers_stay_byte_identical(tmp_path):
    manager = Manager(tmp_path / "state")
    assert "remote_models" not in manager.validate(manager.settings)


# --- discovery -----------------------------------------------------------------------------


def test_discover_merges_reachable_servers_and_skips_unreachable_ones(tmp_path, network):
    key = tmp_path / "server.key"
    key.write_text(KEY + "\n")
    network.routes["good:8080"] = models_answer
    servers = [
        {"url": "http://good:8080", "key_file": str(key)},
        {"url": "http://bad:8080", "key_file": ""},
    ]
    runtimes, statuses = asyncio.run(remote_models.discover(servers))
    assert runtimes[0] == {
        "id": "qwen3:8b",
        "context": 32768,
        "runtime": "remote",
        "url": "http://good:8080",
        "key_file": str(key),
        "remote": True,
        "host": "good",
    }
    assert [r["id"] for r in runtimes] == ["qwen3:8b", "gemma-4"]
    assert statuses == [
        {
            "url": "http://good:8080",
            "host": "good",
            "reachable": True,
            "models": ["qwen3:8b", "gemma-4"],
            "has_key": True,
            "error": "",
        },
        {
            "url": "http://bad:8080",
            "host": "bad",
            "reachable": False,
            "models": [],
            "has_key": False,
            "error": "Could not connect to the server.",
        },
    ]
    sent = {r.url.host: r.headers.get("authorization") for r in network.requests}
    assert sent == {"good": "Bearer " + KEY, "bad": None}


def test_a_hostile_answer_marks_the_server_unreachable_instead_of_breaking_discovery(network):
    network.routes["evil:1"] = lambda request: httpx.Response(200, content=b"[" * 100_000)
    network.routes["good:8080"] = models_answer
    servers = [
        {"url": "http://evil:1", "key_file": ""},
        {"url": "http://good:8080", "key_file": ""},
    ]
    runtimes, statuses = asyncio.run(remote_models.discover(servers))
    assert [s["reachable"] for s in statuses] == [False, True]
    assert {r["url"] for r in runtimes} == {"http://good:8080"}


def test_an_unreadable_key_file_marks_the_server_unreachable(tmp_path, network):
    network.routes["good:8080"] = models_answer
    missing = tmp_path / "gone.key"
    runtimes, statuses = asyncio.run(
        remote_models.discover([{"url": "http://good:8080", "key_file": str(missing)}])
    )
    assert runtimes == [] and statuses[0]["reachable"] is False
    assert "API key could not be read" in statuses[0]["error"]
    assert str(missing) not in statuses[0]["error"]
    assert network.requests == []


def scan_with(tmp_path, servers, local_servers=(), **options):
    with (
        patch.object(local_models, "processes", return_value=list(local_servers)),
        patch("control.discovery.command", AsyncMock(return_value=(1, ""))),
        patch("control.discovery.Path.home", return_value=tmp_path),
    ):
        result = asyncio.run(discovery.scan(servers, **options))
    return next(service for service in result["services"] if service["id"] == "local")


def test_scan_offers_remote_models_beside_local_ones_and_survives_a_dead_server(tmp_path, network):
    network.routes["127.0.0.1:8080"] = lambda request: httpx.Response(
        200, json={"data": [{"id": "gemma-4"}]}
    )
    network.routes["good:8080"] = models_answer
    local_server = {"url": "http://127.0.0.1:8080", "key_file": "", "model_file": "/m/a.gguf"}
    servers = [
        {"url": "http://good:8080", "key_file": ""},
        {"url": "http://dead:8080", "key_file": ""},
    ]
    local = scan_with(tmp_path, servers, [local_server])
    by_id = {runtime["id"]: runtime for runtime in local["runtimes"]}
    assert by_id["qwen3:8b"]["remote"] is True and by_id["qwen3:8b"]["url"] == "http://good:8080"
    # A remote server cannot take over a model id that a local server already serves.
    assert "remote" not in by_id["gemma-4"] and by_id["gemma-4"]["url"] == "http://127.0.0.1:8080"
    assert local["models"] == ["gemma-4", "qwen3:8b"]
    assert [(s["url"], s["reachable"]) for s in local["remote_servers"]] == [
        ("http://good:8080", True),
        ("http://dead:8080", False),
    ]


def test_scan_without_saved_servers_reports_an_empty_list(tmp_path, network):
    local = scan_with(tmp_path, [])
    assert local["remote_servers"] == [] and local["runtimes"] == []


def test_a_second_server_cannot_take_over_an_earlier_servers_model(tmp_path, network):
    network.routes["first:1"] = lambda request: httpx.Response(200, json={"data": [{"id": "m"}]})
    network.routes["second:1"] = lambda request: httpx.Response(200, json={"data": [{"id": "m"}]})
    local = scan_with(
        tmp_path,
        [{"url": "http://first:1", "key_file": ""}, {"url": "http://second:1", "key_file": ""}],
    )
    assert [r["url"] for r in local["runtimes"]] == ["http://first:1"]


def test_a_network_server_cannot_take_an_id_a_saved_local_profile_may_serve(tmp_path, network):
    network.routes["good:8080"] = lambda request: httpx.Response(
        200, json={"data": [{"id": "managed-local"}, {"id": "other"}]}
    )
    local = scan_with(
        tmp_path, [{"url": "http://good:8080", "key_file": ""}], reserved={"managed-local"}
    )
    assert [runtime["id"] for runtime in local["runtimes"]] == ["other"]
    assert local["models"] == ["other"]


def test_a_network_server_cannot_take_the_name_of_an_installed_ollama_model(tmp_path, network):
    network.routes["127.0.0.1:11434"] = lambda request: httpx.Response(
        200, json={"models": [{"name": "qwen3:8b"}]}
    )
    network.routes["good:8080"] = models_answer
    local = scan_with(tmp_path, [{"url": "http://good:8080", "key_file": ""}])
    assert [runtime["id"] for runtime in local["runtimes"]] == ["gemma-4"]
    assert local["models"] == ["qwen3:8b", "gemma-4"]


def test_reserved_ids_follow_the_saved_local_profiles(tmp_path):
    assert local_models.reserved_ids(tmp_path) == set()
    weights = (tmp_path / "models" / "qwen.gguf").resolve()
    local_models.save_profile(tmp_path, {"model_file": str(weights)})
    assert local_models.reserved_ids(tmp_path) == {
        local_models.MANAGED_ALIAS,
        str(weights),
        "qwen.gguf",
    }


def test_an_unreadable_profile_catalog_does_not_stop_discovery(tmp_path):
    (tmp_path / "local-profiles.json").write_text("{not json")
    assert local_models.reserved_ids(tmp_path) == set()


def test_refresh_reserves_the_ids_of_saved_local_profiles(tmp_path):
    manager = Manager(tmp_path / "state")
    local_models.save_profile(manager.state, {"model_file": str(tmp_path / "qwen.gguf")})
    with patch("control.discovery.scan", AsyncMock(return_value={"binaries": {}})) as scan:
        asyncio.run(manager.refresh())
    assert local_models.MANAGED_ALIAS in scan.await_args.kwargs["reserved"]


@pytest.mark.parametrize(
    "context, expected",
    [
        (0, None),
        (-5, None),
        (1, 1),
        (32768, 32768),
        (10_000_000, 10_000_000),
        (10_000_001, None),
        (2**63, None),
        (True, None),
        (1.5, None),
        ("4096", None),
    ],
)
def test_the_reported_context_window_is_clamped(context, expected):
    model = remote_models.parse_model({"id": "m", "meta": {"n_ctx": context}})
    assert model == {"id": "m", "context": expected}


def test_refresh_hands_the_saved_servers_and_their_key_files_to_the_scan(tmp_path):
    manager = Manager(tmp_path / "state")
    manager.settings["remote_models"] = [{"url": URL}, {"url": "http://other:1"}]
    remote_models.store_key(manager.state, URL, KEY)
    with patch("control.discovery.scan", AsyncMock(return_value={"binaries": {}})) as scan:
        asyncio.run(manager.refresh())
    scan.assert_awaited_once_with(
        [
            {"url": URL, "key_file": str(remote_models.key_file(manager.state, URL))},
            {"url": "http://other:1", "key_file": ""},
        ],
        reserved=set(),
    )


def test_build_local_maps_a_remote_runtime_to_its_url(tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text("{}")
    key = tmp_path / "server.key"
    runtime = {
        "id": "qwen3:8b",
        "runtime": "remote",
        "url": "http://good:8080",
        "key_file": str(key),
        "remote": True,
        "host": "good",
    }
    config = {"services": {"local": {}}}
    spec = {"enabled": True, "models": ["qwen3:8b"], "mode": "native"}
    checked = {"authenticated": True, "models": {"qwen3:8b": ["configured"]}}
    info = {"binary": sys.executable, "auth_file": str(auth), "runtimes": [runtime]}
    runtime_config.build_local(config, "local", spec, checked, info, tmp_path)
    assert config["local"]["local_models"]["qwen3:8b"] == runtime
    # No weights file means no saved profile: a network model starts with every permission off.
    assert not any(config["services"]["local"]["model_permissions"]["qwen3:8b"].values())
    assert config["local"]["model_roots"] == {"qwen3:8b": []}
