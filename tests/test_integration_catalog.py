import asyncio
import sys
from unittest.mock import AsyncMock, patch

from control.integration_catalog import catalog


def test_slow_cli_initialization_can_finish_before_browser_deadline():
    from control.integration_catalog import _run

    # A real installed CLI took 10 s on its first query; the old 8 s budget lost it.
    code, output = run(_run(sys.executable, '-c',
                            'import time; time.sleep(9); print("catalog-ready")'))
    assert code == 0
    assert output.strip() == 'catalog-ready'


def run(value):
    return asyncio.run(value)


def test_codex_catalog_combines_configured_mcp_and_available_plugins_without_secrets():
    calls = []

    async def fake_run(*args):
        calls.append(args)
        if args[1:] == ("mcp", "list", "--json"):
            return 0, '[{"name":"drive","enabled":true,"transport":{"type":"stdio","command":"secret-command","env":{"TOKEN":"secret"}},"auth_status":"authenticated"}]'
        return 0, '{"installed":[{"pluginId":"github@openai","name":"GitHub","marketplaceName":"openai","version":"1","installed":true,"enabled":true}],"available":[{"pluginId":"linear@openai","name":"Linear","marketplaceName":"openai","version":"2","installed":false,"enabled":false}]}'

    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("codex", "/bin/codex"))

    assert calls == [
        ("/bin/codex", "mcp", "list", "--json"),
        ("/bin/codex", "plugin", "list", "--available", "--json"),
    ]
    assert result["warnings"] == []
    assert result["items"] == [
        {"id": "mcp:drive", "name": "drive", "kind": "mcp", "status": "configured", "enabled": True},
        {"id": "plugin:github@openai", "name": "GitHub", "kind": "plugin", "status": "installed", "enabled": True},
        {"id": "plugin:linear@openai", "name": "Linear", "kind": "plugin", "status": "available", "enabled": False},
    ]
    assert "secret" not in str(result)


def test_claude_uses_available_plugins_and_only_parses_mcp_names():
    async def fake_run(*args):
        if args[1:] == ("plugin", "list", "--available", "--json"):
            return 0, '{"installed":[],"available":[{"name":"Calendar","marketplaceName":"anthropic","installed":false,"enabled":false}]}'
        return 0, "drive: https://drive.example/mcp\nlocal-tools: npx local-tools\n"

    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("claude", "/bin/claude"))

    assert result == {
        "items": [
            {"id": "mcp:drive", "name": "drive", "kind": "mcp", "status": "configured"},
            {"id": "mcp:local-tools", "name": "local-tools", "kind": "mcp", "status": "configured"},
            {"id": "plugin:Calendar@anthropic", "name": "Calendar", "kind": "plugin", "status": "available", "enabled": False},
        ],
        "warnings": [],
    }


def test_catalog_falls_back_to_known_mcp_metadata_when_claude_output_is_unknown():
    async def fake_run(*_args):
        return 1, "not a list"

    fallback = {"claude": [{"id": "mcp:drive", "name": "Drive", "kind": "mcp", "status": "configured"}]}
    with patch("control.integration_catalog._run", side_effect=fake_run):
        result = run(catalog("claude", "/bin/claude", fallback=fallback))

    assert result["items"] == fallback["claude"]
    assert result["warnings"]


def test_catalog_rejects_unknown_provider_without_executing_a_cli():
    with patch("control.integration_catalog._run", new_callable=AsyncMock) as command:
        result = run(catalog("deepseek", "/bin/codex"))
    assert result == {"items": [], "warnings": ["Catálogo não suportado para este provedor."]}
    command.assert_not_awaited()


def test_claude_empty_mcp_list_is_not_a_query_failure():
    async def fake_run(*args):
        return (0, 'No MCP servers configured. Use `claude mcp add` to add a server.') if args[-2:] == ('mcp', 'list') else (0, '{"installed":[],"available":[]}')
    with patch('control.integration_catalog._run', side_effect=fake_run):
        assert run(catalog('claude','/bin/claude')) == {'items':[], 'warnings':[]}


def test_plugin_descriptions_are_preserved_as_text_for_both_statuses():
    from control.integration_catalog import _plugins
    import json

    items = _plugins(json.dumps({
        "installed": [{"id": "installed", "description": "  Explains installed tools.  "}],
        "available": [{"id": "available", "description": "<b>Plain text description</b>"},
                      {"id": "invalid", "description": {"token": "not metadata"}}],
    }))
    assert items[0]["description"] == "Explains installed tools."
    assert items[1]["description"] == "<b>Plain text description</b>"
    assert "description" not in items[2]


def test_plugin_description_from_local_manifest_is_bounded(tmp_path):
    import json
    from control.integration_catalog import _plugins
    directory = tmp_path / ".codex-plugin"
    directory.mkdir()
    manifest = directory / "plugin.json"
    manifest.write_text(json.dumps({"interface": {"longDescription": "Create documents."}, "secret": "never exposed"}))
    payload = json.dumps({"installed": [{"id": "documents", "source": {"source": "local", "path": str(tmp_path)}}]})
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
