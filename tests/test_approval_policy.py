from agent_service.approval_policy import MODES, effective_permissions, full_approval_allowed, rule_key

def test_full_is_valid_but_does_not_expand_grants():
    original={'read':True,'write':False,'shell':False,'internet':False}
    assert 'full' in MODES
    assert effective_permissions(original,'full')==original

def test_full_only_auto_approves_actions_inside_existing_grants():
    permissions={'read':True,'write':False,'shell':True,'internet':False}
    assert full_approval_allowed('item/commandExecution/requestApproval',{},permissions)
    assert not full_approval_allowed('applyPatchApproval',{},permissions)
    assert not full_approval_allowed('permissions/requestApproval',{'permissions':{'network':True}},permissions)
    assert full_approval_allowed('claude/can_use_tool',{'tool_name':'Read'},permissions)
    assert not full_approval_allowed('claude/can_use_tool',{'tool_name':'WebFetch'},permissions)

def test_read_only_cannot_expand_admin_grants():
    original={'read':True,'write':True,'shell':True,'internet':False,'hooks':True}
    actual=effective_permissions(original,'read_only')
    assert actual=={'read':True,'write':False,'shell':False,'internet':False,'hooks':False,'tests':False}
    assert original['write'] is True

def test_remembered_command_is_exact_and_permission_bound():
    kind='item/commandExecution/requestApproval';request={'command':'curl https://example.org','cwd':'/project'}
    key=rule_key(kind,request,{'internet':True})
    assert key==rule_key(kind,{**request,'threadId':'different'}, {'internet':True})
    assert key!=rule_key(kind,{**request,'command':'curl https://other.org'}, {'internet':True})
    assert key!=rule_key(kind,{**request,'cwd':'/other'}, {'internet':True})
    assert key!=rule_key(kind,request,{'internet':False})
    assert rule_key('item/tool/requestUserInput',request,{}) is None
