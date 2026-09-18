"""Project tools available only inside the Codex disposable workspace."""
import difflib
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import shutil
from mcp.server.fastmcp import FastMCP
from tools import safe_file, sandbox, process, ToolError

mcp=FastMCP('selected-project')
ROOT=Path('/work')
CONFIG=json.loads(Path('/bridge/project.json').read_text())

def available():
    names={}
    for label in CONFIG['roots']:
        root=Path('/sources')/label
        raw=subprocess.check_output(['git','-c','safe.directory=*','-C',str(root),'ls-files','-z']) if (root/'.git').exists() else None
        files=raw.decode().split('\0') if raw is not None else [str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()]
        for name in files:
            if not name or any(p.startswith('.') for p in Path(name).parts) or Path(name).suffix.lower() in ('.key','.pem','.gguf','.sqlite3'):continue
            names[label+'/'+name]=root/name
    for p in ROOT.rglob('*'):
        if p.is_file() and not p.is_symlink():names[str(p.relative_to(ROOT))]=p
    return names

def source(path):
    p=available().get(path)
    if p is None:raise ValueError('path not authorized')
    root=ROOT if p.is_relative_to(ROOT) else Path('/sources')/Path(path).parts[0]
    return safe_file(root,str(p.relative_to(root)))

@mcp.tool()
def list_files() -> list[str]:
    """List files in the selected project snapshot, including explicitly authorized extra directories."""
    return list(available())[:10000]

@mcp.tool()
def read_file(path:str,start:int=1,end:int=200) -> dict:
    """Read a relative file with line references; maximum 500 lines."""
    if start<1 or end<start or end-start>500:raise ValueError('invalid range')
    text=source(path).read_text()
    return {'path':path,'lines':[{'line':n,'text':line} for n,line in enumerate(text.splitlines(),1) if start<=n<=end]}

@mcp.tool()
def search(query:str) -> list[dict]:
    """Search text inside authorized project files. Maximum 100 matches."""
    if not 1<=len(query)<=200:raise ValueError('invalid query')
    result=[]
    for name in list_files():
        try:text=source(name).read_text()
        except (OSError,UnicodeError,ToolError):continue
        for n,line in enumerate(text.splitlines(),1):
            if query.casefold() in line.casefold():
                result.append({'path':name,'line':n,'text':line[:1000]})
                if len(result)>=100:return result
    return result

@mcp.tool()
def propose_file(path:str,content:str) -> dict:
    """Save a file for validation and application to the selected project when enabled; otherwise retain a proposal."""
    if not CONFIG.get('permissions',{}).get('write',False):raise ValueError('write permission denied')
    parts=Path(path).parts
    if not parts or Path(path).is_absolute() or any(p.startswith('.') for p in parts) or len(content.encode())>100000:
        raise ValueError('invalid path or size')
    target=ROOT/path
    if not target.resolve().is_relative_to(ROOT) or any((ROOT/Path(*parts[:i])).is_symlink() for i in range(1,len(parts)+1)):
        raise ValueError('path denied')
    original=source(path).read_text() if path in available() else ''
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content)
    return {'path':path,'diff':''.join(difflib.unified_diff(original.splitlines(True),content.splitlines(True),'a/'+path,'b/'+path)),
            'host_changed':False,'application_pending':CONFIG.get('apply_changes',False)}

@mcp.tool()
async def run_test(command_id:str) -> dict:
    """Run an administratively registered test in a nested sandbox without network, auth files or other projects."""
    if not CONFIG.get('permissions',{}).get('tests',False):raise ValueError('test permission denied')
    command=CONFIG.get('test_commands',{}).get(command_id)
    if not command:raise ValueError('command not authorized; available: '+', '.join(CONFIG.get('test_commands',{})))
    with tempfile.TemporaryDirectory() as tmp:
        total=0
        for name in available():
            if CONFIG.get('test_paths') and not any(name.startswith(p.rstrip('/')+'/') or name==p for p in CONFIG['test_paths']):continue
            src=source(name);total+=src.stat().st_size
            if total>50*1024*1024:raise ValueError('test snapshot limit')
            dst=Path(tmp)/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
        code,out=await process(sandbox(Path(tmp),command,writable=True),60)
    return {'command_id':command_id,'exit_code':code,'output':out,'network':False}

if __name__=='__main__':mcp.run(transport='stdio')
