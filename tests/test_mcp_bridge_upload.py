"""The model-callable upload_path tool: client.json roots, credential denylist, human confirmation."""

import asyncio
import io
import zipfile

import httpx
import pytest
from mcp.server.elicitation import AcceptedElicitation, DeclinedElicitation
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult, ErrorData

from agent_service import mcp_bridge

REAL_CLIENT = httpx.AsyncClient


class Person:
    """A client that shows the confirmation and answers it."""

    def __init__(self, answer="accept"):
        self.answer = answer
        self.messages = []

    async def elicit(self, message, schema):
        self.messages.append(message)
        if self.answer == "unsupported":
            raise McpError(ErrorData(code=-32601, message="Method not found"))
        if self.answer == "accept":
            return AcceptedElicitation(data=schema(confirm=True))
        return DeclinedElicitation()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A scratch HOME, client.json roots and a server that records every upload."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    sent = []

    def server(request):
        sent.append(zipfile.ZipFile(io.BytesIO(request.read())).namelist())
        return httpx.Response(200, json={"workspace_id": "w1", "files": len(sent[-1])})

    settings = {"url": "http://server.test", "upload_roots": [str(home / "work")]}
    monkeypatch.setattr(mcp_bridge, "config", lambda: dict(settings))
    monkeypatch.setattr(
        mcp_bridge.httpx,
        "AsyncClient",
        lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(server), **kwargs),
    )
    return home, settings, sent


def upload(path, person):
    return asyncio.run(mcp_bridge.upload_path("p", str(path), person))


def write(path, text="data"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


CREDENTIAL_STORES = [
    "Library/Keychains/login.keychain-db",
    "Library/Application Support/Google/Chrome/Default/Cookies",
    "Library/Application Support/Google/Chrome/Default/Login Data",
    ".gnupg/pubring.kbx",
    ".local/share/keyrings/login.keyring",
    ".local/share/keepharness/claude-cli-login",
    ".password-store/bank.gpg",
    ".netrc",
    ".git-credentials",
    ".docker/config.json",
    ".kube/config",
    ".var/app/com.google.Chrome/config/google-chrome/Default/Cookies",
    "id_ed25519",
]


def test_upload_path_scoped_and_confirmed(client):
    home, settings, sent = client
    folder = home / "work" / "report"
    write(folder / "notes.txt", "work transcript")
    for name in (".netrc", "id_rsa", "deploy.p12", ".git-credentials"):
        write(folder / name)
    write(home / "Documents" / "outside.txt")

    # Outside the configured roots: refused before anything is read or asked.
    person = Person()
    assert upload(home / "Documents" / "outside.txt", person) == {
        "error": "path_outside_upload_roots",
        "upload_roots": [str(home / "work")],
    }
    # Declined: nothing is sent.
    declining = Person("decline")
    assert upload(folder, declining) == {"error": "upload_not_confirmed"}
    assert str(folder) in declining.messages[0] and "1 file" in declining.messages[0]
    # A client without confirmation support: nothing is sent.
    assert upload(folder, Person("unsupported")) == {"error": "upload_confirmation_unavailable"}
    assert person.messages == [] and sent == []

    # Confirmed: only the ordinary file leaves; credential files are reported as excluded.
    result = upload(folder, person)
    assert result["workspace_id"] == "w1", result
    assert sent == [["notes.txt"]]
    assert result["excluded_count"] == 4
    assert len(person.messages) == 1

    # Without roots in client.json the tool refuses every path.
    del settings["upload_roots"]
    assert upload(folder, person)["error"] == "upload_roots_not_configured"
    assert sent == [["notes.txt"]]


@pytest.mark.parametrize("store", CREDENTIAL_STORES)
def test_upload_path_refuses_credential_stores_even_inside_a_root(client, store):
    home, settings, sent = client
    settings["upload_roots"] = [str(home)]
    person = Person()
    assert upload(write(home / store), person) == {"error": "credential_or_dependency_path_denied"}
    assert person.messages == [] and sent == []


def test_mcp_client_confirms_through_elicitation(client):
    home, _, sent = client
    write(home / "work" / "brief.md", "brief")
    asked = []

    async def answer(context, params):
        asked.append(params.message)
        return ElicitResult(action="accept", content={"confirm": True})

    async def scenario():
        async with create_connected_server_and_client_session(
            mcp_bridge.mcp, elicitation_callback=answer
        ) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert set(tools["upload_path"].inputSchema["properties"]) == {
                "project_id",
                "local_path",
            }
            assert "user_requested" not in tools["project_services"].inputSchema["properties"]
            return await session.call_tool(
                "upload_path", {"project_id": "p", "local_path": str(home / "work" / "brief.md")}
            )

    result = asyncio.run(scenario())
    assert not result.isError, result
    assert sent == [["brief.md"]]
    assert str(home / "work" / "brief.md") in asked[0]
