"""Reader injection uses the CLI's resolved layers, never a second trust resolver."""

import asyncio
import json
import os
import shutil
import tomllib
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from adapters.codex.backend import run_native
from adapters.codex.rpc import RPCError


def reader_file(folder, *, enabled, command=None):
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / 'config.toml'
    file.write_text('[mcp_servers.harness_reader]\nenabled=' + str(enabled).lower() + '\n'
                    + ('command=' + json.dumps(command) + '\n' if command else ''))
    return file


def run_reader(tmp_path, monkeypatch, cwd, config_response):
    calls = []

    class RPC:
        async def call(self, method, params):
            calls.append((method, params))
            if method == 'config/read':
                assert params == {'cwd': str(cwd), 'includeLayers': False}
                if isinstance(config_response, Exception):
                    raise config_response
                return config_response
            return {'thread': {'id': 'fixture'}}

        async def send(self, method, params):
            calls.append((method, params))

        async def receive(self):
            return {'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}}

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield RPC()

    monkeypatch.setattr('adapters.codex.native.connection', connection)
    monkeypatch.setattr('adapters.codex.backend.version_notice', lambda *args: None)
    asyncio.run(run_native(
        {'binary': 'fixture-never-executed'}, 'fixture', lambda *args: None,
        {'root': str(cwd), 'permissions': {'read': True, 'shell': False}, 'access_mode': 'read_only'},
        'fixture', 'configured', tmp_path / 'session', lambda *args: None,
    ))
    assert [method for method, _ in calls].index('config/read') < [method for method, _ in calls].index('thread/start')
    return next(params for method, params in calls if method == 'thread/start')


@pytest.mark.parametrize('source', ['fixture', 'installed_cli'])
@pytest.mark.parametrize('scenario', ['inherited', 'exact_cwd', 'outside_project', 'untrusted', 'reenabled', 'user_then_project'])
def test_native_reader_uses_effective_cli_configuration(tmp_path, monkeypatch, scenario, source):
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    (repo / '.git/HEAD').write_text('ref: refs/heads/main\n')
    cwd = repo if scenario == 'exact_cwd' else repo / 'sub'
    cwd.mkdir(exist_ok=True)
    user = Path(os.environ['CODEX_HOME']) / 'config.toml'
    user.write_text('[projects.' + json.dumps(str(repo)) + ']\ntrust_level=' + json.dumps('untrusted' if scenario == 'untrusted' else 'trusted') + '\n')
    parent = reader_file(repo / '.codex', enabled=False, command='owner-reader')
    # These are the active layers in Codex's config/read response. An outside or
    # untrusted layer does not participate; a deeper trusted table overrides keys.
    active_layers = [user, parent]
    if scenario == 'outside_project':
        parent.unlink()
        reader_file(tmp_path / '.codex', enabled=False, command='outside-reader')
        active_layers = [user]
    elif scenario == 'untrusted':
        active_layers = [user]
    elif scenario == 'reenabled':
        active_layers.append(reader_file(cwd / '.codex', enabled=True))
    elif scenario == 'user_then_project':
        user.write_text(user.read_text() + '[mcp_servers.harness_reader]\nenabled=false\ncommand="owner-reader"\n')
        parent.write_text('[mcp_servers.harness_reader]\nenabled=true\n')
    effective = {}
    for layer in active_layers:
        for name, values in tomllib.loads(layer.read_text()).get('mcp_servers', {}).items():
            effective.setdefault(name, {}).update(values)
    response = {'config': {'mcp_servers': effective}}
    if source == 'installed_cli':
        # Metadata only: no thread/start or turn/start reaches the installed CLI.
        from adapters.codex.state import _ask

        binary = shutil.which('codex')
        if binary is None:
            pytest.skip('Codex binary is not installed')
        monkeypatch.chdir(cwd)
        environment = {name: os.environ[name] for name in ('HOME', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR')}
        environment.update(PATH=os.defpath, TMPDIR=str(tmp_path))
        results, failures = asyncio.run(_ask(binary, [('config', 'config/read', {'cwd': str(cwd), 'includeLayers': True})], environment=environment))
        assert failures == {}
        response = results['config']
        configured = response['config'].get('mcp_servers', {})
        assert configured.keys() == effective.keys()
        for name, expected in effective.items():
            assert {key: configured[name][key] for key in expected} == expected
    params = run_reader(tmp_path, monkeypatch, cwd, response)
    overrides = params['config']['mcp_servers']
    if scenario in ('outside_project', 'untrusted'):
        assert overrides['harness_reader']['enabled'] is True
        assert overrides['harness_reader']['command'] != 'outside-reader'
    else:
        assert 'harness_reader' not in overrides
        assert effective['harness_reader']['enabled'] is (scenario in ('reenabled', 'user_then_project'))
        assert effective['harness_reader']['command'] == 'owner-reader'


@pytest.mark.parametrize('response', [{}, {'config': None}, {'config': {'mcp_servers': []}}, RPCError({'code': -32601, 'message': 'Unavailable'})])
def test_native_reader_does_not_inject_when_effective_configuration_is_unknown(tmp_path, monkeypatch, response):
    params = run_reader(tmp_path, monkeypatch, tmp_path, response)
    assert 'harness_reader' not in params['config']['mcp_servers']
