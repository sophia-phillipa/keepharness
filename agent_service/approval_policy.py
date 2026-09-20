"""Persistent approvals are exact actions scoped to identity, conversation and grants."""
import hashlib
import json

MODES={'ask','auto','full','read_only'}

def rule_key(kind, request, permissions):
    if 'commandExecution/requestApproval' not in kind and kind!='execCommandApproval':return None
    command=request.get('command')
    if not isinstance(command,str) or not command:return None
    value={'kind':kind,'command':command,'cwd':request.get('cwd'),'permissions':permissions}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def effective_permissions(permissions, mode):
    result=dict(permissions)
    if mode=='read_only':
        for key in ('write','shell','hooks','tests'):result[key]=False
    return result

def full_approval_allowed(kind, request, permissions):
    """Approve only native actions already enabled by the configured grants."""
    if 'permissions/requestApproval' in kind:return False
    if 'commandExecution/requestApproval' in kind or kind=='execCommandApproval':return bool(permissions.get('shell'))
    if kind=='applyPatchApproval':return bool(permissions.get('write'))
    if not kind.startswith('claude/'):return False
    tool=request.get('tool_name')
    if tool in ('Read','Glob','Grep'):return bool(permissions.get('read'))
    if tool in ('Edit','Write','NotebookEdit'):return bool(permissions.get('write'))
    if tool=='Bash':return bool(permissions.get('shell'))
    if tool in ('WebFetch','WebSearch'):return bool(permissions.get('internet'))
    return False
