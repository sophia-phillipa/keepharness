from Adapters.codex.rpc import usage_delta


def test_resumed_usage_excludes_prior_turns():
    before={'inputTokens':36544,'outputTokens':414}
    total={'inputTokens':49019,'outputTokens':444}
    last={'inputTokens':12475,'outputTokens':30}
    assert usage_delta(before,total,last)==last
    assert usage_delta(None,total,last)==last
    assert usage_delta(total,total,last)=={'inputTokens':0,'outputTokens':0}
    assert usage_delta({},before,{})==before
    assert usage_delta(total,last,last)==last  # counter reset / compaction
