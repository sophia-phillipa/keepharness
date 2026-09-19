"""Exercise real filesystem isolation with harmless Python, never an AI CLI."""
import json
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from agent_service.local_sandbox import wrap
from agent_service.tools import ToolError


@pytest.mark.parametrize('writable',[False,True])
def test_authorized_root_outside_symlink_credentials_and_write_boundary(tmp_path,writable):
    if not shutil.which('bwrap'):pytest.skip('bubblewrap is not installed')
    root=tmp_path/'project';root.mkdir();(root/'inside.txt').write_text('allowed')
    outside=tmp_path/'outside-secret.txt';outside.write_text('must remain outside')
    (root/'escape.txt').symlink_to(outside)
    session=tmp_path/'session';session.mkdir()
    script='''import os,json,pathlib
root=pathlib.Path(%r)
def readable(path):
 try:pathlib.Path(path).read_text();return True
 except OSError:return False
try:(root/'written.txt').write_text('fixture');write=True
except OSError:write=False
print(json.dumps({'inside':readable(root/'inside.txt'),'outside':readable(%r),'symlink':readable(root/'escape.txt'),'write':write,'home':os.environ['HOME'],'foreign_env':os.environ.get('UNRELATED_SECRET'),'local_key':os.environ.get('TAIL_HARNESS_LOCAL_KEY')}))
'''%(str(root),str(outside))
    command=wrap(['/usr/bin/python3','-c',script],session,root,
                 {'root':str(root),'permissions':{'read':True,'write':writable}},
                 {'TAIL_HARNESS_LOCAL_KEY':'fixture-local','UNRELATED_SECRET':'must-not-pass'})
    result=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    value=json.loads(result.stdout)
    assert value['inside'] and not value['outside'] and not value['symlink']
    assert value['write'] is writable
    assert value['home']==str(session/'agent-home')
    assert value['foreign_env'] is None and value['local_key']=='fixture-local'


def test_missing_bwrap_is_fail_closed(tmp_path):
    with patch('agent_service.local_sandbox.shutil.which',return_value=None):
        with pytest.raises(ToolError,match='local_filesystem_isolation_unavailable'):
            wrap(['/usr/bin/python3'],tmp_path,tmp_path,{})
