"""Administrative contracts with temporary state and no host operations."""
import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient
from control.server import Manager, create_app, ADMIN_BODY_LIMIT
from control.local_models import save_profile, load_profiles

INVENTORY={'platform':'Linux','services':[],'binaries':{},'projects':[],
           'network':{'online':False,'hostname':None}}


@pytest.fixture
def manager(tmp_path,monkeypatch):
    value=Manager(tmp_path/'state')
    monkeypatch.setattr(value,'integrations',lambda:{})
    return value


@pytest.fixture
def admin(tmp_path,monkeypatch):
    monkeypatch.setattr('control.server.scan',AsyncMock(return_value=copy.deepcopy(INVENTORY)))
    monkeypatch.setattr(Manager,'integrations',lambda self:{})
    monkeypatch.setattr(Manager,'start',AsyncMock())
    monkeypatch.setattr(Manager,'stop',AsyncMock())
    app=create_app(tmp_path/'state')
    with TestClient(app,base_url='http://127.0.0.1:8094') as client:
        client.get('/')
        client.headers['X-Harness-Admin']='1'
        yield client,app.state.manager


def test_round01_failed_live_runtime_write_preserves_settings(manager,monkeypatch):
    manager.save(manager.settings)
    before=manager.path.read_bytes();memory=copy.deepcopy(manager.settings)
    runtime=manager.state/'runtime.json';runtime.write_text('{"revision":"old"}')
    monkeypatch.setattr(manager,'running',lambda:True)
    monkeypatch.setattr(manager,'build_runtime_config',AsyncMock(return_value={'revision':'new'}))
    def fail(config):raise OSError('synthetic write failure')
    monkeypatch.setattr(manager,'_write_runtime',fail)
    changed=copy.deepcopy(manager.settings);changed['maestro_instructions']='new instructions'
    with pytest.raises(OSError,match='synthetic'):
        asyncio.run(manager.apply_settings(changed))
    assert manager.path.read_bytes()==before
    assert manager.settings==memory
    assert json.loads(runtime.read_text())=={'revision':'old'}


def test_round02_failed_import_preserves_profiles(admin,tmp_path,monkeypatch):
    client,manager=admin
    binary=tmp_path/'llama-server';binary.write_text('synthetic')
    model=tmp_path/'fixture.gguf';model.write_text('synthetic')
    profile={'binary':str(binary),'model_file':str(model),'performance':{'threads':'2'}}
    save_profile(manager.state,profile)
    before={p.name:p.read_bytes() for p in manager.state.glob('local-profile*.json')}
    bundle=client.post('/api/settings-export',json={}).json()
    bundle['local_profiles'][str(model)]['performance']['threads']='4'
    bundle['local_profile']['performance']['threads']='4'
    bundle['settings']['port']=8195
    monkeypatch.setattr(manager,'running',lambda:True)
    result=client.post('/api/settings-import',json={'bundle':bundle,'apply':True})
    assert result.status_code==400
    assert {p.name:p.read_bytes() for p in manager.state.glob('local-profile*.json')}==before


@pytest.mark.parametrize('bind',[2130706433,True,1])
def test_round03_bind_requires_string(manager,bind):
    data=copy.deepcopy(manager.settings);data['vpn_bind']=bind
    with pytest.raises(ValueError):manager.validate(data)


@pytest.mark.parametrize('data',[[],None,True,{'services':[]},{'services':{'local':None}}, {'projects':[None]}, {'logins':[{}]}])
def test_round04_malformed_requests_never_persist(admin,data):
    client,manager=admin
    result=client.post('/api/settings',content=json.dumps(data),headers={'Content-Type':'application/json'})
    assert result.status_code==400
    assert not manager.path.exists()
    assert result.json()['error']


@pytest.mark.parametrize('headers,status', [({'Origin':'https://hostile.invalid'},403),
    ({'Sec-Fetch-Site':'cross-site'},403),({'Host':'hostile.invalid:8094'},403),
    ({'Tailscale-User-Login':'someone@example.invalid'},403),({'X-Harness-Admin':'0'},400)])
def test_round05_csrf_and_origin_rejection_preserve_state(admin,headers,status):
    client,manager=admin
    result=client.post('/api/settings',json=manager.settings,headers=headers)
    assert result.status_code==status
    assert not manager.path.exists()
    assert result.headers['cache-control']=='no-store'
    assert result.headers['x-content-type-options']=='nosniff'


def test_round06_settings_roundtrip_permissions_and_restart(admin,tmp_path):
    client,manager=admin
    root=tmp_path/'project';root.mkdir()
    data=copy.deepcopy(manager.settings)
    data.update(projects=[{'id':'fixture','label':'Revisão acessível','root':str(root),'permissions':{'read':True,'internet':False}}],maestro_instructions='Preserve Unicode α and drafts.')
    data['services']['local']['permissions']={'read':True,'write':False}
    assert client.post('/api/settings',json=data).status_code==200
    saved=client.get('/api/state').json()['settings']
    assert saved['projects'][0]['permissions']=={'read':True,'internet':False}
    assert saved['services']['local']['permissions']['read']
    assert not saved['services']['local']['permissions']['write']
    assert Manager(manager.state).settings==saved
    assert manager.path.stat().st_mode & 0o777==0o600
    assert not manager.path.with_suffix('.tmp').exists()


def test_round07_integrations_and_mcp_model_effort_are_validated(admin,monkeypatch):
    client,manager=admin
    data=copy.deepcopy(manager.settings)
    data['services']['codex'].update(enabled=True,models=['fixture'],integrations=['missing'])
    assert client.post('/api/settings',json=data).status_code==400
    monkeypatch.setattr(manager,'integrations',lambda:{'codex':[{'id':'present'}]})
    data['services']['codex']['integrations']=['present']
    data['mcp_defaults']={'backend':'codex','model':'fixture','effort':'ultra'}
    manager.provider_models={'codex':{'fixture':['low']}}
    assert client.post('/api/settings',json=data).status_code==400
    data['mcp_defaults']['effort']='low'
    assert client.post('/api/settings',json=data).status_code==200
    assert manager.settings['services']['codex']['integrations']==['present']


def test_round08_busy_stop_preserves_tasks(manager):
    runs=manager.state/'runs';runs.mkdir()
    with sqlite3.connect(runs/'jobs.sqlite3') as db:
        db.execute('CREATE TABLE jobs(state TEXT)');db.execute("INSERT INTO jobs VALUES('running')")
    with pytest.raises(ValueError,match='tarefas'):asyncio.run(manager.stop())


def test_round08_admin_lock_recovers(admin):
    client,http_manager=admin
    asyncio.run(http_manager.lock.acquire())
    try:
        response=client.post('/api/settings',json=http_manager.settings)
        assert response.status_code==429 and response.headers['retry-after']=='1'
    finally:http_manager.lock.release()
    assert client.post('/api/settings',json=http_manager.settings).status_code==200


def test_round09_body_and_operation_limits(admin):
    client,manager=admin
    result=client.post('/api/settings',content=b'x'*(ADMIN_BODY_LIMIT+1))
    assert result.status_code==413 and not manager.path.exists()
    manager.operations.jobs={str(n):{'state':'running'} for n in range(4)}
    response=client.post('/api/integration',json={'provider':'codex','action':'login','name':'fixture'})
    assert response.status_code==429 and response.headers['retry-after']=='5'
    manager.operations.jobs={}
    assert client.post('/api/settings',json=manager.settings).status_code==200


@pytest.mark.parametrize('failure',[OSError,asyncio.CancelledError])
def test_round10_cross_retest_transaction_restores_runtime_after_write(manager,monkeypatch,failure):
    manager.save(manager.settings)
    runtime=manager.state/'runtime.json';runtime.write_text('{"revision":"old"}')
    previous={p.name:p.read_bytes() for p in (manager.path,runtime)}
    settings=copy.deepcopy(manager.settings)
    monkeypatch.setattr(manager,'running',lambda:True)
    monkeypatch.setattr(manager,'build_runtime_config',AsyncMock(return_value={'revision':'new'}))
    def partial(config):
        runtime.write_text(json.dumps(config))
        raise failure('synthetic')
    monkeypatch.setattr(manager,'_write_runtime',partial)
    changed=copy.deepcopy(settings);changed['maestro_instructions']='changed'
    with pytest.raises(failure):asyncio.run(manager.apply_settings(changed))
    assert manager.settings==settings
    assert {p.name:p.read_bytes() for p in (manager.path,runtime)}==previous


def test_round10_failed_start_restores_settings_and_lifecycle(manager,monkeypatch):
    from types import SimpleNamespace
    manager.save(manager.settings)
    previous=manager.path.read_bytes();settings=copy.deepcopy(manager.settings)
    old_proc=SimpleNamespace(returncode=1)
    manager.proc=old_proc;manager.applied=123;manager.startup_error='prior state'
    created=SimpleNamespace(returncode=None)
    async def fail_start():
        manager.proc=created;manager.applied=456;manager.startup_error=None
        (manager.state/'autostart').touch()
        raise OSError('synthetic readiness failure')
    async def stop(force=False):
        assert force and manager.proc is created
        created.returncode=-15
    stop_mock=AsyncMock(side_effect=stop)
    monkeypatch.setattr(manager,'start',fail_start)
    monkeypatch.setattr(manager,'stop',stop_mock)
    data=copy.deepcopy(settings);data['services']['codex'].update(enabled=True,models=['fixture'])
    with pytest.raises(OSError,match='readiness'):asyncio.run(manager.apply_settings(data))
    assert manager.path.read_bytes()==previous and manager.settings==settings
    stop_mock.assert_awaited_once_with(force=True)
    assert manager.proc is old_proc and manager.applied==123 and manager.startup_error=='prior state'
    assert not (manager.state/'autostart').exists()


def test_round10_concurrent_mutations_share_admin_lock(tmp_path,monkeypatch):
    import httpx
    app=create_app(tmp_path/'state')
    manager=app.state.manager
    async def scenario():
        entered=asyncio.Event();release=asyncio.Event()
        async def slow_apply(data):entered.set();await release.wait()
        monkeypatch.setattr(manager,'apply_settings',slow_apply)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8094',
            headers={'X-Harness-Admin':'1','Cookie':'admin='+manager.cookie}) as client:
            pending=asyncio.create_task(client.post('/api/settings',json={}))
            try:
                await asyncio.wait_for(entered.wait(),1)
                for route in ('settings','settings-import','local-profile'):
                    result=await client.post('/api/'+route,json={})
                    assert result.status_code==429
                assert not manager.path.exists()
            finally:release.set()
            assert (await pending).status_code==200
    asyncio.run(scenario())


def test_round10_import_preview_never_starts_but_apply_enabled_services_does(admin):
    client,manager=admin
    bundle=client.post('/api/settings-export',json={}).json()
    bundle['settings']['services']['codex'].update(enabled=True,models=['fixture'])
    assert client.post('/api/settings-import',json={'bundle':bundle}).json()['applied'] is False
    manager.start.assert_not_awaited()
    result=client.post('/api/settings-import',json={'bundle':bundle,'apply':True})
    assert result.status_code==200 and result.json()['applied']
    manager.start.assert_awaited_once()


@pytest.mark.parametrize('route',['local-profile','local-import'])
def test_round10_failed_profile_update_restores_permissions(admin,tmp_path,monkeypatch,route):
    client,manager=admin
    binary=tmp_path/'llama-server';binary.write_text('synthetic')
    model=tmp_path/'fixture.gguf';model.write_text('synthetic')
    profile={'binary':str(binary),'model_file':str(model),'performance':{'threads':'2'},'permissions':{'read':True,'write':False}}
    save_profile(manager.state,profile)
    previous=load_profiles(manager.state)
    candidate=copy.deepcopy(profile);candidate['performance']['threads']='4';candidate['permissions']['write']=True
    monkeypatch.setattr(manager,'running',lambda:True)
    monkeypatch.setattr(manager,'apply_settings',AsyncMock(side_effect=ValueError('synthetic runtime rejection')))
    monkeypatch.setattr('control.local_models.processes',lambda:[candidate])
    response=client.post('/api/'+route,json=candidate if route=='local-profile' else {'file':str(model)})
    assert response.status_code==400
    assert load_profiles(manager.state)==previous
