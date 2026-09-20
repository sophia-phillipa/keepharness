import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,AsyncMock
from starlette.testclient import TestClient
from control.server import create_app
from control.local_models import performance,save_profile,load_profile,launch_options,validate_profile

INVENTORY={'platform':'Linux','services':[],'binaries':{},'projects':[],'network':{'online':False,'hostname':None}}
class ConfigurationTest(unittest.TestCase):
 def test_performance_roundtrip_does_not_copy_credentials(self):
  args=['llama-server','--n-gpu-layers','44','--n-cpu-moe','3','--cpu-range','2-5','--cpu-range-batch','0-7','--threads','4','--api-key','secret','--api-key-file','/private/key']
  values=performance(args)
  with tempfile.TemporaryDirectory() as d:
   save_profile(d,{'binary':'/runtime/llama-server','model_file':'/models/test.gguf','performance':values,'key_file':'/private/key'})
   raw=Path(d,'local-profile.json').read_text();self.assertNotIn('secret',raw);self.assertNotIn('/private',raw)
   self.assertEqual(Path(d,'local-profile.json').stat().st_mode&0o777,0o600)
   options=launch_options(load_profile(d));self.assertIn('2-5',options);self.assertIn('0-7',options);self.assertEqual(options[options.index('--n-cpu-moe')+1],'3')
 def test_profile_rejects_unknown_flags_and_missing_files(self):
  with tempfile.TemporaryDirectory() as d:
   binary=Path(d,'llama-server');binary.touch();model=Path(d,'test.gguf');model.touch()
   profile={'binary':str(binary),'model_file':str(model),'performance':{'api-key':'x'}}
   with self.assertRaises(ValueError):validate_profile(profile)
   profile['performance']={'threads':'--model'}
   with self.assertRaises(ValueError):validate_profile(profile)
   profile['performance']={'threads':'4'};self.assertEqual(validate_profile(profile),profile)
   model.unlink()
   with self.assertRaises(ValueError):validate_profile(profile)
 def test_export_preview_apply_and_failed_import(self):
  with tempfile.TemporaryDirectory() as d,patch('control.server.scan',AsyncMock(return_value=INVENTORY)):
   Path(d,'vpn.key').write_text('never-export-this')
   with TestClient(create_app(d),base_url='http://127.0.0.1:8094') as c:
    c.get('/');headers={'X-Harness-Admin':'1'}
    bundle=c.post('/api/settings-export',json={},headers=headers).json();self.assertNotIn('never-export-this',json.dumps(bundle))
    bundle['settings']['port']=8195
    preview=c.post('/api/settings-import',json={'bundle':bundle},headers=headers);self.assertEqual(preview.status_code,200);self.assertFalse(preview.json()['applied']);self.assertFalse(Path(d,'settings.json').exists())
    applied=c.post('/api/settings-import',json={'bundle':bundle,'apply':True},headers=headers);self.assertEqual(applied.status_code,200);self.assertEqual(json.loads(Path(d,'settings.json').read_text())['port'],8195)
    self.assertFalse(c.get('/api/state').json()['status']['running'])
    before=Path(d,'settings.json').read_bytes();bad=copy.deepcopy(bundle);bad['settings']['vpn_bind']='0.0.0.0'
    self.assertEqual(c.post('/api/settings-import',json={'bundle':bad,'apply':True},headers=headers).status_code,400);self.assertEqual(Path(d,'settings.json').read_bytes(),before)
    self.assertEqual(c.post('/api/settings-import',json={'bundle':{'version':99}},headers=headers).status_code,400)
 def test_import_malformed_and_live_reload_while_running(self):
  from control.server import Manager
  with tempfile.TemporaryDirectory() as d,patch('control.server.scan',AsyncMock(return_value=INVENTORY)):
   with TestClient(create_app(d),base_url='http://127.0.0.1:8094') as c:
    c.get('/');h={'X-Harness-Admin':'1'}
    self.assertEqual(c.post('/api/settings-import',json=[],headers=h).status_code,400)
    bundle=c.post('/api/settings-export',json={},headers=h).json()
    with patch.object(Manager,'running',return_value=True):
     self.assertEqual(c.post('/api/settings-import',json={'bundle':bundle,'apply':True},headers=h).status_code,200)
     self.assertTrue(Path(d,'settings.json').exists())
     before=Path(d,'settings.json').read_bytes()
     bundle['settings']['port']=8195
     self.assertEqual(c.post('/api/settings-import',json={'bundle':bundle,'apply':True},headers=h).status_code,400)
     self.assertEqual(Path(d,'settings.json').read_bytes(),before)
