import asyncio
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from control.integration_catalog import catalog


def test_slow_cli_initialization_can_finish_before_browser_deadline():
    from control.integration_catalog import _run

    # A real installed CLI took 10 s on its first query; the old 8 s budget lost it.
    code, output = run(
        _run(sys.executable, "-c", 'import time; time.sleep(9); print("catalog-ready")')
    )
    assert code == 0
    assert output.strip() == "catalog-ready"


def run(value):
    return asyncio.run(value)


def test_codex_catalog_combines_configured_mcp_and_available_plugins_without_secrets():
    calls = []

    async def fake_run(*args):
        calls.append(args)
        if args[1:] == ("mcp", "list", "--json"):
            return (
                0,
                '[{"name":"drive","enabled":true,"transport":{"type":"stdio","command":"secret-command","env":{"TOKEN":"secret"}},"auth_status":"authenticated"}]',
            )
        return (
            0,
            '{"installed":[{"pluginId":"github@openai","name":"GitHub","marketplaceName":"openai","version":"1","installed":true,"enabled":true}],"available":[{"pluginId":"linear@openai","name":"Linear","marketplaceName":"openai","version":"2","installed":false,"enabled":false}]}',
        )

    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("codex", "/bin/codex"))

    assert calls == [
        ("/bin/codex", "mcp", "list", "--json"),
        ("/bin/codex", "plugin", "list", "--available", "--json"),
    ]
    assert result["warnings"] == []
    assert result["items"] == [
        {
            "id": "mcp:drive",
            "name": "drive",
            "kind": "mcp",
            "status": "configured",
            "enabled": True,
        },
        {
            "id": "plugin:github@openai",
            "name": "GitHub",
            "kind": "plugin",
            "status": "installed",
            "enabled": True,
            "marketplace": "openai",
            "version": "1",
        },
        {
            "id": "plugin:linear@openai",
            "name": "Linear",
            "kind": "plugin",
            "status": "available",
            "enabled": False,
            "marketplace": "openai",
            "version": "2",
        },
    ]
    assert "secret" not in str(result)


def test_claude_uses_available_plugins_and_only_parses_mcp_names():
    async def fake_run(*args):
        if args[1:] == ("plugin", "list", "--available", "--json"):
            return (
                0,
                '{"installed":[],"available":[{"name":"Calendar","marketplaceName":"anthropic","installed":false,"enabled":false}]}',
            )
        return 0, "drive: https://drive.example/mcp\nlocal-tools: npx local-tools\n"

    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("claude", "/bin/claude"))

    assert result == {
        "items": [
            {"id": "mcp:drive", "name": "drive", "kind": "mcp", "status": "configured"},
            {"id": "mcp:local-tools", "name": "local-tools", "kind": "mcp", "status": "configured"},
            {
                "id": "plugin:Calendar@anthropic",
                "name": "Calendar",
                "kind": "plugin",
                "status": "available",
                "enabled": False,
                "marketplace": "anthropic",
            },
        ],
        "warnings": [],
        "actions": ["connector_add"],
    }


# Shape of a real `claude mcp list` (Claude Code 2.1.x): account connectors carry a
# "claude.ai " prefix and a space in the name, and every line ends with a health marker.
CLAUDE_MCP_LIST = """Checking MCP server health\u2026

claude.ai Gmail: https://gmail.mcp.example/mcp - \u2714 Connected
claude.ai Postman: https://mcp.postman.example/mcp - ! Needs authentication
claude.ai Mercado Libre: https://mcp.example/mcp - \u2718 Failed to connect \u2014 HTTP 503: Error POSTing to endpoint: {"jsonrpc":"2.0","id":0}
graphify: /home/user/.local/bin/graphify-mcp  - \u2714 Connected
"""


def test_claude_account_connectors_are_listed_with_their_health():
    async def fake_run(*args):
        if args[1:] == ("mcp", "list"):
            return 0, CLAUDE_MCP_LIST
        return 0, '{"installed":[],"available":[]}'

    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("claude", "/bin/claude"))

    assert result == {
        "items": [
            {
                "id": "account-app:claude.ai Gmail",
                "name": "claude.ai Gmail",
                "kind": "account-app",
                "status": "connected",
            },
            {
                "id": "account-app:claude.ai Postman",
                "name": "claude.ai Postman",
                "kind": "account-app",
                "status": "needs_authentication",
            },
            {
                "id": "account-app:claude.ai Mercado Libre",
                "name": "claude.ai Mercado Libre",
                "kind": "account-app",
                "status": "failed",
            },
            {"id": "mcp:graphify", "name": "graphify", "kind": "mcp", "status": "configured"},
        ],
        "warnings": [],
        "actions": ["connector_add"],
    }
    # No endpoint, command or provider error text leaks into the catalog.
    assert "http" not in str(result) and "jsonrpc" not in str(result)


def test_catalog_falls_back_to_known_mcp_metadata_when_claude_output_is_unknown():
    async def fake_run(*_args):
        return 1, "not a list"

    fallback = {
        "claude": [{"id": "mcp:drive", "name": "Drive", "kind": "mcp", "status": "configured"}]
    }
    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("claude", "/bin/claude", fallback=fallback))

    assert result["items"] == fallback["claude"]
    assert result["warnings"]


def test_catalog_rejects_unknown_provider_without_executing_a_cli():
    with patch("control.integration_catalog._run", new_callable=AsyncMock) as command:
        result = run(catalog("deepseek", "/bin/codex"))
    assert result == {
        "items": [],
        "warnings": ["Catalog not supported for this provider."],
        "actions": [],
    }
    command.assert_not_awaited()


def test_claude_empty_mcp_list_is_not_a_query_failure():
    async def fake_run(*args):
        return (
            (0, "No MCP servers configured. Use `claude mcp add` to add a server.")
            if args[-2:] == ("mcp", "list")
            else (0, '{"installed":[],"available":[]}')
        )

    with patch("control.integration_catalog._run", side_effect=fake_run):
        assert run(catalog("claude", "/bin/claude")) == {
            "items": [],
            "warnings": [],
            "actions": ["connector_add"],
        }


def test_plugin_descriptions_are_preserved_as_text_for_both_statuses():
    import json

    from control.integration_catalog import _plugins

    items = _plugins(
        json.dumps(
            {
                "installed": [{"id": "installed", "description": "  Explains installed tools.  "}],
                "available": [
                    {"id": "available", "description": "<b>Plain text description</b>"},
                    {"id": "invalid", "description": {"token": "not metadata"}},
                ],
            }
        )
    )
    assert items[0]["description"] == "Explains installed tools."
    assert items[1]["description"] == "<b>Plain text description</b>"
    assert "description" not in items[2]


def test_plugin_detail_metadata_is_allowlisted_and_bounded():
    import json

    from control.integration_catalog import _plugins

    items = _plugins(
        json.dumps(
            {
                "available": [
                    {
                        "id": "documents@official",
                        "marketplaceName": "official",
                        "version": "1.2.3",
                        "author": {"name": "Example Developer", "email": "secret@example.test"},
                        "homepage": "https://example-user:EXAMPLE_TOKEN@example.test/plugin?token=EXAMPLE_QUERY#EXAMPLE_FRAGMENT",
                        "apps": [{"name": "Documents", "token": "secret"}],
                        "skills": ["Draft documents"],
                        "credentials": {"token": "never"},
                        "source": {"source": "local", "path": "/secret/local/path"},
                    },
                    {
                        "id": "issues@official",
                        "repository": {
                            "url": "https://example.test/issues?private=yes#fragment",
                            "token": "secret",
                        },
                    },
                    {"id": "local-file@official", "homepage": "file:///private/example/checkout"},
                    {
                        "id": "local-http@official",
                        "homepage": "http://127.0.0.1/private/example/checkout",
                    },
                    {
                        "id": "local-http-dot@official",
                        "homepage": "http://127.0.0.1./private/example/checkout",
                    },
                    {
                        "id": "local-private-dot@official",
                        "homepage": "http://192.168.1.1./private/example/checkout",
                    },
                    {
                        "id": "local-short-loopback@official",
                        "homepage": "http://127.1/private/example/checkout",
                    },
                    {
                        "id": "public-port@official",
                        "homepage": "https://example.test:8443/plugin?private=yes#fragment",
                    },
                    {
                        "id": "malformed-port@official",
                        "homepage": "https://example.test:not-a-port/plugin",
                    },
                    {
                        "id": "local-hex-ipv4@official",
                        "homepage": "http://0x7f.0.0.1/private/example/checkout",
                    },
                    {"id": "local-path@official", "repository": "/private/example/checkout"},
                ]
            }
        )
    )
    item = items[0]
    assert item == {
        "id": "plugin:documents@official",
        "name": "documents@official",
        "kind": "plugin",
        "status": "available",
        "enabled": False,
        "marketplace": "official",
        "version": "1.2.3",
        "developer": "Example Developer",
        "source": "https://example.test/plugin",
        "apps": ["Documents"],
        "skills": ["Draft documents"],
    }
    assert "secret" not in str(item)
    assert items[1]["source"] == "https://example.test/issues"
    assert all("source" not in item for item in items[2:7])
    assert items[7]["source"] == "https://example.test:8443/plugin"
    assert all("source" not in item for item in items[8:])
    public_json = json.dumps(items)
    for secret in (
        "EXAMPLE_TOKEN",
        "EXAMPLE_QUERY",
        "EXAMPLE_FRAGMENT",
        "/private/example/checkout",
        "/secret/local/path",
    ):
        assert secret not in public_json


@pytest.mark.parametrize(
    "source",
    [
        "http://127%2e0.0.1/private/example/checkout",
        "https://bad host.example/plugin",
        "https://bad\thost.example/plugin",
        "https://bad\nhost.example/plugin",
        "https://bad\x00host.example/plugin",
        "https://bad\\host.example/plugin",
        "https://bad_host.example/plugin",
        "https://-bad.example/plugin",
        "https://bad-.example/plugin",
        "https://bad..example/plugin",
        "https://example.test../plugin",
        "https://" + "x" * 64 + ".example/plugin",
        "https://" + ".".join(["x" * 63] * 4) + "/plugin",
        "http://[2606:4700:4700::1111%25eth0]/plugin",
        "https://example.test:/plugin",
        "https://example.test:65536/plugin",
        "https://example.test:+443/plugin",
        "https://example.test:１２/plugin",
        "https://[example.test]/plugin",
        "https://example.test]/plugin",
        "https://example.0x7f/plugin",
        "https://example.123/plugin",
    ],
)
def test_plugin_metadata_omits_malformed_source_authorities(source):
    from control.integration_catalog import _plugin_metadata

    assert "source" not in _plugin_metadata({"homepage": source})


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "https://USER:secret@Example.Test.:8443/plugin?q=secret#secret",
            "https://example.test:8443/plugin",
        ),
        ("http://8.8.8.8:8080/plugin", "http://8.8.8.8:8080/plugin"),
        ("https://[2606:4700:4700::1111]:443/plugin", "https://[2606:4700:4700::1111]:443/plugin"),
        ("https://xn--bcher-kva.example/plugin", "https://xn--bcher-kva.example/plugin"),
    ],
)
def test_plugin_metadata_preserves_valid_public_source_authorities(source, expected):
    from control.integration_catalog import _plugin_metadata

    assert _plugin_metadata({"homepage": source})["source"] == expected


def test_plugin_description_from_local_manifest_is_bounded(tmp_path):
    import json

    from control.integration_catalog import _plugins

    directory = tmp_path / ".codex-plugin"
    directory.mkdir()
    manifest = directory / "plugin.json"
    manifest.write_text(
        json.dumps(
            {"interface": {"longDescription": "Create documents."}, "secret": "never exposed"}
        )
    )
    payload = json.dumps(
        {"installed": [{"id": "documents", "source": {"source": "local", "path": str(tmp_path)}}]}
    )
    items = _plugins(payload)
    assert items[0]["description"] == "Create documents."
    assert "secret" not in str(items)
    manifest.write_text(" " * 65537)
    assert "description" not in _plugins(payload)[0]


def test_installed_marketplace_description_uses_exact_cache_version(tmp_path, monkeypatch):
    import json

    from control.integration_catalog import _plugins

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    directory = tmp_path / "plugins/cache/market/documents/1.2/.codex-plugin"
    directory.mkdir(parents=True)
    (directory / "plugin.json").write_text(json.dumps({"description": "Document tools"}))
    plugin = {"name": "documents", "marketplaceName": "market", "version": "1.2"}
    assert _plugins(json.dumps({"installed": [plugin]}))[0]["description"] == "Document tools"
    plugin["version"] = "../1.2"
    assert "description" not in _plugins(json.dumps({"installed": [plugin]}))[0]


# Help layouts: commander (Claude) and clap (Codex). Shapes copied from the real CLIs' --help.
COMMANDER_HELP = """Usage: claude plugin [options] [command]

Manage Claude Code plugins

Options:
  -h, --help                       display help for command

Commands:
  install|i [options] <plugin>     Install a plugin
                                     marketplace entries are resolved first
  marketplace                      Manage plugin marketplaces
  uninstall|rm [options] <plugin>  Uninstall a plugin

Learn more about plugins:
  ghost [options]                  Not a command
"""

CLAP_HELP = """Manage Codex plugins

Usage: codex plugin [OPTIONS] <COMMAND>

Commands:
  list         List installed and available plugins
  add          Install a plugin
  remove       Remove a plugin
  marketplace  Manage plugin marketplaces
  help         Print this message or the help of the given subcommand(s)

Options:
  -h, --help  Print help
"""


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Isolated homes for the fake CLIs: no real ~/.claude or ~/.codex is read."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    return tmp_path


def fake_cli(tmp_path, provider):
    """A private copy of a fake CLI, so a test may change its mtime without touching the fixture."""
    target = tmp_path / provider
    shutil.copy2(Path(__file__).parent / "fixtures" / f"fake-{provider}" / provider, target)
    return str(target)


def control(directory, name, text):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text)


def test_parse_subcommands_reads_commander_and_clap_layouts():
    from control.integration_catalog import parse_subcommands

    assert parse_subcommands(COMMANDER_HELP) == {"install", "i", "marketplace", "uninstall", "rm"}
    assert parse_subcommands(CLAP_HELP) == {"list", "add", "remove", "marketplace", "help"}


def test_parse_subcommands_skips_deeper_continuation_lines_in_descriptions():
    from control.integration_catalog import parse_subcommands

    help_text = "Commands:\n  install|i [options] <plugin>  Install a plugin\n" + (
        "                                 marketplace entries are resolved first\n"
    )
    assert parse_subcommands(help_text) == {"install", "i"}


def test_parse_subcommands_is_empty_for_garbage_headless_text_and_text_past_the_scan_limit():
    from control.integration_catalog import parse_subcommands

    assert parse_subcommands("") == frozenset()
    assert parse_subcommands("  install  Install a plugin\n") == frozenset()
    assert parse_subcommands("Commands:\n  !!! ***\n  1abc  x\n  -flag  y\n") == frozenset()
    assert parse_subcommands("Commands:\n" + "\n" * 70_000 + "  late  Entry\n") == frozenset()


def test_capabilities_read_both_help_pages_of_a_claude_cli(cli_env):
    from control.integration_catalog import capabilities

    binary = fake_cli(cli_env, "claude")
    assert run(capabilities("claude", binary)) == frozenset({"marketplace_add"})


def test_capabilities_read_the_clap_help_of_a_codex_cli(cli_env):
    from control.integration_catalog import capabilities

    binary = fake_cli(cli_env, "codex")
    assert run(capabilities("codex", binary)) == frozenset({"marketplace_add"})


def test_capabilities_omit_marketplace_add_when_plugin_help_lacks_the_verb(cli_env):
    from control.integration_catalog import capabilities

    binary = fake_cli(cli_env, "claude")
    control(cli_env / "claude-config", "fake-claude-help", "absent")
    assert run(capabilities("claude", binary)) == frozenset()


@pytest.mark.parametrize("mode", ["fail", "garbage", "hang"])
def test_capabilities_are_unknown_when_a_help_page_fails(cli_env, monkeypatch, mode):
    from control import integration_catalog
    from control.integration_catalog import capabilities

    monkeypatch.setattr(integration_catalog, "TIMEOUT_SECONDS", 0.5)
    binary = fake_cli(cli_env, "claude")
    control(cli_env / "claude-config", "fake-claude-help", mode)
    assert run(capabilities("claude", binary)) is None


def test_capabilities_are_unknown_for_a_missing_binary(cli_env):
    from control.integration_catalog import capabilities

    assert run(capabilities("claude", str(cli_env / "no-such-claude"))) is None


def test_failed_probe_is_not_memoized(cli_env):
    from control.integration_catalog import capabilities

    binary = fake_cli(cli_env, "claude")
    control(cli_env / "claude-config", "fake-claude-help", "fail")
    assert run(capabilities("claude", binary)) is None
    (cli_env / "claude-config" / "fake-claude-help").unlink()
    assert run(capabilities("claude", binary)) == frozenset({"marketplace_add"})


def test_memo_hit_runs_no_command(cli_env, monkeypatch):
    from control import integration_catalog
    from control.integration_catalog import capabilities

    commands = []
    original = integration_catalog._run

    async def counted(*args):
        commands.append(args)
        return await original(*args)

    monkeypatch.setattr(integration_catalog, "_run", counted)
    binary = fake_cli(cli_env, "claude")
    assert run(capabilities("claude", binary)) == frozenset({"marketplace_add"})
    assert len(commands) == 2
    assert run(capabilities("claude", binary)) == frozenset({"marketplace_add"})
    assert len(commands) == 2


def test_changed_binary_mtime_reprobes(cli_env):
    from control.integration_catalog import capabilities

    binary = fake_cli(cli_env, "claude")
    assert run(capabilities("claude", binary)) == frozenset({"marketplace_add"})
    control(cli_env / "claude-config", "fake-claude-help", "absent")
    status = os.stat(binary)
    os.utime(binary, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))
    assert run(capabilities("claude", binary)) == frozenset()


def test_catalog_actions_are_empty_when_the_binary_is_missing(cli_env):
    result = run(catalog("claude", str(cli_env / "no-such-claude"), fallback={"claude": []}))
    assert result["actions"] == []


def test_connector_add_is_absent_when_mcp_list_falls_back(cli_env):
    # The fake claude has no `mcp list` verb, so the read fails and the fallback is used.
    binary = fake_cli(cli_env, "claude")
    result = run(catalog("claude", binary, fallback={"claude": []}))
    assert result["warnings"]
    assert result["actions"] == ["marketplace_add"]
