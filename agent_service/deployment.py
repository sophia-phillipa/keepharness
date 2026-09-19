"""Apply authorized text edits with conflict detection, syntax checks and durable backups."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from .tools import ToolError


def target(root, name):
    parts=Path(name).parts
    if len(parts)<2 or parts[0]!='project' or any(p.startswith('.') for p in parts) or Path(name).is_absolute():
        raise ToolError('deployment_path_denied')
    dest=root.joinpath(*parts[1:])
    if any(root.joinpath(*parts[1:i]).is_symlink() for i in range(2,len(parts)+1)) or not dest.resolve().is_relative_to(root):
        raise ToolError('deployment_path_denied')
    return dest


def snapshot(root):
    root=Path(root).resolve()
    names=(subprocess.check_output(['git','-C',str(root),'ls-files','--cached','--others','--exclude-standard','-z']).decode().split('\0')
           if (root/'.git').exists() else [str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()])
    result={}
    for name in names:
        if not name:continue
        try:p=target(root,'project/'+name)
        except ToolError:continue
        if p.is_file():result['project/'+name]=hashlib.sha256(p.read_bytes()).hexdigest()
    return result


def apply(project, files, baseline, backup_dir):
    root=Path(project['root']).resolve();pending=[]
    for name,content in files.items():
        dest=target(root,name)
        before=dest.read_bytes() if dest.exists() else None
        after=content.encode()
        if before==after:continue
        digest=hashlib.sha256(before).hexdigest() if before is not None else None
        if digest!=baseline.get(name):raise ToolError('deployment_conflict: '+name)
        if len(after)>100000:raise ToolError('deployment_file_limit')
        if dest.suffix=='.py':
            try:ast.parse(content,filename=name)
            except SyntaxError:raise ToolError('deployment_python_syntax: '+name)
        if dest.suffix=='.js':
            with tempfile.NamedTemporaryFile(suffix='.js') as tmp:
                tmp.write(after);tmp.flush()
                check=subprocess.run([project.get('node_binary','node'),'--check',tmp.name],capture_output=True,timeout=15)
                if check.returncode:raise ToolError('deployment_javascript_syntax: '+name)
        pending.append((name,dest,before,after))
    backup=Path(backup_dir);backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    manifest=[]
    for name,dest,before,after in pending:
        saved=backup/name;saved.parent.mkdir(parents=True,exist_ok=True)
        if before is not None:saved.write_bytes(before)
        manifest.append({'path':name,'existed':before is not None,'sha256':hashlib.sha256(after).hexdigest()})
    (backup/'manifest.json').write_text(json.dumps(manifest,indent=2))
    written=[]
    try:
        for name,dest,before,after in pending:
            dest.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as tmp:
                tmp.write(after);tmp_path=Path(tmp.name)
            tmp_path.chmod(dest.stat().st_mode & 0o777 if dest.exists() else 0o644)
            tmp_path.replace(dest);written.append((dest,before))
    except BaseException:
        for dest,before in reversed(written):
            if before is None:dest.unlink(missing_ok=True)
            else:dest.write_bytes(before)
        raise
    return {'applied':bool(pending),'files':[p[0] for p in pending],
            'validation':'Python AST and JavaScript syntax; functional tests are reported separately',
            'backup':str(backup),'restart_required':bool(pending) and bool(project.get('restart_service'))}
