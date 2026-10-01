import pytest

from agent_service.invocations import normalize_chips
from agent_service.resources import MAX_METADATA_BYTES, markdown, unfenced


def test_tab_separated_list_fence_inside_quote_is_not_an_invocation():
    prompt = "> -\t```text\n>    /review example\n>    ```\n/review actual"
    item = {
        "id": "review",
        "resource_id": "project/p/.codex/agents/review.toml",
        "kind": "agent",
        "mode": "delegated",
    }
    masked = unfenced(prompt, preserve_offsets=True)
    invocation = normalize_chips(prompt, [{"id": "review", "token": "/review"}], [item])[0]
    assert "/review example" not in masked
    assert invocation.args == "actual"


def test_crlf_frontmatter_obeys_raw_metadata_byte_limit():
    text = "---\r\ndescription: x\r\n" + " a\r\n" * 17_000 + "---\r\nbody"
    closing = text.index("---\r\n", 3) + len("---\r\n")
    assert closing > MAX_METADATA_BYTES
    with pytest.raises(ValueError, match="metadata_too_large"):
        markdown(text)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("extra", [0, 1])
def test_frontmatter_exact_raw_limit(newline, extra):
    prefix = "---" + newline + "description: "
    suffix = newline + "---" + newline
    text = prefix + "x" * (MAX_METADATA_BYTES - len((prefix + suffix).encode()) + extra) + suffix
    assert len(text.encode()) == MAX_METADATA_BYTES + extra
    if extra:
        with pytest.raises(ValueError, match="metadata_too_large"):
            markdown(text + "body")
    else:
        meta, body = markdown(text + "body")
        assert meta["description"] and body == "body"
