"""control.env: KEEPHARNESS_<NAME> wins; legacy aliases work with one deprecation warning."""

import logging
import os

import pytest

from control import env


def test_new_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("KEEPHARNESS_AGENT_URL", "https://new")
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    assert env.read("AGENT_URL") == "https://new"


def test_legacy_alias_is_used_with_a_deprecation_warning(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_AGENT_URL", raising=False)
    monkeypatch.setenv("LOCAL_AGENT_URL", "https://legacy")
    env._WARNED.discard("AGENT_URL")
    with pytest.warns(DeprecationWarning, match="LOCAL_AGENT_URL"):
        assert env.read("AGENT_URL") == "https://legacy"


def test_th_venv_is_the_legacy_alias_for_keepharness_venv(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_VENV", raising=False)
    monkeypatch.setenv("TH_VENV", "/opt/venv")
    env._WARNED.discard("VENV")
    with pytest.warns(DeprecationWarning, match="TH_VENV"):
        assert env.read("VENV") == "/opt/venv"


def test_default_is_returned_when_neither_name_is_set(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_AGENT_CLIENT", raising=False)
    monkeypatch.delenv("LOCAL_AGENT_CLIENT", raising=False)
    assert env.read("AGENT_CLIENT", "fallback") == "fallback"
    assert env.read("AGENT_CLIENT") is None


def test_unmapped_name_has_no_legacy_fallback(monkeypatch):
    monkeypatch.delenv("KEEPHARNESS_LOG_LEVEL", raising=False)
    assert env.read("LOG_LEVEL", "info") == "info"


def test_each_tail_harness_variable_left_set_is_named_once_at_startup(monkeypatch, caplog):
    for name in [name for name in os.environ if name.startswith("TAIL_HARNESS_")]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("TAIL_HARNESS_LOG_LEVEL", "debug")
    monkeypatch.setenv("TAIL_HARNESS_AGENT_URL", "https://private.example")
    with caplog.at_level(logging.WARNING, logger="control.env"):
        env.warn_legacy_names()
    assert [record.getMessage() for record in caplog.records] == [
        "TAIL_HARNESS_AGENT_URL is no longer read; rename it to KEEPHARNESS_AGENT_URL.",
        "TAIL_HARNESS_LOG_LEVEL is no longer read; rename it to KEEPHARNESS_LOG_LEVEL.",
    ]
    assert "private.example" not in caplog.text
