"""control.env: TAIL_HARNESS_<NAME> wins; legacy aliases work with one deprecation warning."""

import pytest

from control import env


def test_new_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("TAIL_HARNESS_AGENT_URL", "https://new")
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    assert env.read("AGENT_URL") == "https://new"


def test_legacy_alias_is_used_with_a_deprecation_warning(monkeypatch):
    monkeypatch.delenv("TAIL_HARNESS_AGENT_URL", raising=False)
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    env._WARNED.discard("AGENT_URL")
    with pytest.warns(DeprecationWarning, match="LOCAL_AGENT_URL"):
        assert env.read("AGENT_URL") == "https://legacy"


def test_th_venv_is_the_legacy_alias_for_tail_harness_venv(monkeypatch):
    monkeypatch.delenv("TAIL_HARNESS_VENV", raising=False)
    monkeypatch.setenv("TH_VENV", "/opt/venv")
    env._WARNED.discard("VENV")
    with pytest.warns(DeprecationWarning, match="TH_VENV"):
        assert env.read("VENV") == "/opt/venv"


def test_default_is_returned_when_neither_name_is_set(monkeypatch):
    monkeypatch.delenv("TAIL_HARNESS_AGENT_CLIENT", raising=False)
    monkeypatch.delenv("LOCAL_AGENT_CLIENT", raising=False)
    assert env.read("AGENT_CLIENT", "fallback") == "fallback"
    assert env.read("AGENT_CLIENT") is None


def test_unmapped_name_has_no_legacy_fallback(monkeypatch):
    monkeypatch.delenv("TAIL_HARNESS_LOG_LEVEL", raising=False)
    assert env.read("LOG_LEVEL", "info") == "info"
