"""Project access is additive; leaving the project removes only its additions."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from agent_service import maestro
from agent_service.app import Service, create_app
from control.server import Manager
from control.local_models import validate_profile,save_profile,runtime_roots
from test_model_permissions import model_config
from test_local_profiles import profiles


def grants_config(tmp_path):
    cfg=model_config(tmp_path)
    cfg['uploads_enabled']=False
    cfg['projects']['p']['permissions']={'read':True,'internet':True,'upload':True,'shell':True}
    cfg['projects']['sem-projeto']={}
    for client in cfg['clients'].values():client['projects'].append('sem-projeto')
    cfg['services']['local']['projects'].append('sem-projeto')
    for model in ('qwen','gemma'):cfg['services']['local']['model_permissions'][model]={'read':True,'upload':False,'internet':False}
    cfg['local']['model_roots']={'qwen':[str(tmp_path/'model-folder')],'gemma':[]}
    (tmp_path/'model-folder').mkdir()
    return cfg


def test_permissions_are_union_false_cannot_revoke_and_without_project_inherits(tmp_path):
    cfg=grants_config(tmp_path)
    cfg['services']['local']['model_permissions']['qwen']['write']=True
    cfg['projects']['p']['permissions']['write']=False
    inside=maestro.model_permissions(cfg,'local','qwen','p')
    outside=maestro.model_permissions(cfg,'local','qwen','sem-projeto')
    assert inside['write'] and inside['read'] and inside['internet'] and inside['upload']
    assert outside['write'] and outside['read'] and not outside['internet'] and not outside['upload']
    assert {m['model'] for m in maestro.candidates(cfg,'p',uploads=True)}=={'qwen','gemma'}
    assert not maestro.candidates(cfg,'sem-projeto',uploads=True)


def test_api_model_permissions_project_auth_and_upload_grant(tmp_path):
    app=create_app(grants_config(tmp_path));client=TestClient(app,headers={'Authorization':'Bearer a'})
    try:
        inside=client.get('/v1/models?project_id=p').json()
        outside=client.get('/v1/models?project_id=sem-projeto').json()
        assert inside['uploads_enabled'] and not outside['uploads_enabled']
        assert all(m['permissions']['internet'] for m in inside['models'])
        assert all(not m['permissions']['internet'] for m in outside['models'])
        assert client.get('/v1/models?project_id=not-authorized').status_code==403
        assert client.post('/v1/files?project_id=p',headers={'X-Filename':'file.txt'},content=b'fixture').status_code==201
        assert client.post('/v1/files?project_id=sem-projeto',headers={'X-Filename':'file.txt'},content=b'fixture').status_code==403
    finally:client.close();app.state.service.db.close()


def test_execution_roots_project_plus_model_and_model_only_outside(tmp_path):
    cfg=grants_config(tmp_path);service=Service(cfg);identity=('a',cfg['clients']['a'])
    try:
        for project in ('p','sem-projeto'):
            data={'project_id':project,'backend':'local','model':'qwen','effort':'configured','prompt':'fixture'}
            jid=service.submit(identity,data)['job_id']
            with patch('agent_service.app.adapters.run_native',AsyncMock(return_value={'answer':'fixture'})) as run:
                asyncio.run(service.infer(service.job(identity,jid),data))
                chosen=run.call_args.args[3]
                roots=[chosen['root'],*chosen.get('additional_roots',[])]
                assert str(tmp_path/'model-folder') in roots
                assert (str(tmp_path) in roots) is (project=='p')
                assert chosen['permissions']['internet'] is (project=='p')
    finally:service.db.close()


def test_settings_preserve_project_grants_and_validate_boolean_values(tmp_path):
    manager=Manager(tmp_path/'state');root=tmp_path/'project';root.mkdir()
    settings={**manager.settings,'projects':[{'id':'p','root':str(root),'permissions':{'internet':True,'read':False}}]}
    assert manager.validate(settings)['projects'][0]['permissions']=={'internet':True,'read':False}
    settings['projects'][0]['permissions']['upload']='true'
    with pytest.raises(ValueError):manager.validate(settings)


def test_model_allowed_roots_persist_and_reject_broad_or_credential_paths(tmp_path):
    qwen,_=profiles(tmp_path);root=tmp_path/'authorized';root.mkdir()
    profile=validate_profile({**qwen,'allowed_roots':[str(root),str(root)]})
    save_profile(tmp_path,profile)
    assert runtime_roots(tmp_path,[{'id':'q','model_file':qwen['model_file']}],['q'])=={'q':[str(root)]}
    for forbidden in ('/',str(__import__('pathlib').Path.home())):
        with pytest.raises(ValueError):validate_profile({**qwen,'allowed_roots':[forbidden]})


def test_models_advertise_local_admin_link_on_vpn_hostname(tmp_path):
    cfg=grants_config(tmp_path)
    cfg['admin_url']='http://127.0.0.1:8094/'
    app=create_app(cfg)
    client=TestClient(app,base_url='http://harness.test:8093',headers={'Authorization':'Bearer a'})
    try:
        assert client.get('/v1/models').json()['admin_url']==cfg['admin_url']
    finally:
        client.close();app.state.service.db.close()
