import pytest

from agent_service.invocations import Invocation, InvocationError, validate_chain


def test_invocation_preserves_arguments_and_portable_identity():
    value = Invocation(kind="command", resource_id="catalog/demo/commands/review.md", args='  "two words"\n$VAR  ', order=0, mode="inline")
    assert value.to_dict()["args"] == '  "two words"\n$VAR  '
    assert value.resource_id == "catalog/demo/commands/review.md"


@pytest.mark.parametrize("resource_id", ["/tmp/private.md", "catalog/../secret", "C:\\private", "", "catalog//bad"])
def test_rejects_nonportable_identity(resource_id):
    with pytest.raises(InvocationError):
        Invocation(kind="agent", resource_id=resource_id)


@pytest.mark.parametrize("fields", [{"kind": "rule"}, {"mode": "background"}, {"order": -1}, {"order": True}, {"args": []}])
def test_rejects_invalid_contract_fields(fields):
    with pytest.raises(InvocationError):
        Invocation(**{"kind": "agent", "resource_id": "project/agents/reviewer.md", **fields})


def test_conversational_role_is_standalone_only():
    role = Invocation(kind="agent", resource_id="project/agents/discussion.md", mode="conversational")
    assert validate_chain([role]) == [role]
    with pytest.raises(InvocationError, match="conversational_chain_unsupported"):
        validate_chain([role, Invocation(kind="command", resource_id="project/commands/check.md", order=1)])
