import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from starlette.testclient import TestClient
from control.server import Manager,create_app
from control.operations import operation
from agent_service.app import Service,APIError

INVENTORY={'platform':'Linux','services':[{'id':'codex','name':'Codex','found':True,'credential_present':True},{'id':'claude','name':'Claude','found':False},{'id':'local','name':'Local','found':False}],'binaries':{},'projects':[],'network':{'online':False,'hostname':None}}
class ControlTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.manager=Manager(self.tmp.name);self.manager.admin_port=8094
 def tearDown(self):self.tmp.cleanup()
 def test_default_and_permissions(self):
  self.assertFalse(any(s['enabled'] for s in self.manager.settings['services'].values()))
  settings=copy.deepcopy(self.manager.settings);settings['services']['local']['permissions']['write']=True
  with self.assertRaisesRegex(ValueError,'leitura'):self.manager.validate(settings)
  settings=copy.deepcopy(self.manager.settings);settings['projects']=[{'id':'home','root':str(Path.home())}]
  with self.assertRaisesRegex(ValueError,'ampla'):self.manager.validate(settings)
  settings=copy.deepcopy(self.manager.settings);settings['vpn_bind']='0.0.0.0'
  with self.assertRaisesRegex(ValueError,'privado'):self.manager.validate(settings)
  settings['vpn_bind']='10.44.0.2';self.assertEqual(self.manager.validate(settings)['vpn_bind'],'10.44.0.2')
 def test_auth_csrf_and_save(self):
  with patch('control.server.scan',AsyncMock(return_value=INVENTORY)):
   with TestClient(create_app(self.tmp.name),base_url='http://127.0.0.1:8094') as client:
    self.assertEqual(client.get('/api/state').status_code,401)
    self.assertEqual(client.get('/',headers={'Host':'evil.example:8094'}).status_code,403)
    self.assertEqual(client.get('/',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
    self.assertEqual(client.get('/').status_code,200)
    self.assertEqual(client.get('/api/state').status_code,200)
    settings=self.manager.settings
    self.assertEqual(client.post('/api/settings',json=settings).status_code,400)
    self.assertEqual(client.post('/api/settings',json=settings,headers={'X-Harness-Admin':'1','Origin':'https://evil.example'}).status_code,403)
    self.assertEqual(client.post('/api/settings',json=settings,headers={'X-Harness-Admin':'1'}).status_code,200)
    self.assertEqual((Path(self.tmp.name)/'settings.json').stat().st_mode&0o777,0o600)
 def test_cross_site_admin_link_navigation(self):
  navigation={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document','Sec-Fetch-User':'?1'}
  with patch('control.server.scan',AsyncMock(return_value=INVENTORY)):
   with TestClient(create_app(self.tmp.name),base_url='http://127.0.0.1:8094') as client:
    response=client.get('/',headers=navigation)
    self.assertEqual(response.status_code,200)
    self.assertIn('admin',response.cookies)
    self.assertEqual(client.get('/api/state').status_code,200)
    for headers in (
     {**navigation,'Sec-Fetch-Mode':'cors'},
     {**navigation,'Sec-Fetch-Dest':'iframe'},
     {k:v for k,v in navigation.items() if k!='Sec-Fetch-User'},
     {**navigation,'Origin':'https://evil.example'},
     {**navigation,'Host':'evil.example:8094'},
     {**navigation,'Tailscale-User-Login':'remote@example.com'},
    ):
     with self.subTest(headers=headers):
      self.assertEqual(client.get('/',headers=headers).status_code,403)
    for path in ('/api/state','/admin.js','/admin.css','/assets/icons.svg'):
     with self.subTest(path=path):
      self.assertEqual(client.get(path,headers=navigation).status_code,403)
    self.assertEqual(client.post('/api/settings',json=self.manager.settings,headers={**navigation,'X-Harness-Admin':'1'}).status_code,403)
 def test_connector_arguments_no_shell(self):
  self.assertEqual(operation('codex','codex',{'action':'connector_add','name':'drive','url':'https://example.com/mcp'}),['codex','mcp','add','drive','--url','https://example.com/mcp'])
  for name in ('--help','bad;touch /tmp/x'):
   with self.assertRaises(ValueError):operation('claude','claude',{'action':'login','name':name})
  with self.assertRaises(ValueError):operation('codex','codex',{'action':'connector_add','name':'mail','url':'https://example.com/mcp?token=secret'})
 def test_enforced_provider_scope(self):
  cfg={'state_dir':self.tmp.name,'projects':{'a':{},'b':{}},'clients':{'u':{'projects':['a','b']}},'services':{'codex':{'enabled':True,'models':['m'],'projects':['a'],'permissions':{'upload':False}}},'codex_models':{'m':['low']},'codex':{}}
  s=Service(cfg);identity=('u',cfg['clients']['u'])
  self.assertEqual(s.assess(identity,{'project_id':'a','backend':'codex','model':'m','effort':'low','prompt':'x'})['decision'],'accept')
  for changes,error in [({'project_id':'b'},'service_project_denied'),({'model':'other'},'model_denied'),({'file_ids':['x']},'uploads_denied')]:
   with self.assertRaisesRegex(APIError,error):s.assess(identity,{'project_id':'a','backend':'codex','model':'m','effort':'low','prompt':'x',**changes})
  s.db.close()

if __name__=='__main__':unittest.main()

class OwnershipTest(unittest.TestCase):
 def test_job_owner_isolation(self):
  with tempfile.TemporaryDirectory() as folder:
   cfg={'state_dir':folder,'projects':{'p':{}},'clients':{},'services':{'local':{'enabled':True,'models':['m'],'projects':['p'],'permissions':{}}}}
   service=Service(cfg);alice=('alice',{'projects':['p']});bob=('bob',{'projects':['p']})
   job=service.submit(alice,{'project_id':'p','backend':'local','model':'m','prompt':'test'})
   with self.assertRaisesRegex(APIError,'job_owner_denied'):service.job(bob,job['job_id'])
   self.assertEqual(service.job(alice,job['job_id'])['owner'],'alice');service.db.close()
