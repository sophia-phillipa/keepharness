"""Synthetic fixture contracts and explicitly opt-in paid CLI conformance probes.

Default collection performs only local fixture checks. Set TAIL_HARNESS_LIVE=1
and select a provider/probe with -k to authorize real model calls.
"""

import json
import os
import shutil
from pathlib import Path

import claude_probes
import pytest
from codex_probes import assert_codex_conformance, run_codex_probe
from conformance import FIXTURE_ROOT, isolated_fixture, sanitized_argv

LIVE_ENABLED = os.environ.get("TAIL_HARNESS_LIVE") == "1"
CODEX_CASES = (
    "skills",
    "question",
    "question_plan",
    "question_enabled",
    "agents_symlink_trusted",
    "agents_trusted",
    "prompt",
    "mcp",
    "mcp_timeout",
    "mcp_turn_block",
)


def test_synthetic_fixture_links_external_catalog_and_cleans_up():
    original_skill = (FIXTURE_ROOT / "catalog/skills/demo-native/SKILL.md").read_bytes()
    with isolated_fixture() as fixture:
        root = fixture.root
        links = (
            fixture.project / ".claude/commands/synthetic-probe.md",
            fixture.project / ".claude/agents/demo--writer.md",
            fixture.claude_config_dir / "agents/demo--reviewer.md",
            fixture.claude_config_dir / "skills/demo-native",
            fixture.project / ".claude/rules/synthetic-path-rule.md",
            fixture.project / ".codex/agents/demo--writer.toml",
            fixture.codex_home / "agents/demo--reviewer.toml",
            fixture.codex_home / "skills/demo-native",
            fixture.codex_home / "prompts/synthetic-probe.md",
        )
        for link in links:
            assert link.is_symlink()
            assert link.resolve().exists()
            assert link.resolve().is_relative_to(FIXTURE_ROOT)
            assert not link.resolve().is_relative_to(root)
        command = links[0].read_text()
        assert "$ARGUMENTS" in command and "```bash" in command and '"$HOME"' in command
        assert "paths:" in links[4].read_text()
        for config in (
            fixture.project / ".claude/settings.json",
            fixture.claude_config_dir / "settings.json",
        ):
            assert json.loads(config.read_text())["hooks"]["UserPromptSubmit"]
        assert not (fixture.codex_home / "auth.json").exists()
        assert not (fixture.claude_config_dir / ".credentials.json").exists()
    assert not root.exists()
    assert (FIXTURE_ROOT / "catalog/skills/demo-native/SKILL.md").read_bytes() == original_skill


def test_synthetic_fixture_strips_parent_secrets_and_routing(monkeypatch):
    names = (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "TAIL_HARNESS_TOKEN",
        "OPENAI_BASE_URL",
        "ANTHROPIC_BASE_URL",
        "CLAUDECODE",
        "CODEX_THREAD_ID",
        "BASH_ENV",
        "PYTHONPATH",
    )
    for name in names:
        monkeypatch.setenv(name, "synthetic-canary")
    with isolated_fixture() as fixture:
        assert all(name not in fixture.env for name in names)
        assert fixture.env["HOME"] == str(fixture.home)
        assert fixture.env["CLAUDE_CONFIG_DIR"] == str(fixture.claude_config_dir)
        assert fixture.env["CODEX_HOME"] == str(fixture.codex_home)
        assert all(os.environ[name] == "synthetic-canary" for name in names)


def test_synthetic_fixture_copies_only_explicit_auth_with_private_permissions(
    tmp_path, monkeypatch
):
    fake_original = tmp_path / "original-home"
    claude = fake_original / ".claude"
    codex = fake_original / ".codex"
    claude.mkdir(parents=True)
    codex.mkdir()
    originals = {
        claude / ".credentials.json": '{"synthetic_auth":"claude-canary"}',
        codex / "auth.json": '{"synthetic_auth":"codex-canary"}',
        claude / "settings.json": '{"never_copy":"user-settings"}',
        codex / "config.toml": 'never_copy = "user-config"',
    }
    for path, content in originals.items():
        path.write_text(content)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_original))
    with isolated_fixture(copy_claude_auth=True, copy_codex_auth=True) as fixture:
        for source, destination in (
            (claude / ".credentials.json", fixture.claude_config_dir / ".credentials.json"),
            (codex / "auth.json", fixture.codex_home / "auth.json"),
        ):
            assert destination.read_bytes() == source.read_bytes()
            assert destination.stat().st_mode & 0o777 == 0o600
            assert not destination.is_symlink()
        assert "never_copy" not in (fixture.claude_config_dir / "settings.json").read_text()
        assert not (fixture.codex_home / "config.toml").exists()
    assert all(path.read_text() == content for path, content in originals.items())


def test_evidence_argv_redacts_inline_and_separate_secrets():
    args = [
        "claude",
        "--token",
        "canary-a",
        "--api-key=canary-b",
        "--settings",
        '{"api_key":"canary-c"}',
    ]
    sanitized = json.dumps(sanitized_argv(args))
    assert not any(canary in sanitized for canary in ("canary-a", "canary-b", "canary-c"))
    assert "[REDACTED]" in sanitized


def test_live_entrypoints_refuse_unapproved_execution(monkeypatch):
    monkeypatch.delenv("TAIL_HARNESS_LIVE", raising=False)
    for run, name in ((claude_probes.run_claude_probe, "command"), (run_codex_probe, "skills")):
        with pytest.raises(RuntimeError, match="TAIL_HARNESS_LIVE=1"):
            run(name)


@pytest.mark.live
@pytest.mark.skipif(not LIVE_ENABLED, reason="paid probes: set TAIL_HARNESS_LIVE=1")
@pytest.mark.parametrize("name", claude_probes.PROBES)
def test_claude_conformance(name):
    if not shutil.which("claude"):
        pytest.skip("claude is not installed")
    report = claude_probes.run_claude_probe(name)
    claude_probes.assert_claude_conformance(report)


@pytest.mark.live
@pytest.mark.skipif(not LIVE_ENABLED, reason="paid probes: set TAIL_HARNESS_LIVE=1")
@pytest.mark.parametrize("name", CODEX_CASES)
def test_codex_conformance(name):
    if not shutil.which("codex"):
        pytest.skip("codex is not installed")
    report = run_codex_probe(name)
    assert_codex_conformance(report)
